import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import pytest

import docich.corner_improve as corner_improve
from docich.corner_improve import (
    CornerImproveError,
    build_prompt,
    parse_candidate,
    run_corner_improve,
    slice_corner_matches,
    summarize_matches,
)
from docich.resolver.bot_eval import bot_default_weights
from docich.resolver import gnurobots as gnurobots_resolver


def _weights(**over):
    base = dict(gnurobots_resolver.DEFAULT_STRATEGY)
    base.update(over)
    return base


def test_slice_filters_game_window_and_corrupt_lines(tmp_path):
    log = tmp_path / 'gnurobots.jsonl'
    log.write_text('\n'.join([
        '{"ts":"100","game":"gnurobots","score":10,"source":"wrapper"}',
        '{"ts":"200","game":"gnurobots","score":20,"source":"wrapper"}',
        '{"ts":"300","game":"gnurobots","score":30,"source":"wrapper"}',
        '{"ts":"200","game":"robots","score":99,"source":"x"}',
        'not json',
        '{"ts":"nan","game":"gnurobots","score":5}',
    ]), encoding='utf-8')
    got = slice_corner_matches(log, 'gnurobots', 150.0, 250.0)
    assert [m['score'] for m in got] == [20]
    assert slice_corner_matches(tmp_path / 'missing.jsonl', 'gnurobots', 0, 999) == []


def test_summarize(tmp_path):
    assert summarize_matches([]) == {'n': 0, 'mean': 0.0, 'best': 0}
    assert summarize_matches([{'score': 10}, {'score': 30}]) == {'n': 2, 'mean': 20.0, 'best': 30}


def test_parse_candidate_fenced_and_raw():
    keys = set(_weights())
    key = sorted(keys)[0]
    assert parse_candidate(f'```json\n{{"{key}": 2.5}}\n```', keys) == {key: 2.5}
    assert parse_candidate(f'{{"{key}": 3}}', keys) == {key: 3}
    with pytest.raises(CornerImproveError):
        parse_candidate('```json\n{"nope": 1}\n```', keys)
    with pytest.raises(CornerImproveError):
        parse_candidate(f'{{"{key}": "x"}}', keys)
    with pytest.raises(CornerImproveError):
        parse_candidate('hello', keys)


def test_build_prompt_contains_stats_and_weights():
    prompt = build_prompt(game='gnurobots', stats={'n': 5, 'mean': 100.0, 'best': 200},
                          current=_weights(), previous={})
    assert 'gnurobots' in prompt and '5試合' in prompt and '```json' in prompt


class _G:
    def __init__(self, state_dir):
        self.state_dir = state_dir


def _setup_completed(tmp_path, scores):
    state_dir = tmp_path / 'run'
    (state_dir / 'scores').mkdir(parents=True)
    log = state_dir / 'scores' / 'gnurobots.jsonl'
    base = 1789034400  # 2026-09-10T19:00:00+09:00
    log.write_text('\n'.join(
        json.dumps({'ts': str(base + i * 60), 'game': 'gnurobots', 'score': s, 'source': 'wrapper'})
        for i, s in enumerate(scores)
    ) + '\n', encoding='utf-8')
    started = '2026-09-10T19:00:00+09:00'
    ends = '2026-09-10T19:30:00+09:00'
    (state_dir / 'retro_corner.json').write_text(json.dumps({
        'schema_version': 1, 'status': 'completed', 'date': '2026-09-10',
        'game': 'gnurobots', 'previous_game': 'sorengame',
        'started_at': started, 'ends_at': ends, 'completed_at': ends,
    }), encoding='utf-8')
    return state_dir


def _patch_resolver_path(monkeypatch, tmp_path):
    import docich.resolver.improve as improve
    monkeypatch.setattr(improve, 'GNUROBOTS_RESOLVER', str(tmp_path / 'resolver.scm'))


def test_skips(tmp_path):
    state_dir = _setup_completed(tmp_path, [10, 20])
    g = _G(state_dir)
    assert run_corner_improve(g, game='gnurobots', date_str='2026-09-11',
                              agents='a')['status'] == 'skipped'
    assert run_corner_improve(g, game='robots', date_str='2026-09-10',
                              agents='a')['status'] == 'skipped'
    assert run_corner_improve(g, game='gnurobots', date_str='2026-09-10',
                              agents='a', dry_run=True)['status'] == 'dry-run'


def test_no_matches_skipped(tmp_path):
    state_dir = _setup_completed(tmp_path, [])
    g = _G(state_dir)
    result = run_corner_improve(g, game='gnurobots', date_str='2026-09-10', agents='a')
    assert result['status'] == 'skipped' and result['reason'] == 'no-matches'


def test_promote_and_keep_paths(tmp_path, monkeypatch):
    _patch_resolver_path(monkeypatch, tmp_path)
    key = sorted(_weights())[0]
    prompts = []
    llm = lambda prompt: (prompts.append(prompt) or f'```json\n{{"{key}": 2.5}}\n```')

    state_dir = _setup_completed(tmp_path, [10, 20])
    g = _G(state_dir)
    (state_dir / 'resolver').mkdir(parents=True, exist_ok=True)
    (state_dir / 'resolver' / 'gnurobots_strategy.json').write_text(
        json.dumps(_weights()), encoding='utf-8')
    history = state_dir / 'resolver' / 'history'
    history.mkdir(parents=True, exist_ok=True)
    (history / '20990101.json').write_text(json.dumps(_weights()), encoding='utf-8')
    other_game_history = history / 'bastet'
    other_game_history.mkdir()
    (other_game_history / '20990102.json').write_text(json.dumps(_weights()), encoding='utf-8')

    def better_candidate(strat):
        mean = 120.0 if strat.get(key) == 2.5 else 100.0
        return {'mean_score': mean, 'played': 2}

    result = run_corner_improve(
        g, game='gnurobots', date_str='2026-09-10', agents='a',
        evaluator=better_candidate,
        llm=llm,
    )
    assert result['status'] == 'promoted'
    assert result['baseline_mean'] == 100.0
    assert result['candidate_mean'] == 120.0
    assert '前回の戦略重み (JSON):\n{}' in prompts[0]
    strategy = json.loads((state_dir / 'resolver' / 'gnurobots_strategy.json').read_text())
    assert strategy[key] == 2.5
    assert list((history / 'gnurobots').glob('*.json'))
    assert (tmp_path / 'resolver.scm').exists()
    assert (state_dir / 'resolver' / 'improve_log.jsonl').exists()

    state_dir2 = _setup_completed(tmp_path / 'run2p', [10, 20])
    g2 = _G(state_dir2)

    def insufficient_candidate(strat):
        mean = 105.0 if strat.get(key) == 2.5 else 100.0
        return {'mean_score': mean, 'played': 2}

    result2 = run_corner_improve(
        g2, game='gnurobots', date_str='2026-09-10', agents='a',
        evaluator=insufficient_candidate,
        llm=llm,
    )
    assert result2['status'] == 'kept'
    assert result2['corner_mean'] == 15.0
    assert result2['baseline_mean'] == 100.0
    assert result2['candidate_mean'] == 105.0
    assert not (state_dir2 / 'resolver' / 'gnurobots_strategy.json').exists()


def test_bad_llm_output_raises(tmp_path):
    state_dir = _setup_completed(tmp_path, [10, 20])
    g = _G(state_dir)
    with pytest.raises(CornerImproveError):
        run_corner_improve(g, game='gnurobots', date_str='2026-09-10', agents='a',
                           llm=lambda prompt: 'not json',
                           evaluator=lambda strat: {'mean_score': 1.0, 'played': 2})
    record = json.loads((state_dir / 'corner_improve_gnurobots.json').read_text())
    assert record['status'] == 'failed'
    assert record['reason_code'] == 'llm-format'
    assert record['phase'] == 'llm'
    assert 'not json' not in json.dumps(record)


def test_eval_failure_records_reason_code(tmp_path):
    state_dir = _setup_completed(tmp_path, [10, 20])
    g = _G(state_dir)
    key = sorted(_weights())[0]

    def failing_evaluator(strat):
        raise RuntimeError('sensitive evaluator detail')

    with pytest.raises(CornerImproveError):
        run_corner_improve(g, game='gnurobots', date_str='2026-09-10', agents='a',
                           llm=lambda prompt: f'```json\n{{"{key}": 2.5}}\n```',
                           evaluator=failing_evaluator)
    record = json.loads((state_dir / 'corner_improve_gnurobots.json').read_text())
    assert record['status'] == 'failed'
    assert record['reason_code'] == 'eval'
    assert record['phase'] == 'eval'
    assert 'sensitive' not in json.dumps(record)


def test_gate_disabled_records_reason_code(tmp_path, monkeypatch):
    monkeypatch.delenv('DOCICH_ALLOW_REAL_AI', raising=False)
    state_dir = _setup_completed(tmp_path, [10, 20])
    g = _G(state_dir)
    with pytest.raises(CornerImproveError):
        run_corner_improve(g, game='gnurobots', date_str='2026-09-10', agents='a')
    record = json.loads((state_dir / 'corner_improve_gnurobots.json').read_text())
    assert record['status'] == 'failed'
    assert record['reason_code'] == 'gate-disabled'
    assert record['phase'] == 'llm'


def test_untrusted_failure_metadata_is_clamped_before_persist(tmp_path):
    state_dir = _setup_completed(tmp_path, [10, 20])
    g = _G(state_dir)

    def hostile_llm(_prompt):
        raise CornerImproveError(
            'sensitive exception body',
            code={'secret-code': 'must-not-persist'},
            phase=['secret-phase'],
        )

    with pytest.raises(CornerImproveError):
        run_corner_improve(
            g, game='gnurobots', date_str='2026-09-10', agents='a',
            llm=hostile_llm,
            evaluator=lambda strat: {'mean_score': 1.0, 'played': 2},
        )
    record = json.loads((state_dir / 'corner_improve_gnurobots.json').read_text())
    assert record['status'] == 'failed'
    assert record['reason_code'] == 'unexpected'
    assert record['phase'] == 'unknown'
    assert 'secret' not in json.dumps(record)


def test_parse_candidate_attaches_fixed_codes():
    keys = set(_weights())
    key = sorted(keys)[0]
    for payload, code in (
        ('not json', 'llm-format'),
        ('{}', 'llm-format'),
        ('{"unknown-key": 1}', 'llm-keys'),
        (f'{{"{key}": true}}', 'llm-values'),
        (f'{{"{key}": 0.0009}}', 'llm-values'),
    ):
        with pytest.raises(CornerImproveError) as excinfo:
            parse_candidate(payload, keys)
        assert excinfo.value.code == code
        assert excinfo.value.phase == 'llm'


def test_prompt_shows_only_proposable_numeric_weights(tmp_path):
    state_dir = tmp_path / 'run'
    (state_dir / 'scores').mkdir(parents=True)
    base = 1789034400  # 2026-09-10T19:00:00+09:00
    (state_dir / 'scores' / 'nsnake.jsonl').write_text(
        json.dumps({'ts': str(base + 60), 'game': 'nsnake', 'score': 10, 'source': 'wrapper'}) + '\n',
        encoding='utf-8')
    (state_dir / 'retro_corner.json').write_text(json.dumps({
        'schema_version': 1, 'status': 'completed', 'date': '2026-09-10', 'game': 'nsnake',
        'previous_game': 'sorengame',
        'started_at': '2026-09-10T19:00:00+09:00',
        'ends_at': '2026-09-10T19:30:00+09:00',
        'completed_at': '2026-09-10T19:30:00+09:00',
    }), encoding='utf-8')
    g = _G(state_dir)
    prompts = []

    def llm(prompt):
        prompts.append(prompt)
        return '```json\n{"min_free": 9}\n```'

    result = run_corner_improve(
        g, game='nsnake', date_str='2026-09-10', agents='a',
        llm=llm, evaluator=lambda strat: {'mean_score': 100.0, 'played': 2},
    )
    assert result['status'] == 'kept'
    assert '"min_free"' in prompts[0]
    # nsnake's fixed boolean flag must not be presented as a tunable weight;
    # the model returned it once and the strict parser failed the whole job.
    assert 'tail_passable' not in prompts[0]


def test_parse_candidate_boundaries():
    keys = set(_weights())
    key = sorted(keys)[0]
    assert parse_candidate(f'{{"{key}": 0.001}}', keys) == {key: 0.001}
    assert parse_candidate(f'{{"{key}": 1000000.0}}', keys) == {key: 1000000.0}
    with pytest.raises(CornerImproveError):
        parse_candidate(f'{{"{key}": true}}', keys)
    with pytest.raises(CornerImproveError):
        parse_candidate(f'{{"{key}": 0.0009}}', keys)
    with pytest.raises(CornerImproveError):
        parse_candidate(f'{{"{key}": NaN}}', keys)
    with pytest.raises(CornerImproveError):
        parse_candidate(f'{{"{key}": Infinity}}', keys)
    try:
        parse_candidate('{"k1":1,"k2":2,"k3":3,"k4":4,"k5":5,"k6":6}', keys)
        raise AssertionError('must reject unknown keys')
    except CornerImproveError as exc:
        assert 'k6' not in str(exc)



def test_parse_candidate_allows_zero_only_when_game_policy_allows_it():
    assert parse_candidate('{"hard_drop": 0}', {"hard_drop"}, minimum=0.0) == {
        "hard_drop": 0,
    }
    with pytest.raises(CornerImproveError) as default_excinfo:
        parse_candidate('{"hard_drop": 0}', {"hard_drop"})
    assert default_excinfo.value.code == 'llm-values'
    with pytest.raises(CornerImproveError) as excinfo:
        parse_candidate('{"hard_drop": -0.001}', {"hard_drop"}, minimum=0.0)
    assert excinfo.value.code == 'llm-values'


def test_already_running_is_skipped(tmp_path):
    import fcntl

    state_dir = _setup_completed(tmp_path, [10, 20])
    lock = state_dir / 'locks' / 'corner-improve-gnurobots.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    held = lock.open('a+')
    fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        result = run_corner_improve(_G(state_dir), game='gnurobots',
                                    date_str='2026-09-10', agents='a')
    finally:
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)
        held.close()
    assert result['status'] == 'skipped' and result['reason'] == 'already-running'


def test_improve_lane_serializes_and_reports_busy(tmp_path):
    from docich.corner_improve import improve_lane, improve_lane_free

    state_dir = tmp_path / "run"
    assert improve_lane_free(state_dir) is True
    with improve_lane(state_dir) as first:
        assert first is True
        assert improve_lane_free(state_dir) is False
        with improve_lane(state_dir, timeout=0.05, sleep=lambda _seconds: None) as second:
            assert second is False
    assert improve_lane_free(state_dir) is True


def test_lane_busy_skip_is_recorded(tmp_path, monkeypatch):
    import json as _json
    from contextlib import contextmanager
    from docich import corner_improve

    @contextmanager
    def busy(_state_dir, **_kwargs):
        yield False

    monkeypatch.setattr(corner_improve, "improve_lane", busy)
    state_dir = _setup_completed(tmp_path, [10, 20])
    result = run_corner_improve(_G(state_dir), game='gnurobots',
                                date_str='2026-09-10', agents='a')

    assert result['status'] == 'skipped' and result['reason'] == 'lane-busy'
    status = _json.loads((state_dir / 'corner_improve_gnurobots.json').read_text())
    assert status['status'] == 'skipped'
    assert status['reason_code'] == 'lane-busy'


def test_explicit_window_ignores_a_shared_state_owned_by_the_next_corner(tmp_path):
    import datetime as dt

    state_dir = tmp_path / 'run'
    (state_dir / 'scores').mkdir(parents=True)
    start = dt.datetime.fromisoformat('2026-09-10T19:00:00+09:00').timestamp()
    (state_dir / 'scores' / 'gnurobots.jsonl').write_text(
        json.dumps({'ts': str(start + 60), 'game': 'gnurobots', 'score': 42}) + '\n',
        encoding='utf-8',
    )
    # The next queued corner already owns the shared state file.
    (state_dir / 'retro_corner.json').write_text(json.dumps({
        'status': 'active', 'game': 'moon-buggy', 'date': '2026-09-11',
        'started_at': '2026-09-11T00:00:00+09:00',
        'ends_at': '2026-09-11T00:20:00+09:00',
    }), encoding='utf-8')

    result = run_corner_improve(
        _G(state_dir), game='gnurobots', date_str='2026-09-10', agents='a',
        dry_run=True, window=(start, start + 1800),
    )

    assert result['status'] == 'dry-run'
    assert result['stats']['n'] == 1 and result['stats']['best'] == 42


def _setup_completed_pacman(tmp_path):
    state_dir = tmp_path / 'pacman-run'
    (state_dir / 'scores').mkdir(parents=True)
    base = 1789034400
    (state_dir / 'scores' / 'pacman4console.jsonl').write_text('\n'.join(
        json.dumps({'ts': str(base + i * 60), 'game': 'pacman4console',
                    'score': score, 'source': 'wrapper'})
        for i, score in enumerate([100, 200])
    ) + '\n', encoding='utf-8')
    (state_dir / 'retro_corner.json').write_text(json.dumps({
        'schema_version': 1, 'status': 'completed', 'date': '2026-09-10',
        'game': 'pacman4console', 'previous_game': 'sorengame',
        'started_at': '2026-09-10T19:00:00+09:00',
        'ends_at': '2026-09-10T19:30:00+09:00',
        'completed_at': '2026-09-10T19:30:00+09:00',
    }), encoding='utf-8')
    return state_dir


def _pacman_candidate():
    defaults = bot_default_weights('pacman4console')
    key = sorted(corner_improve.numeric_weights(defaults))[0]
    baseline_value = defaults[key]
    candidate_value = min(float(baseline_value) + 1.0, 1e6)
    if candidate_value == baseline_value:
        candidate_value = max(float(baseline_value) - 1.0, 0.001)
    return defaults, key, candidate_value


def _stage_pacman_candidate(state_dir, key, candidate_value):
    return run_corner_improve(
        _G(state_dir), game='pacman4console', date_str='2026-09-10', agents='a',
        llm=lambda _prompt: json.dumps({key: candidate_value}),
        evaluator=lambda _strategy: (_ for _ in ()).throw(
            AssertionError('candidate staging must not run the evaluator')
        ),
    )


def test_pacman_candidate_is_staged_then_promoted_by_abba_score(tmp_path, monkeypatch):
    state_dir = _setup_completed_pacman(tmp_path)
    defaults, key, candidate_value = _pacman_candidate()
    monkeypatch.setenv('DOCICH_BOT_BRAIN_DIR', str(tmp_path / 'live-brain'))

    staged = _stage_pacman_candidate(state_dir, key, candidate_value)
    assert staged['status'] == 'kept'
    assert staged['reason_code'] == 'ab-pending'
    trial_path = state_dir / 'resolver' / 'pacman4console_ab_trial.json'
    trial = json.loads(trial_path.read_text(encoding='utf-8'))
    assert trial['status'] == 'pending'
    assert trial['baseline'] == defaults
    assert trial['candidate'][key] == candidate_value

    played_strategies = []

    def evaluator(strategy):
        played_strategies.append(strategy[key])
        return {'mean_score': 100 if strategy[key] == candidate_value else 50, 'played': 1}

    result = run_corner_improve(
        _G(state_dir), game='pacman4console', date_str='2026-09-10', agents='a',
        evaluator=evaluator,
    )

    assert played_strategies == [defaults[key], candidate_value, candidate_value, defaults[key]]
    assert result['status'] == 'promoted'
    assert result['reason_code'] == 'ab-adopted'
    assert result['baseline_mean'] == 50
    assert result['candidate_mean'] == 100
    assert not trial_path.exists()
    promoted = json.loads((state_dir / 'resolver' / 'pacman4console_strategy.json').read_text())
    live = json.loads((tmp_path / 'live-brain' / 'pacman4console' / 'weights.json').read_text())
    assert promoted == live == trial['candidate']


def test_pacman_incomplete_ab_retries_and_tie_keeps_baseline(tmp_path, monkeypatch):
    state_dir = _setup_completed_pacman(tmp_path)
    defaults, key, candidate_value = _pacman_candidate()
    monkeypatch.setenv('DOCICH_BOT_BRAIN_DIR', str(tmp_path / 'live-brain'))
    staged = _stage_pacman_candidate(state_dir, key, candidate_value)
    assert staged['reason_code'] == 'ab-pending'

    calls = []

    def incomplete_once(strategy):
        calls.append(strategy[key])
        return {'mean_score': 0, 'played': 0}

    incomplete = run_corner_improve(
        _G(state_dir), game='pacman4console', date_str='2026-09-10', agents='a',
        evaluator=incomplete_once,
    )
    trial_path = state_dir / 'resolver' / 'pacman4console_ab_trial.json'
    assert incomplete['reason_code'] == 'ab-incomplete'
    assert calls == [defaults[key]]
    assert json.loads(trial_path.read_text())['status'] == 'pending'

    equal = run_corner_improve(
        _G(state_dir), game='pacman4console', date_str='2026-09-10', agents='a',
        evaluator=lambda _strategy: {'mean_score': 75, 'played': 1},
    )
    assert equal['status'] == 'kept'
    assert equal['reason_code'] == 'ab-rejected'
    assert not trial_path.exists()
    assert not (state_dir / 'resolver' / 'pacman4console_strategy.json').exists()


def test_pacman_ab_discards_candidate_when_baseline_changes(tmp_path):
    state_dir = _setup_completed_pacman(tmp_path)
    defaults, key, candidate_value = _pacman_candidate()
    _stage_pacman_candidate(state_dir, key, candidate_value)
    changed = dict(defaults)
    changed[key] = candidate_value
    strategy_file = state_dir / 'resolver' / 'pacman4console_strategy.json'
    strategy_file.write_text(json.dumps(changed), encoding='utf-8')

    result = run_corner_improve(
        _G(state_dir), game='pacman4console', date_str='2026-09-10', agents='a',
        evaluator=lambda _strategy: (_ for _ in ()).throw(
            AssertionError('stale candidate must not be evaluated')
        ),
    )

    assert result['status'] == 'kept'
    assert result['reason_code'] == 'ab-stale'
    assert not (state_dir / 'resolver' / 'pacman4console_ab_trial.json').exists()


def test_pacman_ab_promotion_flag_uses_unrounded_means():
    defaults, key, candidate_value = _pacman_candidate()
    candidate = dict(defaults)
    candidate[key] = candidate_value
    trial = {
        'status': 'pending', 'game': 'pacman4console',
        'baseline': defaults, 'candidate': candidate,
        'baseline_sha256': corner_improve._strategy_digest(defaults),
        'candidate_sha256': corner_improve._strategy_digest(candidate),
    }
    summary = corner_improve._pacman_ab_summary(
        trial, {'A': [10.01, 10.01], 'B': [10.02, 10.02]},
        date_str='2026-09-10', corner_stats={'n': 1, 'mean': 10, 'best': 10}, matches=2,
    )
    assert summary['baseline_mean'] == summary['candidate_mean'] == 10.0
    assert summary['promoted'] is True
    promoted_trial = {**trial, 'status': 'promoting', 'summary': summary}
    assert corner_improve._valid_pacman_ab_summary(summary, promoted_trial)


def test_pacman_interrupted_promotion_finishes_without_rerunning_ab(tmp_path, monkeypatch):
    state_dir = _setup_completed_pacman(tmp_path)
    defaults, key, candidate_value = _pacman_candidate()
    monkeypatch.setenv('DOCICH_BOT_BRAIN_DIR', str(tmp_path / 'live-brain'))
    _stage_pacman_candidate(state_dir, key, candidate_value)
    original_promote = corner_improve._promote

    def interrupted_promote(_g, _game, strategy_file, _old, new):
        strategy_file.parent.mkdir(parents=True, exist_ok=True)
        strategy_file.write_text(json.dumps(new), encoding='utf-8')
        raise OSError('simulated interruption after strategy replacement')

    corner_improve._promote = interrupted_promote
    try:
        with pytest.raises(OSError):
            run_corner_improve(
                _G(state_dir), game='pacman4console', date_str='2026-09-10', agents='a',
                evaluator=lambda strategy: {
                    'mean_score': 100 if strategy[key] == candidate_value else 50,
                    'played': 1,
                },
            )
    finally:
        corner_improve._promote = original_promote

    trial_path = state_dir / 'resolver' / 'pacman4console_ab_trial.json'
    assert json.loads(trial_path.read_text())['status'] == 'promoting'
    rerun = run_corner_improve(
        _G(state_dir), game='pacman4console', date_str='2026-09-10', agents='a',
        evaluator=lambda _strategy: (_ for _ in ()).throw(
            AssertionError('durable promotion decision must not be reevaluated')
        ),
    )
    assert rerun['status'] == 'promoted'
    assert rerun['reason_code'] == 'ab-adopted'
    assert not trial_path.exists()
    live = json.loads((tmp_path / 'live-brain' / 'pacman4console' / 'weights.json').read_text())
    assert live[key] == candidate_value

import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import pytest

from docich.corner_improve import (
    CornerImproveError,
    build_prompt,
    parse_candidate,
    run_corner_improve,
    slice_corner_matches,
    summarize_matches,
)
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
    llm = lambda prompt: f'```json\n{{"{key}": 2.5}}\n```'

    state_dir = _setup_completed(tmp_path, [10, 20])
    g = _G(state_dir)
    (state_dir / 'resolver').mkdir(parents=True, exist_ok=True)
    (state_dir / 'resolver' / 'gnurobots_strategy.json').write_text(
        json.dumps(_weights()), encoding='utf-8')

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
    strategy = json.loads((state_dir / 'resolver' / 'gnurobots_strategy.json').read_text())
    assert strategy[key] == 2.5
    assert list((state_dir / 'resolver' / 'history').glob('*.json'))
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

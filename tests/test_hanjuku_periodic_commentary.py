"""Periodic Hanjuku commentary is corner-owned, grounded and non-repetitive."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from docich import hanjuku_narration, hanjuku_periodic as periodic
from docich import hanjuku_periodic_worker as worker
from docich.game_switch import atomic_write_json

IDENTITY = {'game': 'hanjuku-hero', 'runtime_id': 'g7-abc123',
            'generation': 7, 'lease_id': 'lease-periodic'}
RUN = {**IDENTITY, 'playing': True, 'phase': 'field', 'terminal_reason': None,
       'terminal_candidate': False}


def _runtime(tmp_path):
    runtime = tmp_path / 'runtimes' / IDENTITY['runtime_id']
    runtime.mkdir(parents=True)
    return runtime


def _write(path, value):
    atomic_write_json(path, value)


def _game(**extra):
    settings = {'enabled': True, 'interval_s': 900, 'retry_s': 300,
                'generation_timeout_s': 15, 'max_attempts': 2, **extra}
    return SimpleNamespace(raw={'hanjuku': {'periodic_commentary': settings}})


def _bot(identity=IDENTITY, **policy):
    return {'decision_trace': dict(identity),
            'policy': {'chapter': 8, 'month': '14-2', 'chart_edition': 'steady',
                       'gold': 732, 'captured': ['ながはま'], 'lost': ['おかざき'],
                       'stats': {'wins': 670, 'losses': 145},
                       'tally': {'battles_started': 825},
                       'battle': {'ally': 'トレビス', 'enemy': 'ブラッキー',
                                  'ally_hp': 66, 'enemy_hp': 55,
                                  'castle': 'いちじょうだに'},
                       'goals': [{'kind': 'retake', 'target': 'おかざき'}],
                       **policy}}


def _decision(path, *records):
    with path.open('w', encoding='utf-8') as stream:
        for record in records:
            stream.write(json.dumps({'event': 'decision', **record},
                                    ensure_ascii=False) + '\n')


@pytest.fixture
def live(tmp_path):
    runtime = _runtime(tmp_path)
    _write(runtime / 'hanjuku_run.json', RUN)
    _write(runtime / 'hanjuku_progress.json',
           {**IDENTITY, 'schema': 1, 'cleared': 7, 'ambiguous': False,
            'frame_sha256': 'a' * 64})
    _write(runtime / 'hanjuku_bot.json', _bot())
    now = time.time()
    _decision(runtime / 'hanjuku_decisions.jsonl',
              {'decision': 'battle_result', 'outcome': 'win', 'ally': 'エピィ',
               'enemy': 'ミュスカデ', 'ally_hp': 57, 'enemy_hp': 0,
               'at': now - 500, 'step': 1},
              {'decision': 'castle_lost_observed', 'castle': 'おかざき',
               'at': now - 300, 'step': 2},
              {'decision': 'soldier_refill_receipt', 'resulting_event': 'paid',
               'observed_metric': {'qty': 99, 'soldiers_after': 99},
               'at': now - 200, 'step': 3},
              {'decision': 'battle_card', 'card': 'クースカン', 'at': now - 100},
              {'decision': 'action_plan', 'at': now - 50})
    return SimpleNamespace(runtime=runtime, root=tmp_path, now=now, game=_game())


def _state(live):
    state = periodic._state(live.runtime, IDENTITY)
    state['run_started_at'] = live.now - 900
    return state


def test_config_is_opt_in_and_bounded():
    assert periodic.settings({})['enabled'] is False
    assert periodic.settings({'enabled': True})['interval_s'] == 900.0
    with pytest.raises(ValueError):
        periodic.settings({'enabled': 'yes'})
    with pytest.raises(ValueError):
        periodic.settings({'interval_s': 60})
    with pytest.raises(ValueError):
        periodic.settings({'max_attempts': True})
    assert periodic.settings({'agents': 'opencode-go:deepseek-v4.1-flash'})['agents'] == \
        'opencode-go:deepseek-v4.1-flash'
    with pytest.raises(ValueError):
        periodic.settings({'agents': 'model; rm -rf /'})


def test_scene_generate_helper_honors_the_periodic_agent_override():
    helper = (ROOT / 'scripts/hanjuku_scene_generate.sh').read_text(encoding='utf-8')
    assert 'HANJUKU_PERIODIC_AGENTS' in helper
    assert 'scene_agents="$HANJUKU_PERIODIC_AGENTS"' in helper


def test_live_game_config_is_read_from_the_game_file(tmp_path):
    path = tmp_path / 'hanjuku-hero.toml'
    path.write_text('[hanjuku.periodic_commentary]\n'
                    'enabled = true\ninterval_s = 600\n', encoding='utf-8')
    game = SimpleNamespace(raw={'hanjuku': {'periodic_commentary': {'enabled': False}}})
    assert periodic.config(game)['enabled'] is False
    assert periodic.config(game, path) == {
        'enabled': True, 'interval_s': 600.0, 'retry_s': 300.0,
        'generation_timeout_s': 90.0, 'max_attempts': 2, 'agents': ''}


def test_current_falls_back_to_observed_country_counts_without_shop():
    policy = {'chapter': 8, 'month': '14-2',
              'country_soldier_count': {'count': 12, 'scope': [8, '14-2']},
              'country_general_count': {'count': 3, 'scope': [8, '14-2']}}
    current = periodic._current(policy)
    assert current['economy']['soldiers'] == 12
    assert current['economy']['generals'] == 3


def test_event_fact_reads_battle_hp_from_the_observed_metric():
    fact = periodic._event_fact({
        'decision': 'battle_result', 'outcome': 'win', 'ally': 'エピィ',
        'enemy': 'ミュスカデ',
        'observed_metric': {'ally_hp': 57, 'enemy_hp': 0}})
    assert fact == {'kind': 'battle_result', 'outcome': 'win', 'ally': 'エピィ',
                    'enemy': 'ミュスカデ', 'ally_hp': 57, 'enemy_hp': 0}
    assert periodic._event_fact({
        'decision': 'order_start', 'general': 'コリアンダー', 'target': 'ながはま',
        'roster_probe': True}) is None


def test_situation_is_whitelisted_and_counts_the_window(live):
    value = periodic.situation(live.runtime, RUN, _state(live), live.now)
    assert value['progress'] == {'cleared': 7, 'chapter': 8, 'year': 14,
                                 'month': 2, 'edition': 'steady'}
    assert value['window']['wins'] == 1 and value['window']['losses'] == 0
    assert value['window']['castle_losses'] == 1
    assert {event['kind'] for event in value['events']} == {
        'battle_result', 'castle_lost_observed', 'soldier_refill_receipt'}
    assert value['current']['battle']['ally'] == 'トレビス'
    assert value['current']['order'] == {'goal': 'retake', 'target': 'おかざき'}
    assert value['current']['economy']['gold'] == 732
    text = json.dumps(value, ensure_ascii=False)
    for forbidden in ('decision_id', 'runtime_id', 'lease_id', 'reason',
                      'battle_card', 'action_plan', 'snapshot'):
        assert forbidden not in text


def test_prompt_contains_situation_and_avoid_list(live):
    value = periodic.situation(live.runtime, RUN, _state(live), live.now)
    prompt = periodic.build_prompt(value, ['前回の実況です。'], ['直前の候補です。'])
    assert '直近の実況' in prompt
    assert '前回の実況です。' in prompt and '直前の候補です。' in prompt
    assert '同じ書き出し' in prompt
    assert '"texts"' in prompt


def test_parse_candidates_keeps_valid_lines_and_skips_short_ones():
    output = json.dumps({'texts': ['短い。', '第8章の14年2月、所持金は732ゴールドです。',
                                   '第8章の14年2月、所持金は732ゴールドです。']},
                        ensure_ascii=False)
    assert periodic.parse_candidates(output) == [
        '第8章の14年2月、所持金は732ゴールドです。']
    with pytest.raises(periodic.PeriodicError):
        periodic.parse_candidates(json.dumps({'texts': ['短い。']}, ensure_ascii=False))


def _facts(live):
    return periodic.flatten(
        periodic.situation(live.runtime, RUN, _state(live), live.now))


@pytest.mark.parametrize('text,expected', [
    ('第8章の14年2月、おかざき城の奪還を狙います。', True),
    ('エピィ将軍がミュスカデに勝ちました。', True),
    ('この15分で1勝0敗、兵士を99人補充しました。', True),
    ('7章をクリアしました。', True),
    ('8章をクリアしました。', False),
    ('トレビス将軍のHPは66、ブラッキー将軍のHPは55です。', True),
    ('トレビス将軍とブラッキー将軍、体力は66対55です。', True),
    ('第9章に進みました。', False),
    ('兵士を500人補充しました。', False),
    ('クーモンを倒しました。', False),
    ('おかざき城を占領しました。', False),
    ('ゲームオーバーになりました。', False),
    ('コリアンダー将軍が出撃しました。', False),
    ('次はえど城へ向かいます。', False),
])
def test_valid_text_grounds_names_numbers_and_claims(live, text, expected):
    assert periodic.valid_text(text, _facts(live)) is expected


def test_novelty_rejects_same_opening_and_template_lines():
    recent = ['第8章の14年2月、おかざき城の奪還を狙います。']
    assert periodic.similarity(recent[0], recent[0]) >= 1.0
    assert periodic.similarity('第8章の14年2月、所持金は732ゴールドです。',
                               '第8章の14年2月、兵士を99人補充しました。') >= 1.0
    assert periodic.similarity('この15分、エピィ将軍がミュスカデに勝ちました。',
                               'おかざき城を失いましたが、兵士を99人補充しました。') < 1.0
    assert periodic.choose_novel(
        ['第8章の14年2月、おかざき城の奪還を狙います。',
         'この15分、エピィ将軍がミュスカデに勝ちました。'], recent) == \
        'この15分、エピィ将軍がミュスカデに勝ちました。'
    assert periodic.choose_novel(
        ['第8章の14年2月、おかざき城の奪還を狙います。'], recent) is None


def test_scheduler_waits_interval_then_starts_one_worker(live, monkeypatch):
    spawned = []
    monkeypatch.setattr(periodic, '_spawn', lambda repo, runtime, fd, config: spawned.append(runtime))
    g = SimpleNamespace(repo_root=live.root, config_path=live.root / 'config.toml')
    assert periodic.tick(g, live.game, live.runtime, RUN, IDENTITY, now=live.now) is None
    assert spawned == []
    request = periodic.tick(g, live.game, live.runtime, RUN, IDENTITY,
                            now=live.now + 900)
    assert request is not None and spawned == [live.runtime]
    assert json.loads((live.runtime / periodic.REQUEST).read_text())['seq'] == 1
    state = json.loads((live.runtime / periodic.STATE).read_text())
    assert state['pending_seq'] == 1
    assert periodic.tick(g, live.game, live.runtime, RUN, IDENTITY,
                         now=live.now + 930) is None
    assert len(spawned) == 1
    atomic_write_json(live.runtime / periodic.RESULT, {
        'schema': 1, **IDENTITY, 'seq': 1, 'at': live.now + 920,
        'status': 'enqueued', 'text': '第8章の14年2月、所持金は732ゴールドです。',
        'attempts': 1})
    periodic.tick(g, live.game, live.runtime, RUN, IDENTITY, now=live.now + 940)
    state = json.loads((live.runtime / periodic.STATE).read_text())
    assert state['pending_seq'] is None
    assert state['last_result_seq'] == 1
    assert state['recent_texts'] == ['第8章の14年2月、所持金は732ゴールドです。']
    assert state['next_at'] >= live.now + 900


def test_scheduler_honors_a_live_enable_without_in_memory_config(live, monkeypatch):
    config_dir = live.root / 'config' / 'games'
    config_dir.mkdir(parents=True)
    (config_dir / 'hanjuku-hero.toml').write_text(
        '[hanjuku.periodic_commentary]\nenabled = true\ninterval_s = 600\n',
        encoding='utf-8')
    spawned = []
    monkeypatch.setattr(periodic, '_spawn', lambda repo, runtime, fd, config: spawned.append(runtime))
    g = SimpleNamespace(repo_root=live.root, config_path=live.root / 'config.toml',
                        games_dir=config_dir)
    stale_game = _game(enabled=False)
    assert periodic.tick(g, stale_game, live.runtime, RUN, IDENTITY, now=live.now) is None
    state = json.loads((live.runtime / periodic.STATE).read_text())
    assert state['next_at'] == live.now + 600
    assert periodic.tick(g, stale_game, live.runtime, RUN, IDENTITY,
                         now=live.now + 600) is not None
    assert spawned == [live.runtime]


def test_scheduler_defers_during_battle_but_not_forever(live, monkeypatch):
    spawned = []
    monkeypatch.setattr(periodic, '_spawn', lambda repo, runtime, fd, config: spawned.append(runtime))
    g = SimpleNamespace(repo_root=live.root, config_path=live.root / 'config.toml')
    battle = {**RUN, 'phase': 'battle'}
    assert periodic.tick(g, live.game, live.runtime, battle, IDENTITY,
                         now=live.now) is None
    assert periodic.tick(g, live.game, live.runtime, battle, IDENTITY,
                         now=live.now + 900) is None
    state = json.loads((live.runtime / periodic.STATE).read_text())
    assert state['defer_since'] == live.now + 900
    assert spawned == []
    request = periodic.tick(g, live.game, live.runtime, battle, IDENTITY,
                            now=live.now + 900 + periodic.DEFER_MAX_S + 1)
    assert request is not None and spawned == [live.runtime]


def test_scheduler_does_nothing_when_disabled_or_terminal(live, monkeypatch):
    monkeypatch.setattr(periodic, '_spawn', lambda *args: pytest.fail('must not spawn'))
    g = SimpleNamespace(repo_root=live.root, config_path=live.root / 'config.toml')
    disabled = _game(enabled=False)
    assert periodic.tick(g, disabled, live.runtime, RUN, IDENTITY, now=live.now + 900) is None
    terminal = {**RUN, 'terminal_reason': 'game_over'}
    assert periodic.tick(g, live.game, live.runtime, terminal, IDENTITY,
                         now=live.now + 900) is None
    assert not (live.runtime / periodic.REQUEST).exists()


def _request(live, recent=()):
    value = periodic.situation(live.runtime, RUN, _state(live), live.now)
    return {'schema': 1, 'version': periodic.VERSION, **IDENTITY, 'seq': 1,
            'at': live.now, 'expires_at': live.now + periodic.REQUEST_TTL_S,
            'recent_texts': list(recent), 'situation': value}


def _worker(live, monkeypatch, output, delivered, recent=()):
    _write(live.runtime / periodic.REQUEST, _request(live, recent))
    monkeypatch.setattr(worker, 'load_game', lambda *args: live.game)
    monkeypatch.setattr(worker, 'current', lambda *args, **kwargs: RUN)
    return worker.consume(
        SimpleNamespace(repo_root=live.root, state_dir=live.root), live.runtime, now=live.now,
        generate_fn=lambda *args: (output, 'RADIO_AGENTS'),
        deliver_fn=lambda *args, **kwargs: delivered.append((args, kwargs)) or 'enqueued')


def test_worker_sets_the_periodic_agent_override(live, monkeypatch):
    seen = []
    live.game.raw['hanjuku']['periodic_commentary']['agents'] = (
        'opencode-go:deepseek-v4.1-flash,opencode-go:deepseek-v4-flash')
    _write(live.runtime / periodic.REQUEST, _request(live))
    monkeypatch.setattr(worker, 'load_game', lambda *args: live.game)
    monkeypatch.setattr(worker, 'current', lambda *args, **kwargs: RUN)
    worker.consume(
        SimpleNamespace(repo_root=live.root, state_dir=live.root), live.runtime,
        now=live.now,
        generate_fn=lambda *args: seen.append(os.environ.get('HANJUKU_PERIODIC_AGENTS'))
        or (json.dumps({'texts': ['第8章の14年2月、所持金は732ゴールドです。']},
                       ensure_ascii=False), 'RADIO_AGENTS'),
        deliver_fn=lambda *args, **kwargs: 'enqueued')
    assert seen == ['opencode-go:deepseek-v4.1-flash,opencode-go:deepseek-v4-flash']


def test_worker_delivers_the_least_repetitive_grounded_candidate(live, monkeypatch):
    delivered = []
    output = json.dumps({'texts': [
        '第8章の14年2月、所持金は732ゴールドです。',
        'この15分、エピィ将軍がミュスカデに勝ちました。',
        '第8章の14年2月、兵士を99人補充しました。']}, ensure_ascii=False)
    status, text, attempts, role = _worker(
        live, monkeypatch, output, delivered,
        recent=['第8章の14年2月、所持金は732ゴールドです。'])
    assert (status, text, attempts, role) == ('enqueued',
        'この15分、エピィ将軍がミュスカデに勝ちました。', 1, 'RADIO_AGENTS')
    assert len(delivered) == 1
    assert delivered[0][0][4] == text
    result = json.loads((live.runtime / periodic.RESULT).read_text())
    assert result['status'] == 'enqueued' and result['text'] == text


def test_worker_is_silent_when_every_candidate_repeats(live, monkeypatch):
    delivered = []
    repeated = '第8章の14年2月、所持金は732ゴールドです。'
    _write(live.runtime / periodic.REQUEST, _request(live, [repeated]))
    monkeypatch.setattr(worker, 'load_game', lambda *args: live.game)
    monkeypatch.setattr(worker, 'current', lambda *args, **kwargs: RUN)
    status, text, attempts, role = worker.consume(
        SimpleNamespace(repo_root=live.root, state_dir=live.root), live.runtime, now=live.now,
        generate_fn=lambda *args: (json.dumps({'texts': [repeated]}, ensure_ascii=False),
                                   'RADIO_AGENTS'),
        deliver_fn=lambda *args, **kwargs: delivered.append(args) or 'enqueued')
    assert status == 'duplicate' and text is None and delivered == []
    result = json.loads((live.runtime / periodic.RESULT).read_text())
    assert result['status'] == 'duplicate'


def test_worker_retries_an_invalid_answer_then_delivers(live, monkeypatch):
    delivered = []
    _write(live.runtime / periodic.REQUEST, _request(live))
    monkeypatch.setattr(worker, 'load_game', lambda *args: live.game)
    monkeypatch.setattr(worker, 'current', lambda *args, **kwargs: RUN)
    answers = iter([
        json.dumps({'texts': ['第9章に進みました。']}, ensure_ascii=False),
        json.dumps({'texts': ['第8章の14年2月、所持金は732ゴールドです。']},
                   ensure_ascii=False)])
    status, text, attempts, role = worker.consume(
        SimpleNamespace(repo_root=live.root, state_dir=live.root), live.runtime, now=live.now,
        generate_fn=lambda *args: (next(answers), 'RADIO_AGENTS'),
        deliver_fn=lambda *args, **kwargs: delivered.append(args) or 'enqueued')
    assert status == 'enqueued' and attempts == 2
    assert text == '第8章の14年2月、所持金は732ゴールドです。'


def test_delivery_uses_the_fenced_audio_queue_and_posts_no_chat(live, monkeypatch):
    queued = []
    monkeypatch.setattr('docich.agent.fence.read_canonical',
                        lambda _: {'phase': 'ready', 'active': IDENTITY})
    monkeypatch.setattr('docich.agent.fence.shared_section', lambda _path, fn, **kw: fn())
    monkeypatch.setattr('docich.trading.soren_output.enqueue_audio_text',
                        lambda *args, **kwargs: queued.append((args, kwargs)))
    _write(live.runtime / 'hanjuku_bot.json', {'decision_trace': dict(IDENTITY)})
    status = hanjuku_narration.deliver_periodic(
        SimpleNamespace(state_dir=live.root, repo_root=live.root), live.game,
        live.runtime, IDENTITY, '第8章の14年2月、所持金は732ゴールドです。',
        at=live.now, max_age=60, seq=3)
    assert status == 'enqueued' and len(queued) == 1
    args, kwargs = queued[0]
    assert args[1] == '第8章の14年2月、所持金は732ゴールドです。'
    assert kwargs['context'] == 'hanjuku_commentary'
    assert kwargs['runtime_fence'] == {**IDENTITY, 'expires_at': live.now + 60}
    assert json.loads((live.runtime / 'hanjuku_narration.jsonl').read_text())['status'] == 'enqueued'

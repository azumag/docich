# Verification-only trigger for current-main Hanjuku quality acceptance; no runtime behavior change.
"""Scene narration is evidence-bound, asynchronous and silent on failure."""
from __future__ import annotations

import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import shutil
import shlex
import sys
import time
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from docich import hanjuku_scene as scene, hanjuku_scene_worker as worker
from docich.game_switch import atomic_write_json

IDENTITY = {'game': 'hanjuku-hero', 'runtime_id': 'g1-abc123', 'generation': 1, 'lease_id': 'lease-1'}
OPENING = {'decision': 'battle_start', 'ally': 'ココット', 'enemy': 'クイーン',
           'observed_metric': {'ally_hp': 40, 'enemy_hp': 60}}
OUTPUT = json.dumps({'text': '開戦時のココットはHP40、クイーンはHP60でした。', 'fact_ids': ['f1']}, ensure_ascii=False)


def write_config(root, enabled=True):
    path = root / 'config/games/hanjuku-hero.toml'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[game]\nname="hanjuku-hero"\nadapter="retroarch"\n'
                    '[hanjuku.narration]\nenabled=false\n'
                    '[hanjuku.scene_commentary]\nenabled=' + str(enabled).lower() + '\n')


@pytest.fixture
def live(tmp_path, monkeypatch):
    root = tmp_path / 'docich'
    runtime = root / 'run/runtimes' / IDENTITY['runtime_id']
    runtime.mkdir(parents=True)
    write_config(root)
    g = SimpleNamespace(repo_root=root, state_dir=root / 'run')
    clock = [1000.0]
    monkeypatch.setattr(worker.time, 'time', lambda: clock[0])
    monkeypatch.setattr('docich.agent.fence.read_canonical', lambda _: {'phase': 'ready', 'active': IDENTITY})
    monkeypatch.setattr(worker, 'load_game', lambda *a: SimpleNamespace(raw={'hanjuku': {'narration': {}}}))
    atomic_write_json(runtime / 'hanjuku_run.json', {**IDENTITY, 'playing': True})
    state = {'step': 4, 'screen_kind': 'battle', 'policy': {
        'chapter': 1, 'month': '1-4', 'battle': {'ally': 'ココット', 'enemy': 'クイーン'},
        'tally': {'battles_started': 1}}}
    cfg = scene.config(root)
    snapshot = scene.publish(runtime, state, [OPENING], {'hanjuku': IDENTITY}, cfg, now=clock[0])
    return SimpleNamespace(root=root, runtime=runtime, g=g, clock=clock, state=state, cfg=cfg, snapshot=snapshot)


def consume(live, generate=None, deliver=None):
    return worker.consume(live.g, live.runtime, generate_fn=generate or (lambda *a: (OUTPUT, 'RADIO_AGENTS')),
                          deliver_fn=deliver or (lambda *a: 'enqueued'))


def test_fixed_config_stops_legacy_delivery_but_enables_scene_consumer():
    import tomllib
    doc = tomllib.loads((ROOT / 'config/games/hanjuku-hero.toml').read_text())
    assert doc['hanjuku']['narration']['enabled'] is False
    assert scene.settings(doc['hanjuku']['scene_commentary'])['enabled'] is True


def test_fact_whitelist_requires_receipts_and_does_not_infer_death_or_capture():
    assert scene.fact({'decision': 'battle_card', 'card': 'イッテツーン'}) is None
    assert scene.fact({'decision': 'battle_okunote_select', 'choice': 'バルムンク'}) is None
    assert scene.fact({'decision': 'battle_result', 'ally': 'ココット', 'enemy': 'クイーン',
                       'outcome': 'unclassified'}) is None
    result = scene.fact({'decision': 'battle_result', 'ally': 'ココット', 'enemy': 'クイーン',
                         'outcome': 'loss', 'resulting_event': 'lost:アルマムーン',
                         'observed_metric': {'general_loss': 'unclassified', 'cards_selected': ['イッテツーン']}})
    assert result == {'kind': 'battle_result', 'ally': 'ココット', 'enemy': 'クイーン', 'outcome': 'loss'}
    receipt = {'decision': 'soldier_refill_receipt', 'resulting_event': 'unverified',
               'observed_metric': {'qty': 20, 'soldiers_after': 70, 'basis': 'observed_plus_paid_refill'}}
    assert scene.fact(receipt) is None
    receipt['resulting_event'] = 'paid'
    assert scene.fact(receipt)['soldiers_after'] == 70
    build = {'decision': 'chikujou', 'observed_metric': {'castle': 'アルマムーン', 'quoted_cost': 5,
             'upgrade_verified': True, 'payment_verified': False}}
    assert scene.fact(build) is None
    build['observed_metric']['payment_verified'] = True
    assert scene.fact(build)['cost'] == 5


def test_same_fact_does_not_renew_age_or_regenerate_on_next_bot_step(live):
    live.clock[0] += 1
    live.state['step'] += 1
    again = scene.publish(live.runtime, live.state, [OPENING], {'hanjuku': IDENTITY}, live.cfg)
    assert again['request'] == live.snapshot['request']
    assert again['observed_at'] > live.snapshot['observed_at']
    assert consume(live) == 'enqueued'
    assert consume(live, generate=lambda *a: pytest.fail('duplicate provider call')) == 'duplicate'


def test_unknown_or_empty_facts_never_spawn_worker(live):
    other = live.runtime.parent / 'g2-abc123'
    other.mkdir()
    ident = {**IDENTITY, 'runtime_id': other.name, 'generation': 2}
    value = scene.publish(other, {}, [{'decision': 'battle_card'}], {'hanjuku': ident}, live.cfg)
    assert value['request'] is None
    assert not scene.start_worker(live.root, other, value, live.cfg, popen=lambda *a, **k: pytest.fail())


def test_disabled_config_does_not_start_bootstrap_or_delivery(live):
    write_config(live.root, False)
    assert worker.consume(live.g, live.runtime,
                          generate_fn=lambda *a: pytest.fail('disabled provider')) == 'disabled'
    cfg = scene.config(live.root)
    snapshot = scene.publish(live.runtime, live.state, [OPENING], {'hanjuku': IDENTITY}, cfg)
    assert snapshot['request'] is None
    assert not scene.start_worker(live.root, live.runtime, snapshot, cfg, popen=lambda *a, **k: pytest.fail())


def test_disabled_while_generating_is_silent(live):
    def generate(*args):
        write_config(live.root, False)
        return OUTPUT, 'RADIO_AGENTS'
    assert consume(live, generate, lambda *a: pytest.fail('disabled enqueue')) == 'disabled'


@pytest.mark.parametrize('malformed', ['run_generation', 'canonical_generation', 'schema', 'version'])
def test_malformed_identity_or_producer_protocol_never_reaches_generation(live, monkeypatch, malformed):
    if malformed == 'run_generation':
        atomic_write_json(live.runtime / 'hanjuku_run.json', {**IDENTITY, 'generation': True, 'playing': True})
    elif malformed == 'canonical_generation':
        monkeypatch.setattr('docich.agent.fence.read_canonical', lambda _: {
            'phase': 'ready', 'active': {**IDENTITY, 'generation': True}})
    else:
        snapshot = {**live.snapshot, malformed: True if malformed == 'schema' else 'unknown-version'}
        atomic_write_json(live.runtime / scene.SCENE_FILE, snapshot)
    assert consume(live, lambda *a: pytest.fail('malformed provider call')) == 'fence_lost'


@pytest.mark.parametrize('change', ['scene', 'expired', 'terminal', 'inactive', 'generation'])
def test_changed_or_stale_context_drops_generated_text(live, monkeypatch, change):
    def generate(*args):
        if change == 'scene':
            live.state['policy']['battle']['enemy'] = 'ゼウス'
            scene.publish(live.runtime, live.state, [], {'hanjuku': IDENTITY}, live.cfg)
        elif change == 'expired':
            live.clock[0] += 21
        elif change in {'terminal', 'inactive'}:
            run = {**IDENTITY, 'playing': change != 'inactive'}
            if change == 'terminal':
                run['terminal_candidate'] = True
            atomic_write_json(live.runtime / 'hanjuku_run.json', run)
        else:
            monkeypatch.setattr('docich.agent.fence.read_canonical', lambda _: {
                'phase': 'ready', 'active': {**IDENTITY, 'generation': 2}})
        return OUTPUT, 'RADIO_AGENTS'
    assert consume(live, generate, lambda *a: pytest.fail('stale enqueue')) in {
        'scene_changed', 'no_facts', 'stale', 'fence_lost'}


def test_failed_generation_is_consumed_once_with_no_fixed_fallback(live):
    def fail(*args):
        raise worker.SceneError('rate_limit')
    assert consume(live, fail, lambda *a: pytest.fail()) == 'rate_limit'
    assert consume(live, lambda *a: pytest.fail('retry of the same scene')) == 'duplicate'
    assert not (live.runtime / 'hanjuku_commentary.jsonl').exists()
    rows = [json.loads(line) for line in (live.runtime / f'{scene.LOG_NAME}.jsonl').read_text().splitlines()]
    assert rows[-1]['reason'] == 'rate_limit'
    assert not any('text' in row or 'prompt' in row for row in rows)


def test_success_and_delivery_failure_are_separate_stages(live):
    assert consume(live, deliver=lambda *a: 'delivery_failed') == 'delivery_failed'
    state = json.loads((live.runtime / scene.WORKER_FILE).read_text())
    assert state['counters']['generate_succeeded'] == 1
    assert state['counters']['deliver_failed'] == 1
    assert 'deliver_enqueued' not in state['counters']


def test_completed_battle_remains_audible_after_next_field_observation(live):
    result = {'decision': 'battle_result', 'ally': 'ココット', 'enemy': 'クイーン', 'outcome': 'win',
              'observed_metric': {'ally_hp': 24, 'enemy_hp': 0}}
    original = scene.publish(live.runtime, live.state, [result], {'hanjuku': IDENTITY}, live.cfg)
    assert original['request']['scope'] == 'history'
    def generate(*args):
        live.clock[0] += 8
        live.state['screen_kind'] = 'map'
        live.state['policy'].pop('battle')
        latest = scene.publish(live.runtime, live.state, [], {'hanjuku': IDENTITY}, live.cfg)
        assert latest['scene_id'] != original['scene_id']
        assert latest['request']['at'] == original['request']['at']
        assert latest['request']['expires_at'] == original['request']['expires_at']
        return json.dumps({'text': 'ココットがクイーンに勝ち、終了時のココットのHPは24でした。',
                           'fact_ids': ['f1']}, ensure_ascii=False), 'RADIO_AGENTS'
    calls = []
    assert consume(live, generate, lambda *a: calls.append(a) or 'enqueued') == 'enqueued'
    assert len(calls) == 1


def test_completed_receipt_can_cross_menu_but_not_next_battle(live):
    live.state['policy'].pop('battle')
    live.state['screen_kind'] = 'month_menu'
    receipt = {'decision': 'soldier_refill_receipt', 'resulting_event': 'paid',
               'observed_metric': {'qty': 20, 'soldiers_after': 70, 'basis': 'screen_total'}}
    original = scene.publish(live.runtime, live.state, [receipt], {'hanjuku': IDENTITY}, live.cfg)
    live.clock[0] += 1
    live.state['screen_kind'] = 'map'
    scene.publish(live.runtime, live.state, [], {'hanjuku': IDENTITY}, live.cfg)
    assert worker.current(live.g, live.runtime, original)['request'] == original['request']
    live.state['policy']['battle'] = {'ally': 'ココット', 'enemy': 'ゼウス'}
    live.state['policy']['tally']['battles_started'] += 1
    value = scene.publish(live.runtime, live.state, [], {'hanjuku': IDENTITY}, live.cfg)
    assert value['request'] is None


def test_generation_cooldown_uses_attempts_not_only_success(live):
    assert consume(live) == 'enqueued'
    live.clock[0] += 3
    new = {**OPENING, 'observed_metric': {'ally_hp': 39, 'enemy_hp': 59}}
    scene.publish(live.runtime, live.state, [new], {'hanjuku': IDENTITY}, live.cfg)
    assert consume(live, lambda *a: pytest.fail('rate limit not respected')) == 'cooldown'


@pytest.mark.parametrize('output', [
    {'text': 'ココットは死亡しました。', 'fact_ids': ['f1']},
    {'text': 'クイーンのHPは50でした。', 'fact_ids': ['f1']},
    {'text': '開戦時のココットはHP60、クイーンはHP40でした。', 'fact_ids': ['f1']},
    {'text': '開戦時の記録でした。終了時のココットのHPは40でした。', 'fact_ids': ['f1']},
    {'text': '開戦時の記録で、終了時のココットのHPは40でした。', 'fact_ids': ['f1']},
    {'text': 'ココットがクイーンに勝ちました。', 'fact_ids': ['f1']},
    {'text': 'クイーンがココットに負けました。', 'fact_ids': ['f1']},
    {'text': 'ココットがクイーンを倒しました。', 'fact_ids': ['f1']},
    {'text': '兵士を補充しました。', 'fact_ids': ['f1']},
    {'text': '卵が回復しました。', 'fact_ids': ['f1']},
    {'text': 'アルマムーンを占領しました。', 'fact_ids': ['f1']},
    {'text': '現在のHPは40です。', 'fact_ids': ['f1']},
    {'text': 'ココットの開戦時HPは40でした。', 'fact_ids': ['f9']},
    {'text': 'ココットの開戦時HPは40でした。', 'fact_ids': ['f1', 'f1']},
    {'text': '', 'fact_ids': ['f1']},
    {'text': 'a' * 121, 'fact_ids': ['f1']},
    {'text': 'Before battle HP40', 'fact_ids': ['f1']},
])
def test_invalid_or_unobserved_claim_is_silent(live, output):
    with pytest.raises(worker.SceneError, match='invalid_output'):
        worker.parse_output(json.dumps(output, ensure_ascii=False), live.snapshot)


def test_prompt_uses_only_typed_facts_and_marks_hp_as_historical(live):
    live.snapshot['untrusted_reason'] = 'DO_NOT_INCLUDE_THIS'
    prompt = worker.build_prompt(live.snapshot)
    assert 'DO_NOT_INCLUDE_THIS' not in prompt
    assert '開戦時' in prompt and '死亡を意味しません' in prompt
    assert 'lease-1' not in prompt and IDENTITY['runtime_id'] not in prompt
    assert worker.parse_output(OUTPUT, live.snapshot).startswith('開戦時')


def test_result_outcome_and_end_hp_stay_bound_to_the_observed_general(live):
    result = {'decision': 'battle_result', 'ally': 'ココット', 'enemy': 'クイーン', 'outcome': 'win',
              'observed_metric': {'ally_hp': 24, 'enemy_hp': 0}}
    snapshot = scene.publish(live.runtime, live.state, [result], {'hanjuku': IDENTITY}, live.cfg)
    for text in ['ココットがクイーンに勝ちました。', 'クイーンがココットに敗れました。',
                 'クイーンがココットの攻撃に敗れました。', 'ココットの勝利でした。',
                 '終了時のココットのHPは24でした。']:
        assert worker.parse_output(json.dumps({'text': text, 'fact_ids': ['f1']}, ensure_ascii=False), snapshot) == text
    for text in ['クイーンがココットに勝ちました。', 'ココットがクイーンに敗れました。',
                 'ココットはクイーンに勝てませんでした。', 'ココットはクイーンに勝たなかった。',
                 'ココットがクイーンの攻撃に敗れました。',
                 'ココットがココットに勝ちました。', '終了時のココットのHPは0でした。',
                 '開戦時のココットのHPは24でした。', 'ココットの勝利で卵が回復しました。']:
        with pytest.raises(worker.SceneError, match='invalid_output'):
            worker.parse_output(json.dumps({'text': text, 'fact_ids': ['f1']}, ensure_ascii=False), snapshot)
    for text in ['終了時の記録でした。開戦時のココットのHPは24でした。',
                 '終了時の記録で、開戦時のココットのHPは24でした。']:
        with pytest.raises(worker.SceneError, match='invalid_output'):
            worker.parse_output(json.dumps({'text': text, 'fact_ids': ['f1']}, ensure_ascii=False), snapshot)


def test_refill_count_and_total_cannot_be_swapped(live):
    receipt = {'decision': 'soldier_refill_receipt', 'resulting_event': 'paid',
               'observed_metric': {'qty': 20, 'soldiers_after': 70, 'basis': 'screen_total'}}
    snapshot = scene.publish(live.runtime, live.state, [receipt], {'hanjuku': IDENTITY}, live.cfg)
    good = '兵士を20人補充し、総数は70人でした。'
    assert worker.parse_output(json.dumps({'text': good, 'fact_ids': ['f1']}, ensure_ascii=False), snapshot) == good
    for text in ['兵士を70人補充し、総数は20人でした。', '兵士を20人補充して勝利しました。',
                 '敵の兵士総数は70人でした。']:
        with pytest.raises(worker.SceneError, match='invalid_output'):
            worker.parse_output(json.dumps({'text': text, 'fact_ids': ['f1']}, ensure_ascii=False), snapshot)


def test_soldier_count_behind_egg_priority_cannot_be_assigned_to_the_enemy(live):
    event = {'decision': 'egg_priority_replan', 'observed_metric': {'soldiers': 70}}
    snapshot = scene.publish(live.runtime, live.state, [event], {'hanjuku': IDENTITY}, live.cfg)
    good = '兵士数は70人で、卵回復を優先する方針に変えました。'
    assert worker.parse_output(json.dumps({'text': good, 'fact_ids': ['f1']}, ensure_ascii=False), snapshot) == good
    with pytest.raises(worker.SceneError, match='invalid_output'):
        worker.parse_output(json.dumps({'text': '敵の兵士数は70人でした。', 'fact_ids': ['f1']}, ensure_ascii=False), snapshot)


@pytest.mark.parametrize('kind', ['castle_owned_observed', 'castle_lost_observed'])
def test_castle_ownership_observation_cannot_reverse_into_the_other_result(live, kind):
    snapshot = scene.publish(live.runtime, live.state, [{'decision': kind, 'castle': 'アルマムーン'}],
                             {'hanjuku': IDENTITY}, live.cfg)
    owned = 'アルマムーンの占領が確認されました。'
    lost = 'アルマムーンが敵に奪われました。'
    good, bad = (owned, lost) if kind == 'castle_owned_observed' else (lost, owned)
    assert worker.parse_output(json.dumps({'text': good, 'fact_ids': ['f1']}, ensure_ascii=False), snapshot) == good
    with pytest.raises(worker.SceneError, match='invalid_output'):
        worker.parse_output(json.dumps({'text': bad, 'fact_ids': ['f1']}, ensure_ascii=False), snapshot)


def test_verified_build_cost_can_be_spoken_without_inventing_a_level(live):
    event = {'decision': 'chikujou', 'observed_metric': {'castle': 'アルマムーン', 'quoted_cost': 5,
              'upgrade_verified': True, 'payment_verified': True}}
    snapshot = scene.publish(live.runtime, live.state, [event], {'hanjuku': IDENTITY}, live.cfg)
    text = 'アルマムーンを増築しました。築城費は5Gでした。'
    # The numeric clause must name its castle, even when the same name occurred
    # in a prior sentence; an ambiguous cost is dropped rather than inferred.
    with pytest.raises(worker.SceneError, match='invalid_output'):
        worker.parse_output(json.dumps({'text': text, 'fact_ids': ['f1']}, ensure_ascii=False), snapshot)
    text = 'アルマムーンの築城費は5Gで、増築が確認されました。'
    assert worker.parse_output(json.dumps({'text': text, 'fact_ids': ['f1']}, ensure_ascii=False), snapshot) == text


def card_decision():
    return {'decision': 'battle_card_damage_rejected', 'card': 'クースカン',
            'observed_metric': {'allowed': False, 'role': 'nonlethal_egg_risk',
                'estimate': {'target_kind': 'general', 'raw_damage_min': 32,
                             'enemy_soldier_hp_upper': 28, 'damage_lower_bound': 4,
                             'remaining_hp_upper': 36, 'lethal': False}}}


def test_v132_damage_estimate_is_a_judgment_not_observed_damage(live):
    snapshot = scene.publish(live.runtime, live.state, [card_decision()], {'hanjuku': IDENTITY}, live.cfg)
    assert snapshot['request']['scope'] == 'scene'
    prompt = worker.build_prompt(snapshot)
    assert '使用・命中・勝利ではありません' in prompt
    assert 'lethal=falseは計算下限で撃破を保証できない' in prompt
    good = {'text': 'クースカンのダメージ下限は計算上4で、倒せる保証がないため候補を再検討しました。',
            'fact_ids': ['f1']}
    assert worker.parse_output(json.dumps(good, ensure_ascii=False), snapshot) == good['text']
    held = {'text': 'クースカンは使用候補から外しました。', 'fact_ids': ['f1']}
    assert worker.parse_output(json.dumps(held, ensure_ascii=False), snapshot) == held['text']
    for text in ['クースカンで32ダメージを与えました。',
                 'クースカンを使い、敵の卵を落としました。',
                 'クースカンのダメージ下限は計算上32でした。',
                 'クースカンでは倒せないと判断しました。',
                 'クースカンを使いました。',
                 'クースカンのダメージは32でした。']:
        with pytest.raises(worker.SceneError, match='invalid_output'):
            worker.parse_output(json.dumps({'text': text, 'fact_ids': ['f1']}, ensure_ascii=False), snapshot)


def test_real_delivery_entry_keeps_original_fence_expiry(live, monkeypatch):
    from docich import hanjuku_narration
    monkeypatch.setattr('docich.agent.fence.shared_section', lambda _path, fn, **kw: fn())
    queued = []
    monkeypatch.setattr('docich.trading.soren_output.enqueue_audio_text',
                        lambda *a, **kw: queued.append((a, kw)))
    def generate(*args):
        live.clock[0] += 5
        scene.publish(live.runtime, live.state, [], {'hanjuku': IDENTITY}, live.cfg)
        return OUTPUT, 'BATCH_COMMENTARY_AGENTS'
    assert worker.consume(live.g, live.runtime, generate_fn=generate) == 'enqueued'
    assert len(queued) == 1
    assert queued[0][1]['context'] == 'hanjuku_commentary'
    assert queued[0][1]['runtime_fence'] == {**IDENTITY, 'expires_at': 1020.0}
    assert json.loads((live.runtime / 'hanjuku_narration.jsonl').read_text())['status'] == 'enqueued'


def test_terminal_run_starts_no_generation_or_delivery(live):
    atomic_write_json(live.runtime / 'hanjuku_run.json', {**IDENTITY, 'playing': True,
                                                        'terminal_reason': 'game_over'})
    assert consume(live, lambda *a: pytest.fail('terminal provider call')) == 'fence_lost'


def test_generation_timeout_stops_its_private_bootstrap_and_children(live):
    root = live.root / 'fake_soren'
    root.mkdir()
    (root / 'child.py').write_text('import time\nfrom pathlib import Path\n'
                                 'end=time.monotonic()+6\n'
                                 'with Path("heartbeat").open("ab",buffering=0) as stream:\n'
                                 ' while time.monotonic()<end:\n'
                                 '  stream.write(b"x"); time.sleep(.02)\n')
    (root / 'eloop_lib.sh').write_text("BATCH_COMMENTARY_AGENTS='opencode:fixture-only'\n"
                                     'ai_generate_list() { ' + shlex.quote(sys.executable) +
                                     ' child.py & wait; }\n')
    (live.root / 'scripts').mkdir()
    shutil.copyfile(ROOT / 'scripts/hanjuku_scene_generate.sh', live.root / 'scripts/hanjuku_scene_generate.sh')
    helper = '''
import json,sys,time
from pathlib import Path
from types import SimpleNamespace
from docich import hanjuku_scene_worker as worker
from docich.hanjuku_scene_process import enable_subreaper
from docich.trading import soren_output
repo,runtime,root=map(Path,sys.argv[1:])
soren_output.resolve_soren_root=lambda _g:root
enable_subreaper()
started=time.monotonic()
try:
 worker.generate(SimpleNamespace(repo_root=repo),runtime,
  {'request':{'expires_at':time.time()+20}}, {'generation_timeout_s':1.1}, 'fixture')
except worker.SceneError as error:
 print(json.dumps({'reason':error.reason,'elapsed':time.monotonic()-started}))
'''
    result = subprocess.run([sys.executable, '-c', helper, str(live.root), str(live.runtime), str(root)],
                            env={**os.environ, 'PYTHONPATH': str(ROOT / 'src')},
                            capture_output=True, text=True, timeout=7)
    assert result.returncode == 0, result.stderr
    status = json.loads(result.stdout)
    assert status['reason'] == 'timeout' and status['elapsed'] < 4
    heartbeat = root / 'heartbeat'
    before = heartbeat.stat().st_size
    time.sleep(.12)
    assert heartbeat.stat().st_size == before
    assert not list(live.runtime.glob('.scene-*'))


def test_containment_unavailable_is_visible_without_starting_provider(live, monkeypatch):
    def unsupported():
        raise worker.SceneProcessUnavailable('fixture')
    monkeypatch.setattr(worker, 'load_global', lambda *a: live.g)
    monkeypatch.setattr(worker.signal, 'signal', lambda *a: None)
    monkeypatch.setattr(worker, 'enable_subreaper', unsupported)
    monkeypatch.setattr(worker, 'OwnedSceneProcess', lambda *a, **kw: pytest.fail('unsupported launch'))
    fd = os.open(live.runtime / scene.LOCK_FILE, os.O_CREAT | os.O_RDWR, 0o600)
    assert worker.main(['--runtime', str(live.runtime), '--lock-fd', str(fd)]) == 0
    state = json.loads((live.runtime / scene.WORKER_FILE).read_text())
    assert state['status'] == 'generate_failed' and state['reason'] == 'unavailable'


def test_worker_launch_transfers_lock_and_returns_without_waiting(live):
    calls = []
    def popen(*args, **kwargs):
        calls.append((args, kwargs))
        assert kwargs['stdin'] == kwargs['stdout'] == kwargs['stderr'] == subprocess.DEVNULL
        assert kwargs['start_new_session'] is True
        fd = kwargs['pass_fds'][0]
        other = os.open(live.runtime / scene.LOCK_FILE, os.O_RDONLY)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(other)
        assert os.fstat(fd)
        return object()  # No wait/poll method: launcher must not call either.
    assert scene.start_worker(live.root, live.runtime, live.snapshot, live.cfg, popen=popen)
    assert len(calls) == 1
    assert not scene.start_worker(live.root, live.runtime, live.snapshot, live.cfg, popen=popen)


def test_worker_lock_contention_and_symlink_never_start(live):
    path = live.runtime / scene.LOCK_FILE
    with path.open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert not scene.start_worker(live.root, live.runtime, live.snapshot, live.cfg,
                                      popen=lambda *a, **k: pytest.fail())
    path.unlink()
    path.symlink_to(live.runtime / 'hanjuku_run.json')
    with pytest.raises(OSError):
        scene.start_worker(live.root, live.runtime, live.snapshot, live.cfg,
                           popen=lambda *a, **k: pytest.fail())


def test_bot_narration_failure_keeps_action_record_and_emits_no_template(live, monkeypatch):
    spec = importlib.util.spec_from_file_location('scene_writer', ROOT / 'brains/hanjuku/bot.py')
    bot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bot)
    monkeypatch.setattr(scene, 'observe', lambda *a: (_ for _ in ()).throw(RuntimeError('synthetic')))
    bot.persist(live.runtime, live.state, [OPENING], {'hanjuku': IDENTITY},
                actions=[{'type': 'pad', 'buttons': ['a'], 'hold_ms': 100}], frame_sha256='a' * 64)
    rows = [json.loads(line) for line in (live.runtime / 'hanjuku_decisions.jsonl').read_text().splitlines()]
    assert rows[0]['planned_actions'][0]['buttons'] == ['a']
    assert not (live.runtime / 'hanjuku_commentary.jsonl').exists()


def test_shell_reuses_canonical_bootstrap_and_exact_role_chain(tmp_path):
    root = tmp_path / 'soren'
    root.mkdir()
    (root / 'eloop_lib.sh').write_text('''
printf 'CANARY_BOOTSTRAP_NOT_SPEECH\\n'
BATCH_COMMENTARY_AGENTS='opencode:test-model,amd:existing-model'
AI_GENERATION_QUEUE_OWNER_PID=1
ai_generate_list() {
  [ "$AI_GENERATION_QUEUE_OWNER_PID" = "$BASHPID" ] || return 97
  printf '%s\\n' "$1|$3|$4|$AI_GENERATION_QUEUE_MAX_WAIT_SEC" > called
  printf '%s\\n' 'opencode:test-model' > "$6"
  printf '%s' '{"text":"確認済みの出来事でした。","fact_ids":["f1"]}'
}
''')
    paths = [tmp_path / key for key in ('prompt', 'output', 'last', 'failure', 'meta')]
    paths[0].write_text('test only')
    proc = subprocess.run(['bash', str(ROOT / 'scripts/hanjuku_scene_generate.sh'), str(root),
                           *(str(path) for path in paths), '10'],
                          env={**os.environ, 'DOCICH_ALLOW_REAL_AI': '1'}, capture_output=True, text=True, timeout=5)
    assert proc.returncode == 0
    assert proc.stdout == proc.stderr == ''
    assert (root / 'called').read_text().strip() == 'RADIO:hanjuku-commentary|opencode:test-model,amd:existing-model|10|10'
    assert 'CANARY' not in paths[1].read_text()
    assert json.loads(paths[4].read_text()) == {'role': 'BATCH_COMMENTARY_AGENTS'}


def test_shell_preserves_shorter_existing_operator_wait_caps(tmp_path):
    root = tmp_path / 'soren'
    root.mkdir()
    (root / 'eloop_lib.sh').write_text('''
AI_COMMON_AGENTS='opencode:fixture-only'
AI_RADIO_IMPROVE_WAIT_MAX_SEC=0
OPENCODE_RUN_LOCK_MAX_WAIT_SEC=3
AI_RADIO_MAIN_CHAIN_DEADLINE_EPOCH=1
_ai_generation_queue_max_wait_sec() { printf '2'; }
ai_generate_list() {
  printf '%s|%s|%s|%s|%s' "$AI_GENERATION_QUEUE_MAX_WAIT_SEC" \\
    "$AI_GENERATION_QUEUE_MAX_WAIT_SEC_HARD_CAP" "$AI_RADIO_IMPROVE_WAIT_MAX_SEC" \\
    "$OPENCODE_RUN_LOCK_MAX_WAIT_SEC" "$AI_RADIO_MAIN_CHAIN_DEADLINE_EPOCH" > called
  printf '%s' '{"text":"確認済みの出来事でした。","fact_ids":["f1"]}'
}
''')
    paths = [tmp_path / key for key in ('prompt', 'output', 'last', 'failure', 'meta')]
    paths[0].write_text('test only')
    result = subprocess.run(['bash', str(ROOT / 'scripts/hanjuku_scene_generate.sh'), str(root),
                             *(str(path) for path in paths), '10'],
                            env={**os.environ, 'DOCICH_ALLOW_REAL_AI': '1'}, timeout=5)
    assert result.returncode == 0
    assert (root / 'called').read_text() == '2|1|0|3|1'
    assert json.loads(paths[4].read_text()) == {'role': 'AI_COMMON_AGENTS'}


@pytest.mark.parametrize('guard', ['EXPLORE_MODE=1', 'STREAMING_ENABLED=0', 'radio_pause', 'audio_pause', 'stop'])
def test_shell_honors_existing_runtime_stop_guards(tmp_path, guard):
    root = tmp_path / 'soren'
    (root / 'tmp/state').mkdir(parents=True)
    code = 'ai_generate_list() { touch SHOULD_NOT_RUN; }\n'
    marker = None
    if guard in {'radio_pause', 'audio_pause', 'stop'}:
        name = {'radio_pause': 'tmp/state/radio_worker.paused',
                'audio_pause': 'tmp/state/audio_worker.paused', 'stop': 'tmp/stop'}[guard]
        marker = root / name
        marker.write_text('operator-fixture\n')
    else:
        code += guard + '\n'
    (root / 'eloop_lib.sh').write_text(code)
    paths = [tmp_path / key for key in ('prompt', 'output', 'last', 'failure', 'meta')]
    paths[0].write_text('test only')
    result = subprocess.run(['bash', str(ROOT / 'scripts/hanjuku_scene_generate.sh'), str(root),
                             *(str(path) for path in paths), '1'],
                            env={**os.environ, 'DOCICH_ALLOW_REAL_AI': '1'}, timeout=5)
    assert result.returncode == 3
    assert not (root / 'SHOULD_NOT_RUN').exists()
    assert paths[3].read_text().strip() == 'disabled'
    if marker is not None:
        assert marker.read_text() == 'operator-fixture\n'

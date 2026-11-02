"""A summon turn an A reached after we decided it on a dialogue page still counts.

g1075-1d5f65e9 2026-10-29 JST 18:58:48: the bot decided A on 「ウッドボールは
すばやく よけた!」 (d994da41) at 48.57. The corner captured ガートルード's skill
box sliding in at 48.71 (7df35346, snapshot frame-010.png), the A was sent at
48.87 with that frame in the journal, and であい ran. The agent's next frame was
already ウッドボール's own menu, so v312 never saw our box and the turn went
uncounted: であい ran three times on an unmoved HP before そして わかれ.
"""
import importlib.util
import json
import shutil
from pathlib import Path

from docich import hanjuku_bot
from docich.hanjuku.flows.battle_menus import MONSTER_STALL_SAME_TURN_TICKS
from docich.hanjuku.os_layer.phase import classify
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import parse

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests' / 'fixtures' / 'hanjuku'
SLIDING = FIXTURES / 'g1075-gertrude-box-sliding-overtaken.png'   # 18:58:48.71, the frame the A reached
SETTLED = FIXTURES / 'g1075-gertrude-menu-settled.png'            # 18:58:39.41, であい chosen by the bot
ENEMY_MENU = FIXTURES / 'g1075-woodball-menu.png'                 # 18:58:49.75, the agent's next frame
DODGED = FIXTURES / 'g1075-woodball-dodged-dialogue.png'          # 18:58:48.19, the A's decision frame

spec = importlib.util.spec_from_file_location('hanjuku_overtaken_turn_entry', ROOT / 'brains/hanjuku/bot.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


def digest(path):
    return read_png(path).resized().digest()


def runtime_with(tmp_path, *, sent=SLIDING, decided=DODGED, decided_phase='dialogue',
                 snapshot=SLIDING, at=100.0):
    (tmp_path / 'hanjuku_frames').mkdir(parents=True)
    if snapshot is not None:
        shutil.copyfile(snapshot, tmp_path / 'hanjuku_frames' / 'frame-010.png')
    rows = [
        {'event': 'observation', 'at': at - 0.6, 'phase': decided_phase,
         'frame_sha256': digest(decided), 'snapshot': None},
        {'event': 'observation', 'at': at - 0.2, 'phase': 'field',
         'frame_sha256': digest(sent), 'snapshot': 'frame-010.png'},
        {'event': 'input_sent', 'at': at, 'type': 'pad', 'buttons': ['a'], 'hold_ms': 100,
         'frame_sha256': digest(sent), 'decision_frame_sha256': digest(decided)},
    ]
    (tmp_path / 'hanjuku_events.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
    return tmp_path


def test_the_fixtures_read_as_measured():
    def read(path):
        frame = read_png(path).resized()
        return parse(frame, phase=classify(frame)).kind
    assert read(SLIDING) == 'monster_menu_pending'
    assert read(SETTLED) == 'monster_menu'
    assert read(ENEMY_MENU) == 'monster_menu'      # ウッドボール's own box, not ours
    assert read(DODGED) == 'text'


def test_the_journal_hands_over_the_frame_a_dialogue_a_reached(tmp_path):
    runtime = runtime_with(tmp_path)
    found = entry.overtaken_frames(runtime, {'overtaken_input_at': 99.0}, now=101.0)
    assert [(sha, frame.digest()) for sha, frame in found] == [(digest(SLIDING), digest(SLIDING))]


def test_each_input_is_taken_once_and_the_first_run_only_marks(tmp_path):
    runtime = runtime_with(tmp_path)
    state = {}
    assert entry.overtaken_frames(runtime, state, now=101.0) == []
    assert state['overtaken_input_at'] == 100.0
    state = {'overtaken_input_at': 99.0}
    assert len(entry.overtaken_frames(runtime, state, now=101.0)) == 1
    assert entry.overtaken_frames(runtime, state, now=102.0) == []


def test_inputs_decided_on_a_menu_or_landing_unchanged_are_not_taken(tmp_path):
    # Our own choice on a menu frame that redrew before the send is not a stray.
    assert entry.overtaken_frames(runtime_with(tmp_path / 'own', decided=SETTLED, decided_phase='field'),
                                  {'overtaken_input_at': 99.0}, now=101.0) == []
    assert entry.overtaken_frames(runtime_with(tmp_path / 'same', sent=DODGED, snapshot=DODGED),
                                  {'overtaken_input_at': 99.0}, now=101.0) == []


def test_a_snapshot_the_ring_has_overwritten_is_not_read_as_that_frame(tmp_path):
    runtime = runtime_with(tmp_path, snapshot=ENEMY_MENU)
    assert entry.overtaken_frames(runtime, {'overtaken_input_at': 99.0}, now=101.0) == []
    (runtime / 'hanjuku_frames' / 'frame-010.png').unlink()
    assert entry.overtaken_frames(runtime, {'overtaken_input_at': 99.0}, now=101.0) == []


def state():
    cur = {'ally': 'ゼウス', 'enemy': 'ココット', 'ally_hp': 85, 'enemy_hp': 4,
           'start_ally_hp': 85, 'start_enemy_hp': 4, 'side': 'attack',
           'cards_used': [], 'cards_selected': [], 'cards_missing': [],
           'card_evidence_version': 1, 'card_consumption_complete': True}
    return {'chapter': 4, 'policy': {'chapter': 4, 'battle': cur}, 'screen_kind': 'text'}


def run(st, *steps):
    decisions, buttons = [], []
    for step in steps:
        path, overtaken = step if isinstance(step, tuple) else (step, None)
        actions, st = hanjuku_bot.decide(read_png(path).resized(), st, overtaken_frames=overtaken)
        decisions += [r['decision'] for r in st.get('_records', [])]
        buttons.append([b for a in actions or [] for b in (a.get('buttons') or [])])
    return st, decisions, buttons


def dialogue(n=MONSTER_STALL_SAME_TURN_TICKS + 2):
    return [DODGED] * n


REACHED = [(digest(SLIDING), read_png(SLIDING).resized())]


def test_the_reached_box_counts_skill_one_for_our_summon():
    st, decisions, _ = run(state(), *dialogue(), (ENEMY_MENU, REACHED))
    assert decisions.count('monster_turn_unchosen') == 1
    stall = st['policy']['monster_stall']
    assert stall['scope'] == ['ガートルード', 'ウッドボール'] and stall['enemy_hp'] == 131
    assert stall['turns'] == {'skill1': 1}
    record = next(r for r in st['_records'] if r['decision'] == 'monster_turn_unchosen')
    assert record['observed_metric']['sent_frame_sha256'] == digest(SLIDING)


def test_the_measured_battle_switches_after_two_uses_of_deai():
    # 18:58:39 であい chosen on the settled box, 18:58:48 であい by the stray A
    # (box never decided on), so the next own turn must go down to そして わかれ.
    st, decisions, buttons = run(state(), SETTLED, *dialogue(), (ENEMY_MENU, REACHED),
                                 *dialogue(), SETTLED)
    assert decisions.count('monster_turn_unchosen') == 1
    assert 'monster_skill_stall' in decisions
    assert buttons[-1] == ['down']
    assert st['policy']['monster_stall']['turns'] == {'skill1': 2, 'skill2': 1}


def test_a_reached_box_the_agent_also_saw_leaving_counts_once():
    st, decisions, _ = run(state(), *dialogue(), (SLIDING, REACHED), ENEMY_MENU, *dialogue())
    assert decisions.count('monster_turn_unchosen') == 1
    assert st['policy']['monster_stall']['turns'] == {'skill1': 1}


def test_the_bot_entry_counts_the_turn_from_the_live_journal(tmp_path, monkeypatch):
    import io
    import sys
    import time

    identity = {'game': 'hanjuku-hero', 'runtime_id': 'g9-0123abcd', 'generation': 9, 'lease_id': 'lease-9'}
    runtime = tmp_path / 'run' / 'runtimes' / identity['runtime_id']
    sent_at = time.time() - 1
    runtime_with(runtime, at=sent_at)
    module_spec = importlib.util.spec_from_file_location('hanjuku_overtaken_turn_main', ROOT / 'brains/hanjuku/bot.py')
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    module.ROOT = tmp_path
    module.atomic_write_json(runtime / 'hanjuku_bot.json', {**state(), 'overtaken_input_at': sent_at - 5})
    monkeypatch.setattr(module, 'read_png', lambda path: read_png(ENEMY_MENU if path == Path('latest.png') else path))
    observation = {'game': 'hanjuku-hero', 'screenshot': 'latest.png',
                   'meta': {'runtime_dir': str(runtime), 'hanjuku': identity}}
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(observation)))
    monkeypatch.setattr(sys, 'stdout', io.StringIO())
    assert module.main() == 0
    decisions = [json.loads(line) for line in (runtime / 'hanjuku_decisions.jsonl').read_text().splitlines()]
    counted = [d for d in decisions if d.get('decision') == 'monster_turn_unchosen']
    assert len(counted) == 1 and counted[0]['observed_metric']['sent_frame_sha256'] == digest(SLIDING)
    saved = json.loads((runtime / 'hanjuku_bot.json').read_text())
    assert saved['overtaken_input_at'] == sent_at
    assert saved['policy']['monster_stall']['turns'] == {'skill1': 1}

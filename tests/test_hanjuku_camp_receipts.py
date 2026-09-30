"""Camp cursor and dispatch evidence; neither implies a named arrival."""
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich import hanjuku_policy as p, hanjuku_run
from docich.game_switch import atomic_write_json
from docich.hanjuku_bot import classify, decide
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import Screen, parse
from test_hanjuku_chart_bot import camp_menu

ID = {'game': 'hanjuku-hero', 'runtime_id': 'g514-8937051b', 'generation': 514, 'lease_id': 'lease-test'}


def command_bot():
    spec = importlib.util.spec_from_file_location('camp_command_bot',
        Path(__file__).resolve().parents[1] / 'brains/hanjuku/bot.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def picker():
    # Existing isolated 2026-09-29 probe image. It is NOT a g514 menu frame.
    frame = read_png(Path(__file__).parent / 'fixtures/hanjuku_recall_picker_20260929.png')
    assert frame.digest() == 'b15b5935f9af5777fc82607b84f8483457494398ce3e52a456fcdb3f60a4e5a2'
    return frame


def pending():
    frame = picker()
    state = {'policy': {'chapter': 2, 'tick': 50, 'recall': {'stage': 'dest', 'steps': 0}}}
    actions, state = decide(frame, state)
    assert actions == [p.pad('a'), {'type': 'wait', 'ms': 700}, p.pad('a')]
    assert state['policy']['recall']['goal'] == 'アルマムーン'
    return actions, state


@pytest.mark.parametrize('selected,button', [(0, 'down'), (1, 'down'), (2, 'down'), (3, 'a')])
def test_recall_menu_uses_observed_cursor_instead_of_saved_down_count(selected, button):
    mem = {'chapter': 2, '_records': [], 'recall': {'stage': 'menu', 'steps': 0, 'downs': 3}}
    assert p.camp_recall_step(camp_menu(selected), mem, None) == [p.pad(button)]
    assert mem['recall']['stage'] == ('dest' if selected == 3 else 'menu')


def test_repeated_same_cursor_never_becomes_confirmation_and_stops_finitely():
    mem = {'chapter': 2, 'tick': 100, '_records': [], 'recall': {'stage': 'menu', 'steps': 0}}
    screen = camp_menu(0)
    for _ in range(p.RECALL_LIMIT):
        assert p.camp_recall_step(screen, mem, None) == [p.pad('down')]
    assert p.camp_recall_step(screen, mem, None) == [p.pad('b')]
    assert not mem.get('recall') and mem['recall_skip']['tick'] == 100


def test_missing_hand_and_partial_menu_never_use_legacy_down_then_a():
    mem = {'chapter': 2, 'tick': 100, '_records': [], 'recall': {'stage': 'menu', 'steps': 0}}
    partial = Screen([], None, 'いどう', kind='text')
    assert p.camp_recall_step(partial, mem, None) == []
    screen = camp_menu(3); screen.hand = None
    assert p.camp_recall_step(screen, mem, None) == []
    assert p.camp_recall_step(screen, mem, None) == []
    assert p.camp_recall_step(screen, mem, None) == [p.pad('b')]
    assert not mem.get('recall')
    assert sum(r['decision'] == 'camp_menu_unread' for r in mem['_records']) == 1


def test_actual_recall_picker_keeps_proposal_without_changing_sortie_or_owner():
    frame = picker(); screen = parse(frame, phase=classify(frame))
    assert screen.kind == 'world_map'
    mem = {'chapter': 2, 'tick': 50, '_records': [],
           'sorties': {'old': {'general': p.NAME, 'status': 'en_route', 'target': 'ドミノーラ'}},
           'recall': {'stage': 'dest', 'steps': 0, 'hero': True, 'sorties': ['old']}}
    assert p.world_map_step(screen, mem, frame) == [p.pad('a'), {'type': 'wait', 'ms': 700}, p.pad('a')]
    assert mem['recall']['stage'] == 'await_dispatch'
    assert mem['sorties']['old']['status'] == 'en_route'
    assert not any(r['decision'] in ('hero_recalled', 'camp_recall') for r in mem['_records'])
    # Same picker after the proposal must not get another pair of A's or Y.
    assert p.world_map_step(screen, mem, frame) == []


def test_actual_sender_receipts_bind_request_then_leave_arrival_unconfirmed(tmp_path):
    module = command_bot(); actions, state = pending(); frame = picker()
    atomic_write_json(tmp_path / 'hanjuku_run.json', {**ID, 'frame_sha256': frame.digest()})
    module.persist(tmp_path, state, state.pop('_records'), {'hanjuku': ID},
                   actions=actions, frame_sha256=frame.digest(), frame=frame)
    atomic_write_json(tmp_path / 'hanjuku_bot.json', state)
    action = SimpleNamespace(type='pad', buttons=['a'], hold_ms=100)
    hanjuku_run.action_sent(tmp_path, ID, action)
    one = module.recall_input_receipts(tmp_path, state, {'hanjuku': ID})
    assert one['a_inputs'] == 1
    _, first = decide(frame, state, recall_inputs=one)
    assert not first['policy']['recall'].get('input_sent')
    hanjuku_run.action_sent(tmp_path, ID, action)
    receipt = module.recall_input_receipts(tmp_path, state, {'hanjuku': ID})
    assert receipt['a_inputs'] == 2
    mem = state['policy']; mem['_records'] = []; mem['_recall_inputs'] = receipt
    assert p.camp_recall_step(Screen([], None, '', kind='map'), mem, None) == []
    assert not mem.get('recall')
    assert mem['recall_verification']['input_sent'] and mem['recall_verification']['picker_closed']
    assert mem['recall_verification']['status'] == 'arrival_unconfirmed'
    assert [r['decision'] for r in mem['_records']] == ['camp_recall_input_sent', 'camp_recall_unconfirmed']


def test_receipt_reader_rejects_old_identity_frame_time_and_duplicate_lines(tmp_path):
    module = command_bot(); _, state = pending()
    trace = {**ID, 'decision_id': 'g514-8937051b:514:1', 'frame_sha256': picker().digest(), 'planned_at': 100}
    state['policy']['recall']['request_trace'] = trace
    good = {'event': 'input_sent', 'decision_id': trace['decision_id'],
            'decision_frame_sha256': trace['frame_sha256'], 'at': 101,
            'type': 'pad', 'buttons': ['a'], 'hold_ms': 100}
    bad = [{**good, 'at': 99}, {**good, 'decision_id': 'g513-old:513:1'},
           {**good, 'decision_frame_sha256': 'f'*64}, {**good, 'buttons': ['b']}]
    journal = tmp_path / 'hanjuku_events.jsonl'
    journal.write_text('\n'.join(json.dumps(row) for row in bad + [good, good]))
    assert module.recall_input_receipts(tmp_path, state, {'hanjuku': ID})['a_inputs'] == 1
    assert module.recall_input_receipts(tmp_path, state, {'hanjuku': {**ID, 'lease_id': 'old'}}) is None
    assert module.recall_input_receipts(tmp_path, state, {'hanjuku': {**ID, 'generation': 513}}) is None
    journal.write_text('x' * 70000 + '\n' + json.dumps(good))
    assert module.recall_input_receipts(tmp_path, state, {'hanjuku': ID})['a_inputs'] == 1
    journal.unlink(); journal.symlink_to(tmp_path / 'missing')
    assert module.recall_input_receipts(tmp_path, state, {'hanjuku': ID}) is None


def test_missing_dispatch_receipt_is_bounded_and_never_retries_a():
    _, state = pending(); mem = state['policy']; mem['_records'] = []
    view = parse(picker(), phase='field')
    for _ in range(5):
        assert p.world_map_step(view, mem, picker()) == []
    assert p.world_map_step(view, mem, picker()) == [p.pad('b')]
    assert not mem.get('recall')
    assert not mem['recall_verification'].get('input_sent')
    assert mem['recall_verification']['status'] == 'dispatch_unconfirmed'


@pytest.mark.parametrize('kind',['unknown','yes_no'])
def test_unclassified_return_after_recall_does_not_get_legacy_a(monkeypatch,kind):
    from docich import hanjuku_screen
    _,state=pending()
    monkeypatch.setattr(hanjuku_screen,'parse',lambda *_a,**_k:Screen([],None,'',kind=kind))
    actions,state=decide(picker(),state)
    assert actions==[] and state['policy']['recall']['stage']=='await_dispatch'


@pytest.mark.parametrize('stage',['dest','await_dispatch'])
def test_recall_target_screen_never_confirms_a_different_chart_sortie(stage):
    mem={'chapter':1,'active':'1-A2','orders':{'1-A2':'pending'},
         'order_context':{'1-A2':{'actual_general':'ゼウス'}},
         'sortie_attempt':{'step':'1-A2','target_seen':True},
         'recall':{'stage':stage},'_records':[]}
    p.observe_sortie_transition(Screen([],None,'',kind='world_map'),mem,'map_target')
    assert mem['orders']['1-A2']=='pending' and not mem.get('sorties')
    assert not mem['_records']

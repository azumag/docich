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
from docich.hanjuku.flows import recall as recall_flow
from docich.hanjuku.state.regions import flow as _flow, journal as _journal
from docich.hanjuku.state.regions import world as _world, plan as _plan_region

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
    assert state['policy']['flow']['recall']['goal'] == 'アルマムーン'
    return actions, state


# docich#5105: the menu target is always いどう (row 0; owner 2026-11-19
# 「きかん禁止・いどう→Y」), so the cursor walks up from a lower row and the
# A lands on row 0 instead of walking down to the retired きかん fallback.
@pytest.mark.parametrize('selected,button', [(0, 'a'), (1, 'up'), (2, 'up'), (3, 'up')])
def test_recall_menu_uses_observed_cursor_instead_of_saved_down_count(selected, button):
    mem = {'chapter': 2, '_records': [], 'recall': {'stage': 'menu', 'steps': 0, 'downs': 3}}
    assert p.camp_recall_step(camp_menu(selected), mem, None) == [p.pad(button)]
    assert _flow(mem)['recall']['stage'] == ('dest' if selected == 0 else 'menu')


def test_repeated_same_cursor_never_becomes_confirmation_and_stops_finitely():
    mem = {'chapter': 2, 'tick': 100, '_records': [], 'recall': {'stage': 'menu', 'steps': 0}}
    # docich#5105: the menu target is いどう (row 0), so pin the cursor on
    # row 3 (きかん) — it walks up toward いどう and never arrives on the
    # fixed fixture, then the recall cancels instead of confirming blindly.
    screen = camp_menu(3)
    for _ in range(p.RECALL_LIMIT):
        assert p.camp_recall_step(screen, mem, None) == [p.pad('up')]
    assert p.camp_recall_step(screen, mem, None) == [p.pad('b')]
    assert not _flow(mem).get('recall') and _flow(mem)['recall_skip']['tick'] == 100


def test_missing_hand_and_partial_menu_never_use_legacy_down_then_a():
    mem = {'chapter': 2, 'tick': 100, '_records': [], 'recall': {'stage': 'menu', 'steps': 0}}
    partial = Screen([], None, 'いどう', kind='text')
    assert p.camp_recall_step(partial, mem, None) == []
    screen = camp_menu(3); screen.hand = None
    assert p.camp_recall_step(screen, mem, None) == []
    assert p.camp_recall_step(screen, mem, None) == []
    assert p.camp_recall_step(screen, mem, None) == [p.pad('b')]
    assert not _flow(mem).get('recall')
    assert sum(r['decision'] == 'camp_menu_unread' for r in mem['_records']) == 1


def test_actual_recall_picker_keeps_proposal_without_changing_sortie_or_owner():
    frame = picker(); screen = parse(frame, phase=classify(frame))
    assert screen.kind == 'world_map'
    mem = {'chapter': 2, 'tick': 50, '_records': [],
           'world': {'sorties': {'old': {'general': p.NAME, 'status': 'en_route', 'target': 'ドミノーラ'}}},
           'recall': {'stage': 'dest', 'steps': 0, 'hero': True, 'sorties': ['old']}}
    assert p.world_map_step(screen, mem, frame) == [p.pad('a'), {'type': 'wait', 'ms': 700}, p.pad('a')]
    assert _flow(mem)['recall']['stage'] == 'await_dispatch'
    assert mem['world']['sorties']['old']['status'] == 'en_route'
    assert not any(r['decision'] in ('hero_recalled', 'camp_recall') for r in mem['_records'])
    # Same picker after the proposal must not get another pair of A's or Y.
    assert p.world_map_step(screen, mem, frame) == []


def test_actual_sender_receipts_bind_request_then_record_a_dispatched_return(tmp_path):
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
    assert not first['policy']['flow']['recall'].get('input_sent')
    hanjuku_run.action_sent(tmp_path, ID, action)
    receipt = module.recall_input_receipts(tmp_path, state, {'hanjuku': ID})
    assert receipt['a_inputs'] == 2
    mem = state['policy']; mem['_records'] = []; _journal(mem)['_recall_inputs'] = receipt
    assert p.camp_recall_step(Screen([], None, '', kind='map'), mem, None) == []
    assert not _flow(mem).get('recall')
    assert _flow(mem)['recall_verification']['input_sent'] and _flow(mem)['recall_verification']['picker_closed']
    # docich#4978: a closed picker is the game accepting the return. The record
    # is a dispatch, not an unconfirmed failure; the arrival read stays pending
    # in recall_verification for camp_recheck's named roster read.
    assert _flow(mem)['recall_verification']['status'] == 'dispatched'
    assert [r['decision'] for r in mem['_records']] == ['camp_recall_input_sent', 'camp_recall_dispatched']
    assert not mem.get('uncertain')


def test_unread_cursor_snapshot_uses_existing_ring_without_an_unrelated_chart_context(tmp_path):
    module=command_bot();frame=picker()
    state={'step':241,'screen_kind':'text','policy':{'active':'unrelated'}}
    record={'decision':'camp_menu_unread','chart_step':None}
    module.persist(tmp_path,state,[record],{'hanjuku':ID},actions=[],frame_sha256=frame.digest(),frame=frame)
    plan=json.loads((tmp_path/'hanjuku_decisions.jsonl').read_text().splitlines()[0])
    assert plan['chart_step'] is None and plan['snapshot']=='decision-001.png'
    assert len(list((tmp_path/'hanjuku_frames').glob('*.png')))==1


def test_receipt_reader_rejects_old_identity_frame_time_and_duplicate_lines(tmp_path):
    module = command_bot(); _, state = pending()
    trace = {**ID, 'decision_id': 'g514-8937051b:514:1', 'frame_sha256': picker().digest(), 'planned_at': 100}
    state['policy']['flow']['recall']['request_trace'] = trace
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


def test_card_scroll_receipt_is_bound_to_run_frame_and_delivered_up(tmp_path):
    module = command_bot()
    trace = {**ID, 'decision_id': 'synthetic:1', 'frame_sha256': 'a' * 64, 'planned_at': 100}
    state = {'policy': {'card_upper_boundary': {'await_input': True, 'request_trace': trace}}}
    good = {'event': 'input_sent', 'decision_id': trace['decision_id'],
            'decision_frame_sha256': trace['frame_sha256'], 'at': 101,
            'type': 'pad', 'buttons': ['up'], 'hold_ms': 100}
    bad = [{**good, 'at': 99}, {**good, 'decision_id': 'different'},
           {**good, 'decision_frame_sha256': 'b' * 64}, {**good, 'buttons': ['a']}]
    (tmp_path / 'hanjuku_events.jsonl').write_text('\n'.join(json.dumps(x) for x in bad + [good, good]))
    assert module.card_scroll_receipt(tmp_path, state, {'hanjuku': ID})['up_inputs'] == 1
    assert module.card_scroll_receipt(tmp_path, state, {'hanjuku': {**ID, 'lease_id': 'other'}}) is None


def test_missing_dispatch_receipt_demotes_to_stale_then_bounds_itself():
    _, state = pending(); mem = state['policy']; mem['_records'] = []
    _flow(mem)['camp_recall_blocked'] = {'アルマムーン': 1}
    frame = picker(); view = parse(frame, phase='field')
    for _ in range(6):
        assert p.world_map_step(view, mem, frame) == []
    # docich#5665: an empty six-observation proof window is not yet a defect —
    # journal eviction can starve it while the press was real. The wait
    # demotes to a bounded stale stage instead of a permanent unconfirmed
    # hold, and keeps owning the picker screens while it retries the proof.
    recall = _flow(mem)['recall']
    assert recall['stage'] == 'await_dispatch_stale'
    stale = next(row for row in mem['_records']
                 if row['decision'] == 'camp_recall_dispatch_stale')
    assert stale['resulting_event'] == 'dispatch_proof_window_exhausted'
    assert stale['observed_metric']['steps'] == 6
    for _ in range(11):
        assert p.world_map_step(view, mem, frame) == []
    assert p.world_map_step(view, mem, frame) == [p.pad('b')]
    assert not _flow(mem).get('recall')
    assert not _flow(mem)['recall_verification'].get('input_sent')
    assert _flow(mem)['recall_verification']['status'] == 'dispatch_unconfirmed'
    record = next(row for row in mem['_records']
                  if row['decision'] == 'camp_recall_unconfirmed')
    assert record['resulting_event'] == 'dispatch_unconfirmed'
    assert '到着' not in record['reason']
    assert record['observed_metric']['stage'] == 'await_dispatch_stale'
    assert record['observed_metric']['stale_steps'] == 12
    assert record['observed_metric']['unsent_proven'] is False
    assert record['observed_metric']['resends'] == 0
    # The A was never receipt-confirmed, so the selected castle was not
    # contacted and must not be learned as a refusing destination.
    assert _flow(mem)['camp_recall_blocked'] == {'アルマムーン': 1}


def test_a_late_dispatch_receipt_resumes_the_stale_wait_as_a_fresh_window():
    _, state = pending(); mem = state['policy']; mem['_records'] = []
    frame = picker(); view = parse(frame, phase='field')
    for _ in range(6):
        assert p.world_map_step(view, mem, frame) == []
    recall = _flow(mem)['recall']
    assert recall['stage'] == 'await_dispatch_stale'
    # The send receipt lands in the journal after the demotion: the press was
    # real, so the ordinary acceptance check resumes on a fresh count instead
    # of the stale bound filing unconfirmed.
    trace = {**ID, 'decision_id': 'g514-8937051b:514:7',
             'frame_sha256': frame.digest(), 'planned_at': 100}
    recall['request_trace'] = trace
    _journal(mem)['_recall_inputs'] = {'request_trace': trace, 'a_inputs': 2}
    assert p.world_map_step(view, mem, frame) == []
    assert recall['stage'] == 'await_dispatch' and recall['input_sent']
    assert recall['steps'] == 0 and recall['stale_steps'] == 1
    resumed = next(row for row in mem['_records']
                   if row['decision'] == 'camp_recall_dispatch_resumed')
    assert resumed['resulting_event'] == 'dispatch_receipt_confirmed'
    # The picker dropping back to the field map now accepts the dispatch.
    assert p.camp_recall_step(Screen([], None, '', kind='map'), mem, None) == []
    assert _flow(mem)['recall_verification']['status'] == 'dispatched'
    assert not any(r['decision'] == 'camp_recall_unconfirmed' for r in mem['_records'])


def test_a_proven_unsent_dispatch_resends_once_then_still_bounds_itself():
    _, state = pending(); mem = state['policy']; mem['_records'] = []
    frame = picker(); view = parse(frame, phase='field')
    recall = _flow(mem)['recall']
    trace = {**ID, 'decision_id': 'g514-8937051b:514:9',
             'frame_sha256': frame.digest(), 'planned_at': 100}
    recall['request_trace'] = trace
    for _ in range(6):
        assert p.world_map_step(view, mem, frame) == []
    assert recall['stage'] == 'await_dispatch_stale'
    # The sender journaled other inputs after this request but not our A —
    # unsent is proven, and the destination picker is still on screen, so the
    # wait backfills one A under a fresh trace instead of filing unconfirmed.
    _journal(mem)['_recall_inputs'] = {'request_trace': trace, 'a_inputs': 0,
                                       'later_inputs': True}
    assert p.world_map_step(view, mem, frame) == [p.pad('a')]
    assert recall['stage'] == 'await_dispatch' and recall['resends'] == 1
    assert 'request_trace' not in recall and not recall.get('input_sent')
    # The resent batch never reaches a journal in this fixture either. The
    # wait demotes a second time — without request_trace nothing is proven —
    # and the stale bound ends it without a second A.
    for _ in range(6):
        assert p.world_map_step(view, mem, frame) == []
    for _ in range(10):
        assert p.world_map_step(view, mem, frame) == []
    assert p.world_map_step(view, mem, frame) == [p.pad('b')]
    assert _flow(mem)['recall_verification']['status'] == 'dispatch_unconfirmed'
    assert sum(r['decision'] == 'camp_recall_dispatch_resent'
               for r in mem['_records']) == 1
    record = next(row for row in mem['_records']
                  if row['decision'] == 'camp_recall_unconfirmed')
    assert record['observed_metric']['resends'] == 1


def scene_interrupted_stale_recall(target_cell=(144, 127)):
    trace = {'decision_id': 'old-request'}
    recall = {'stage': 'await_dispatch_stale', 'steps': 0, 'stale_steps': 6,
              'stale_since_tick': 104, 'target': [96, 111], 'goal': 'オオキク',
              'a_inputs': 1, 'requested_tick': 90, 'request_trace': trace,
              'scene_pending': True, 'scene_covered': True, 'scene_covered_tick': 100}
    if target_cell is not None:
        recall['target_cell'] = list(target_cell)
    mem = {'chapter': 9, 'tick': 110,
           'flow': {'recall': recall, 'recall_interrupted': 100},
           '_records': []}
    _journal(mem)['_recall_inputs'] = {'request_trace': trace, 'a_inputs': 0,
                                       'later_inputs': True}
    return mem, recall


def test_scene_interrupted_resend_is_a_fresh_request_and_accepts_picker_close():
    mem, recall = scene_interrupted_stale_recall()
    picker_screen = Screen([], None, '', kind='map_target', marker=(144, 127))
    assert recall_flow._recall_dispatch_step(picker_screen, mem, recall) == [p.pad('a')]
    assert recall['stage'] == 'await_dispatch' and recall['requested_tick'] == 110
    assert recall['resends'] == 1
    assert 'request_trace' not in recall and not recall.get('input_sent')
    assert 'recall_interrupted' not in _flow(mem)
    for key in ('scene_covered', 'scene_covered_tick', 'scene_pending'):
        assert key not in recall

    new_trace = {'decision_id': 'resent-request'}
    recall['request_trace'] = new_trace
    _journal(mem)['_recall_inputs'] = {'request_trace': new_trace, 'a_inputs': 1}
    mem['tick'] = 111
    assert recall_flow._recall_dispatch_step(Screen([], None, '', kind='map'), mem, recall) == []
    verification = _flow(mem)['recall_verification']
    assert verification['status'] == 'dispatched'
    assert verification['input_sent'] is True and verification['picker_closed'] is True
    assert verification['interrupted'] is False and verification['scene_covered'] is False
    decisions = [row['decision'] for row in mem['_records']]
    assert 'camp_recall_dispatch_resent' in decisions
    assert 'camp_recall_input_sent' in decisions
    assert 'camp_recall_dispatched' in decisions
    assert 'camp_recall_unconfirmed' not in decisions


@pytest.mark.parametrize('target_cell,marker', [
    (None, (144, 127)),
    ((144, 127), (152, 127)),
])
def test_scene_interrupted_resend_is_rejected_when_picker_identity_is_unconfirmed(target_cell, marker):
    mem, recall = scene_interrupted_stale_recall(target_cell=target_cell)
    picker_screen = Screen([], None, '', kind='map_target', marker=marker)
    assert recall_flow._recall_dispatch_step(picker_screen, mem, recall) == [p.pad('b')]
    assert 'recall' not in _flow(mem)
    assert not any(row['decision'] == 'camp_recall_dispatch_resent' for row in mem['_records'])
    rejected = next(row for row in mem['_records']
                    if row['decision'] == 'camp_recall_dispatch_resend_rejected')
    assert rejected['resulting_event'] == 'picker_identity_unconfirmed'
    verification = _flow(mem)['recall_verification']
    assert verification['status'] == 'dispatch_unconfirmed'
    assert verification['interrupted'] is True
    terminal = next(row for row in mem['_records']
                    if row['decision'] == 'camp_recall_unconfirmed')
    assert terminal['observed_metric']['unsent_proven'] is True
    assert terminal['observed_metric']['interrupted'] is True


def test_action_sent_mirrors_the_receipt_into_the_input_journal(tmp_path):
    frame = picker()
    atomic_write_json(tmp_path / 'hanjuku_run.json', {**ID, 'frame_sha256': frame.digest()})
    atomic_write_json(tmp_path / 'hanjuku_bot.json',
                      {'decision_trace': {**ID, 'decision_id': 'g514-8937051b:514:1',
                                          'frame_sha256': frame.digest()}})
    hanjuku_run.action_sent(tmp_path, ID,
                            SimpleNamespace(type='pad', buttons=['a'], hold_ms=100))
    mirror = [json.loads(row) for row in
              (tmp_path / 'hanjuku_inputs_sent.jsonl').read_text().splitlines()]
    events = [json.loads(row) for row in
              (tmp_path / 'hanjuku_events.jsonl').read_text().splitlines()]
    assert len(mirror) == len(events) == 1 and mirror == events
    row = mirror[0]
    assert (row['event'], row['type'], row['buttons']) == ('input_sent', 'pad', ['a'])
    assert row['decision_id'] == 'g514-8937051b:514:1'
    assert row['decision_frame_sha256'] == frame.digest()


def test_the_input_mirror_outlives_a_dense_events_tail_and_proves_unsent(tmp_path):
    module = command_bot(); _, state = pending()
    trace = {**ID, 'decision_id': 'g514-8937051b:514:1',
             'frame_sha256': picker().digest(), 'planned_at': 100}
    state['policy']['flow']['recall']['request_trace'] = trace
    good = {'event': 'input_sent', 'decision_id': trace['decision_id'],
            'decision_frame_sha256': trace['frame_sha256'], 'at': 101,
            'type': 'pad', 'buttons': ['a'], 'hold_ms': 100}
    # The events tail was flooded past its 64 KiB proof window; the sparse
    # input-only mirror still carries the matching send row.
    (tmp_path / 'hanjuku_events.jsonl').write_text('x' * 70000)
    (tmp_path / 'hanjuku_inputs_sent.jsonl').write_text(json.dumps(good))
    one = module.recall_input_receipts(tmp_path, state, {'hanjuku': ID})
    assert one['a_inputs'] == 1 and one['later_inputs'] is False
    # Inputs journaled at/after this request without a matching row prove the
    # batch never left — distinct from an unreadable journal (unknown).
    later = {**good, 'decision_id': 'g514-8937051b:514:2', 'at': 102}
    (tmp_path / 'hanjuku_inputs_sent.jsonl').write_text(json.dumps(later))
    two = module.recall_input_receipts(tmp_path, state, {'hanjuku': ID})
    assert two['a_inputs'] == 0 and two['later_inputs'] is True
    # The stale stage keeps asking for the same receipt while it retries.
    state['policy']['flow']['recall']['stage'] = 'await_dispatch_stale'
    assert module.recall_input_receipts(tmp_path, state, {'hanjuku': ID}) == two
    # No readable journal is unknown, never unsent.
    (tmp_path / 'hanjuku_inputs_sent.jsonl').unlink()
    (tmp_path / 'hanjuku_events.jsonl').unlink()
    assert module.recall_input_receipts(tmp_path, state, {'hanjuku': ID}) is None


@pytest.mark.parametrize('kind',['unknown','yes_no'])
def test_unclassified_return_after_recall_does_not_get_legacy_a(monkeypatch,kind):
    from docich import hanjuku_screen
    _,state=pending()
    monkeypatch.setattr(hanjuku_screen,'parse',lambda *_a,**_k:Screen([],None,'',kind=kind))
    actions,state=decide(picker(),state)
    assert actions==[] and state['policy']['flow']['recall']['stage']=='await_dispatch'


@pytest.mark.parametrize('stage',['dest','await_dispatch'])
def test_recall_target_screen_never_confirms_a_different_chart_sortie(stage):
    mem={'chapter':1,'active':'1-A2','orders':{'1-A2':'pending'},
         'order_context':{'1-A2':{'actual_general':'ゼウス'}},
         'sortie_attempt':{'step':'1-A2','target_seen':True},
         'recall':{'stage':stage},'_records':[]}
    p.observe_sortie_transition(Screen([],None,'',kind='world_map'),mem,'map_target')
    assert _plan_region(mem)['orders']['1-A2']=='pending' and not _world(mem).get('sorties')
    assert not mem['_records']


def _verified_memory(tick=1600, dispatched=900, location='camp', reread=1500):
    mem = {'tick': tick, '_records': [],
           'general_statuses': {'statuses': {
               'ナストリーニ': {'location': location, 'hp': 66, 'tick': reread}}}}
    _flow(mem)['camp_verify'] = {'tick': dispatched, 'castle': 'アルマムーン',
                                 'camp': [132, 120], 'generals': ['ナストリーニ']}
    return mem


def test_a_camped_general_still_camped_after_the_wait_is_named():
    from docich.hanjuku.flows import recall as recall_flow
    mem = _verified_memory()
    assert recall_flow.camp_arrival_check(mem) is True
    decisions = [r['decision'] for r in mem['_records']]
    assert 'camp_recall_arrival_timeout' in decisions
    metric = [r for r in mem['_records'] if r['decision'] == 'camp_recall_arrival_timeout'][0]['observed_metric']
    assert metric['still_camped'] == ['ナストリーニ']
    assert _flow(mem).get('recall_goal_failures')['132,120']['アルマムーン'] == 1
    assert 'camp_verify' not in _flow(mem)


def test_an_unread_status_keeps_the_arrival_check_pending():
    from docich.hanjuku.flows import recall as recall_flow
    mem = _verified_memory()
    mem['general_statuses']['statuses']['ナストリーニ']['tick'] = 850   # older than dispatch
    assert recall_flow.camp_arrival_check(mem) is None
    assert 'camp_verify' in _flow(mem)
    assert not any(r['decision'] == 'camp_recall_arrival_timeout' for r in mem['_records'])


def test_a_general_that_left_the_camp_confirms_the_arrival():
    from docich.hanjuku.flows import recall as recall_flow
    mem = _verified_memory(location='castle')
    assert recall_flow.camp_arrival_check(mem) is True
    assert any(r['decision'] == 'camp_recall_arrival_confirmed' for r in mem['_records'])
    assert 'camp_verify' not in _flow(mem)


def test_cliff_escape_request_is_not_a_camp_recall_or_arrival_read():
    from docich.hanjuku.flows import recall as recall_flow

    mem = {'chapter': 9, 'tick': 120, '_records': []}
    state = {'target': [134, 120], 'kind': 'camp'}
    actions = [p.pad('a')]
    assert recall_flow._request_recall(
        mem, state, 'アルマムーン', actions,
        escape_move=True, target_cell=[135, 121]) == actions
    assert 'roster_read_due' not in mem
    assert [row['decision'] for row in mem['_records']] == [
        'camp_escape_move_requested']
    row = mem['_records'][0]
    assert row['resulting_event'] == 'field_move_requested'
    assert row['observed_metric']['escape_move'] is True
    assert row['observed_metric']['target_cell'] == [135, 121]


@pytest.mark.parametrize('goal', [None, 'アルマムーン'])
def test_cliff_escape_dispatch_unconfirmed_is_not_a_camp_recall(goal):
    from docich.hanjuku.flows import recall as recall_flow

    mem = {'chapter': 9, 'tick': 120, '_records': []}
    state = {'stage': 'await_dispatch', 'steps': 5, 'goal': goal,
             'target': [134, 120], 'target_cell': [135, 121],
             'kind': 'camp', 'escape_move': True, 'a_inputs': 1}
    assert recall_flow._recall_dispatch_step(
        Screen([], None, '', kind='text'), mem, state) == []
    assert [row['decision'] for row in mem['_records']] == [
        'camp_escape_move_unconfirmed']
    row = mem['_records'][0]
    assert row['resulting_event'] == 'field_move_unconfirmed'
    assert row['observed_metric']['escape_move'] is True
    assert row['observed_metric']['camp'] == [134, 120]
    assert row['observed_metric']['target_cell'] == [135, 121]
    assert row['observed_metric']['stage'] == 'await_dispatch'
    assert row['observed_metric']['steps'] == 6
    assert row['observed_metric']['input_receipt_confirmed'] is False
    flow = _flow(mem)
    assert flow['escape_move_verification']['status'] == 'dispatch_unconfirmed'
    assert 'recall_verification' not in flow
    assert 'camp_recall_blocked' not in flow
    assert mem['uncertain'] is True


def test_cliff_escape_dispatch_accepted_is_not_a_camp_arrival_proof():
    from docich.hanjuku.flows import recall as recall_flow

    mem = {'chapter': 9, 'tick': 120, '_records': []}
    state = {'stage': 'await_dispatch', 'steps': 0, 'goal': None,
             'target': [134, 120], 'kind': 'camp', 'escape_move': True,
             'a_inputs': 1, 'input_sent': True}
    assert recall_flow._recall_dispatch_step(
        Screen([], None, '', kind='map'), mem, state) == []
    assert [row['decision'] for row in mem['_records']] == [
        'camp_escape_move_dispatched']
    assert mem['_records'][0]['resulting_event'] == 'field_move_accepted'
    assert mem['_records'][0]['observed_metric']['input_receipt_confirmed'] is True
    flow = _flow(mem)
    assert flow['escape_move_verification']['status'] == 'dispatched'
    assert 'recall_verification' not in flow
    assert 'camp_verify' not in flow
    assert not mem.get('uncertain')


@pytest.mark.parametrize('decision', [
    'camp_escape_move_requested', 'camp_escape_move_dispatched',
    'camp_escape_move_unconfirmed'])
def test_cliff_escape_decisions_keep_their_frame_and_input_context(tmp_path, decision):
    module = command_bot()
    frame = picker()
    state = {'step': 242, 'screen_kind': 'text',
             'policy': {'active': 'unrelated'}}
    record = {'decision': decision, 'chart_step': 'camp_escape',
              'strategy_variant': 'escape_move',
              'deviation_reason': 'cliff_exit'}
    module.persist(tmp_path, state, [record], {'hanjuku': ID},
                   actions=[], frame_sha256=frame.digest(), frame=frame)
    plan = json.loads((tmp_path / 'hanjuku_decisions.jsonl').read_text().splitlines()[0])
    assert plan['chart_step'] == 'camp_escape'
    assert plan['strategy_variant'] == 'escape_move'
    assert plan['deviation_reason'] == 'cliff_exit'
    assert plan['snapshot'] == 'decision-002.png'
    assert (tmp_path / 'hanjuku_frames' / 'decision-002.png').exists()

"""docich#5086: a foreign sortie procedure must not settle a recall wait.

g1190 2026-11-21 ch3: while a sortie procedure owned the field, the recall's
await-dispatch wait kept reading the sortie's map frames. Its A never
journaled, the picker never appeared, and the terminal misfiled the wait as
``camp_recall_unconfirmed``. A wait that ends under a foreign departure
request or a live sortie confirmation is a preemption: retried soon like a
scene interruption, not filed as the recall's own unconfirmed dispatch.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich.hanjuku.os_layer.commands import pad
from docich.hanjuku.os_layer.screen import Screen
from docich.hanjuku.flows import recall as recall_flow
from docich.hanjuku.state.regions import flow as _flow

FIELD = Screen([], None, '', kind='map')
WORLD = Screen([], None, '', kind='world_map')


def recall_mem(tick=1000):
    mem = {'chapter': 9, 'tick': tick, '_records': []}
    _flow(mem)['recall'] = {'stage': 'await_dispatch_stale', 'steps': 5,
                            'stale_steps': recall_flow.DISPATCH_STALE_STEP_LIMIT - 1,
                            'goal': 'アルマムーン', 'target': [280, 176],
                            'general': 'リーキ', 'a_inputs': 1}
    return mem


def dispatch(mem, screen=FIELD):
    return recall_flow._recall_dispatch_step(screen, mem, _flow(mem)['recall'])


def decisions(mem):
    return [r['decision'] for r in mem['_records']]


def test_a_departure_request_turns_the_dispatch_wait_into_a_preemption():
    mem = recall_mem()
    _flow(mem)['departure_request'] = {'step': 'A:run:M1'}
    assert dispatch(mem) == []
    assert _flow(mem)['recall_verification']['status'] == 'dispatch_preempted'
    assert 'recall' not in _flow(mem)
    # Same marker a scene cut leaves: retried soon, never a refused castle.
    assert _flow(mem)['recall_interrupted'] == 1000
    assert decisions(mem) == ['camp_recall_preempted']
    assert mem['uncertain'] is True


def test_a_live_sortie_confirmation_owns_the_wait_frames():
    mem = recall_mem(tick=1000)
    _flow(mem)['sortie_attempt'] = {'step': 'A:run:M1', 'confirmation_tick': 900}
    dispatch(mem)
    assert _flow(mem)['recall_verification']['status'] == 'dispatch_preempted'
    assert 'camp_recall_preempted' in decisions(mem)


def test_a_stale_sortie_attempt_no_longer_owns_the_wait():
    mem = recall_mem(tick=1000)
    _flow(mem)['sortie_attempt'] = {'step': 'A:run:M1', 'confirmation_tick': 100}
    dispatch(mem)
    assert _flow(mem)['recall_verification']['status'] == 'dispatch_unconfirmed'
    assert 'camp_recall_unconfirmed' in decisions(mem)
    assert 'camp_recall_preempted' not in decisions(mem)


def test_an_interrupted_sortie_attempt_does_not_own_the_wait():
    mem = recall_mem(tick=1000)
    _flow(mem)['sortie_attempt'] = {'step': 'A:run:M1', 'tick': 900, 'interrupted': True}
    dispatch(mem)
    assert _flow(mem)['recall_verification']['status'] == 'dispatch_unconfirmed'


def test_an_unowned_wait_is_still_an_unconfirmed_dispatch():
    mem = recall_mem()
    dispatch(mem)
    assert _flow(mem)['recall_verification']['status'] == 'dispatch_unconfirmed'
    assert 'camp_recall_unconfirmed' in decisions(mem)
    assert 'camp_recall_preempted' not in decisions(mem)


def test_a_sent_recall_reads_arrival_not_preemption():
    # The recall's own A is journaled: the foreign procedure may still own the
    # field, but a sent dispatch is answered by its own picker, not refiled.
    mem = recall_mem()
    _flow(mem)['recall'].update(stage='await_dispatch', input_sent=True)
    _flow(mem)['departure_request'] = {'step': 'A:run:M1'}
    assert dispatch(mem, WORLD) == [pad('b')]
    assert _flow(mem)['recall_verification']['status'] == 'arrival_unconfirmed'
    assert 'camp_recall_preempted' not in decisions(mem)

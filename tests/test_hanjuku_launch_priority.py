"""docich#5702: launch priority lasts from order_start until real confirmation."""
from docich.hanjuku.strategy.orders import (active_sortie_launch_priority,
                                            arm_sortie_launch_priority)
from docich.hanjuku.state.regions import flow as _flow, plan as _plan_region

STEP = 'A:3:M1'
ORDER = {'step': STEP, 'purpose': 'retake', 'source': 'ヘリオポリス',
         'target': 'ラグナグ', 'general': 'デュオニソス', 'cards': [], 'note': '奪還'}


def memory():
    mem = {'chapter': 12, 'month': '25-9'}
    _plan_region(mem)['chart_plan'] = {'orders': [ORDER]}
    _plan_region(mem)['orders'] = {STEP: 'pending'}
    return mem


def test_order_start_keeps_priority_before_the_departure_commit():
    mem = memory()
    arm_sortie_launch_priority(mem, ORDER)
    _plan_region(mem)['active'] = STEP
    assert active_sortie_launch_priority(mem) == STEP


def test_priority_clears_when_the_attempt_is_no_longer_in_flight():
    mem = memory()
    arm_sortie_launch_priority(mem, ORDER)
    assert active_sortie_launch_priority(mem) is None
    assert 'sortie_launch_pending' not in mem


def test_confirmed_or_settled_probe_does_not_defer_scans_forever():
    mem = memory()
    arm_sortie_launch_priority(mem, ORDER)
    _plan_region(mem)['orders'][STEP] = 'launched'
    _flow(mem)['launch_probe'] = {STEP: {'general': ORDER['general'], 'source': ORDER['source'],
                                         'target': ORDER['target'], 'kept': True}}
    assert active_sortie_launch_priority(mem) is None
    assert 'sortie_launch_pending' not in mem


def test_an_unconfirmed_probe_still_owns_the_priority_window():
    mem = memory()
    arm_sortie_launch_priority(mem, ORDER)
    _plan_region(mem)['orders'][STEP] = 'launched'
    _flow(mem)['launch_probe'] = {STEP: {'general': ORDER['general'], 'source': ORDER['source'],
                                         'target': ORDER['target'], 'reads': 1}}
    assert active_sortie_launch_priority(mem) == STEP


def test_read_only_and_move_orders_do_not_arm_launch_priority():
    mem = memory()
    move = dict(ORDER, purpose='move')
    probe = dict(ORDER, roster_probe=True)
    arm_sortie_launch_priority(mem, move)
    arm_sortie_launch_priority(mem, probe)
    assert 'sortie_launch_pending' not in mem

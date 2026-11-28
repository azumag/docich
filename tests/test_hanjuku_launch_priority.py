"""docich#5702: launch priority lasts from order_start until real confirmation."""
from docich import hanjuku_chart as chart
from docich.hanjuku.strategy import replan
from docich.hanjuku.strategy.orders import (active_sortie_launch_priority,
                                            arm_sortie_launch_priority,
                                            clear_sortie_launch_priority)
from docich.hanjuku.state.regions import flow as _flow, plan as _plan_region, world as _world

STEP = 'A:3:M1'
ORDER = {'step': STEP, 'purpose': 'retake', 'source': 'ヘリオポリス',
         'target': 'ラグナグ', 'general': 'デュオニソス', 'cards': [], 'note': '奪還'}


def memory():
    mem = {'chapter': 12, 'month': '25-9'}
    _plan_region(mem)['chart_plan'] = {'orders': [ORDER]}
    _plan_region(mem)['orders'] = {STEP: 'pending'}
    return mem


def ch12_mechanical_memory():
    chapter = 12
    home = chart.home_castle(chapter)
    normal = [c for c in chart.castles(chapter) if not chart.is_boss_castle(c) and c != home]
    return {'chapter': chapter, 'captured': normal, 'tick': 1000,
            '_records': [], 'plan': {'orders': {}},
            'world': {'garrison': {}, 'garrison_tick': {}, 'sorties': {}}}


def test_ch12_mechanical_boss_order_owns_the_priority_window():
    mem = ch12_mechanical_memory()
    assert replan._mechanical_boss_plan(mem, 'rid') is True
    order = next(o for o in mem['plan']['chart_plan']['orders'] if o['step'] == '12-B1')
    arm_sortie_launch_priority(mem, order)
    _plan_region(mem)['active'] = '12-B1'
    assert active_sortie_launch_priority(mem) == '12-B1'


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
    mem['tick'] = 1000
    arm_sortie_launch_priority(mem, ORDER)
    _plan_region(mem)['orders'][STEP] = 'launched'
    _world(mem)['sorties'] = {STEP: {'general': ORDER['general'], 'source': ORDER['source'],
                                     'target': ORDER['target'], 'status': 'en_route',
                                     'tick': 810, 'confirmation_tick': 800}}
    _flow(mem)['launch_probe'] = {STEP: {'general': ORDER['general'], 'source': ORDER['source'],
                                         'target': ORDER['target'], 'reads': 1,
                                         'confirmation_tick': 800}}
    assert active_sortie_launch_priority(mem) == STEP


def test_an_interrupted_attempt_does_not_make_a_retry_pending_step_in_flight():
    mem = memory()
    arm_sortie_launch_priority(mem, ORDER)
    _world(mem)['launched_orders'] = {STEP: dict(ORDER)}
    _flow(mem)['sortie_attempt'] = {'step': STEP, 'target_seen': True,
                                    'confirmation_tick': 800, 'interrupted': True}
    assert active_sortie_launch_priority(mem) is None
    assert 'sortie_launch_pending' not in mem


def test_stale_probe_cleanup_preserves_an_active_retry_priority():
    mem = memory()
    arm_sortie_launch_priority(mem, ORDER)
    _plan_region(mem)['active'] = STEP
    _flow(mem)['launch_probe'] = {STEP: {'general': ORDER['general'], 'source': ORDER['source'],
                                         'target': ORDER['target'], 'reads': 1,
                                         'confirmation_tick': 800}}
    clear_sortie_launch_priority(mem, STEP)
    assert mem.get('sortie_launch_pending') == STEP
    assert STEP not in (_flow(mem).get('launch_probe') or {})


def test_a_probe_whose_sortie_already_settled_does_not_own_the_priority_window():
    mem = memory()
    mem['tick'] = 1000
    arm_sortie_launch_priority(mem, ORDER)
    _world(mem)['launched_orders'] = {STEP: dict(ORDER)}
    _flow(mem)['launch_probe'] = {STEP: {'general': ORDER['general'], 'source': ORDER['source'],
                                         'target': ORDER['target'], 'reads': 1,
                                         'confirmation_tick': 800}}
    assert active_sortie_launch_priority(mem) is None
    assert 'sortie_launch_pending' not in mem
    assert STEP not in (_flow(mem).get('launch_probe') or {})


def test_a_probe_from_another_departure_does_not_own_the_priority_window():
    mem = memory()
    mem['tick'] = 1000
    arm_sortie_launch_priority(mem, ORDER)
    _plan_region(mem)['orders'][STEP] = 'pending'
    _world(mem)['sorties'] = {STEP: {'general': 'シナモン', 'source': ORDER['source'],
                                     'target': ORDER['target'], 'status': 'en_route',
                                     'tick': 900, 'confirmation_tick': 900}}
    _flow(mem)['launch_probe'] = {STEP: {'general': ORDER['general'], 'source': ORDER['source'],
                                         'target': ORDER['target'], 'reads': 1,
                                         'confirmation_tick': 800}}
    assert active_sortie_launch_priority(mem) is None
    assert 'sortie_launch_pending' not in mem
    assert STEP not in (_flow(mem).get('launch_probe') or {})


def test_read_only_and_move_orders_do_not_arm_launch_priority():
    mem = memory()
    move = dict(ORDER, purpose='move')
    probe = dict(ORDER, roster_probe=True)
    arm_sortie_launch_priority(mem, move)
    arm_sortie_launch_priority(mem, probe)
    assert 'sortie_launch_pending' not in mem

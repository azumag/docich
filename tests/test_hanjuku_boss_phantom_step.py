"""A boss order launched without a march record can never finish on its own.

g1163 chapter 6 2026-11-09 12:47: A:bf8c3dee:M256898 stayed 'launched' with no
sortie while every other boss wave waited behind the reserved target. The
overdue relaunch and the expiry both work on a sortie, so nothing could ever
re-open the step.
"""
from docich import hanjuku_chart as chart
from docich.hanjuku.strategy import orders as O

BOSS = chart.boss_castle(6)


def memory(tick=20000, started=10000, status='launched', sorties=None, plan=None):
    order = {'step': 'A:x:M1', 'general': 'エピィ', 'source': 'オケアナ', 'target': BOSS}
    mem = {'chapter': 6, 'tick': tick, '_records': [],
           'orders': {'A:x:M1': status},
           'order_start_tick': {'A:x:M1': started},
           'sorties': sorties if sorties is not None else {},
           'chart_plan': {'orders': plan if plan is not None else [order]}}
    return mem


def decisions(mem, name):
    return [r for r in mem.get('_records', []) if r['decision'] == name]


def test_a_launch_without_a_march_record_reopens():
    mem = memory()
    O._rearm_phantom_boss_steps(mem)
    assert _plan_region(mem)['orders']['A:x:M1'] == 'pending'
    assert decisions(mem, 'boss_step_rearmed')
    assert mem['boss_step_rearm']['A:x:M1'] == 20000


def test_a_live_march_or_fresh_step_is_left_alone():
    live = memory(sorties={'A:x:M1': {'general': 'エピィ', 'target': BOSS,
                                      'status': 'en_route', 'tick': 19900}})
    O._rearm_phantom_boss_steps(live)
    assert live['orders']['A:x:M1'] == 'launched'
    fresh = memory(started=19999)
    O._rearm_phantom_boss_steps(fresh)
    assert fresh['orders']['A:x:M1'] == 'launched'


def test_the_rearm_is_rate_limited_per_step():
    mem = memory()
    O._rearm_phantom_boss_steps(mem)
    _plan_region(mem)['orders']['A:x:M1'] = 'launched'          # a stale plan re-marked it
    O._rearm_phantom_boss_steps(mem)
    assert _plan_region(mem)['orders']['A:x:M1'] == 'launched'  # inside the cooldown
    mem['tick'] += O.BOSS_STEP_REARM_COOLDOWN + 1
    O._rearm_phantom_boss_steps(mem)
    assert _plan_region(mem)['orders']['A:x:M1'] == 'pending'


def test_a_normal_castle_step_is_not_touched():
    mem = memory(plan=[{'step': 'A:x:M1', 'general': 'エピィ', 'source': 'オケアナ',
                        'target': 'セレーネ'}])
    O._rearm_phantom_boss_steps(mem)
    assert _plan_region(mem)['orders']['A:x:M1'] == 'launched'

"""A 四季王 step whose march record expired must run again (g1163 2026-11-09).

The live memory had ``orders['4-B1'] = orders['4-B2'] = 'launched'`` with an
empty ``sorties`` map: the records had expired, so nothing could bind an entry
and no overdue path could re-arm the step. ``next_order`` returned None while
南東 was the only live site, and the bot idled off-chart instead of attacking
the last king.
"""
from docich import hanjuku_chart as chart
from docich.hanjuku.strategy import orders
from docich.hanjuku.state.regions import plan as _plan_region

SITES = chart.boss_sites(4)
SOUTH_EAST = '四季王城(南東)'


def site_order(site=SOUTH_EAST):
    return next(o for o in chart.orders(4) if o['target'] == site)


def memory(**extra):
    order = site_order()
    # Only the site step is pending work; every other charted step is spent,
    # so next_order has nothing else to return.
    orders_state = {o['step']: 'failed' for o in chart.orders(4)
                    if o['step'] != order['step']}
    orders_state[order['step']] = 'launched'
    return {'chapter': 4, 'tick': 2000,
            'captured': list(chart.CASTLE_NAMES[4]),
            'orders': orders_state,
            'order_start_tick': {order['step']: 100},
            'sorties': {},
            'site_step_rearm': {},
            **extra}


def test_a_site_step_without_a_march_record_is_rearmed_and_runs():
    mem = memory()
    order = site_order()
    result = orders.next_order(mem)
    assert result is not None and result['step'] == order['step']
    assert result['target'] == SOUTH_EAST
    assert _plan_region(mem)['orders'][order['step']] == 'pending'
    assert mem['site_step_rearm'][order['step']] == mem['tick']
    assert 'site_step_rearmed' in [r['decision'] for r in (mem.get('_records') or [])]


def test_a_live_march_record_keeps_the_step_launched():
    mem = memory()
    order = site_order()
    mem['sorties'] = {order['step']: {'status': 'en_route', 'tick': mem['tick'] - 5,
                                      'target': SOUTH_EAST, 'general': 'ジェラート'}}
    orders.next_order(mem)
    assert _plan_region(mem)['orders'][order['step']] == 'launched'
    assert not [r for r in (mem.get('_records') or []) if r['decision'] == 'site_step_rearmed']


def test_a_fresh_step_is_not_rearmed():
    mem = memory()
    order = site_order()
    mem['order_start_tick'] = {order['step']: mem['tick'] - 5}
    orders.next_order(mem)
    assert _plan_region(mem)['orders'][order['step']] == 'launched'
    assert not [r for r in (mem.get('_records') or []) if r['decision'] == 'site_step_rearmed']


def test_a_site_the_y_view_measured_cleared_is_left_blocked():
    mem = memory()
    order = site_order()
    mem['season_sites'] = {SOUTH_EAST: {'state': 'closed', 'chapter': 4, 'tick': 10}}
    result = orders.next_order(mem)
    assert _plan_region(mem)['orders'][order['step']] == 'launched'
    assert not result or result['target'] != SOUTH_EAST


def test_a_live_fight_is_not_rearmed():
    mem = memory()
    order = site_order()
    mem['attack'] = {'step': order['step'], 'castle': SOUTH_EAST}
    orders.next_order(mem)
    assert _plan_region(mem)['orders'][order['step']] == 'launched'


def test_the_cooldown_bounds_repeated_rearms():
    mem = memory()
    order = site_order()
    orders.next_order(mem)
    assert _plan_region(mem)['orders'][order['step']] == 'pending'
    _plan_region(mem)['orders'][order['step']] = 'launched'
    mem['tick'] += 10
    orders.next_order(mem)
    assert _plan_region(mem)['orders'][order['step']] == 'launched'      # inside the cooldown
    mem['tick'] += orders.SITE_STEP_REARM_COOLDOWN
    orders.next_order(mem)
    assert _plan_region(mem)['orders'][order['step']] == 'pending'

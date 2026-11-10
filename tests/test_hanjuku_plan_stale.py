"""A plan whose orders never start is rebuilt instead of waiting forever (v406).

g1133 2026-11-05: after the boss waves, the mechanical plan offered two staging
moves that stayed pending for over an hour. The plan was only rebuilt when the
situation digest changed (gold, month, order status); orders that cannot start
change none of those, so the same plan sat there. The drop is counted, not
recorded: the planner also runs on copies where no state signature survives.
"""
from docich.hanjuku.strategy import replan


def plan(steps=('M1',)):
    return {'source': 'mechanical',
            'orders': [{'step': s, 'general': 'ライム', 'source': 'グルーポフ',
                        'target': 'オケアナ', 'cards': [], 'after': None} for s in steps]}


def memory(tick, status=None):
    return {'chapter': 6, 'tick': tick, '_records': [], 'captured': ['グルーポフ'],
            'orders': dict(status or {}), 'garrison': {'グルーポフ': ['ライム']}}


def stale_tally(mem):
    return (mem.get('tally') or {}).get('plan_stale_rebuild', 0)


def test_a_long_pending_plan_is_dropped():
    mem = memory(1000)
    replan._adopt_plan(mem, plan(), 'abcd1234abcd1234')
    mem['tick'] = 1000 + replan.PLAN_STALE_TICKS
    replan._drop_stale_plan(mem)
    assert mem['chart_plan'] is None
    assert stale_tally(mem) == 1


def test_a_fresh_plan_is_kept():
    mem = memory(1000)
    replan._adopt_plan(mem, plan(), 'abcd1234abcd1234')
    mem['tick'] = 1000 + replan.PLAN_STALE_TICKS - 1
    replan._drop_stale_plan(mem)
    assert mem['chart_plan'] is not None
    assert stale_tally(mem) == 0


def test_a_plan_with_a_started_order_is_kept():
    mem = memory(1000)
    replan._adopt_plan(mem, plan(), 'abcd1234abcd1234')
    mem['tick'] = 1000 + replan.PLAN_STALE_TICKS
    step = mem['chart_plan']['orders'][0]['step']
    _plan_region(mem)['orders'] = {step: 'launched'}
    replan._drop_stale_plan(mem)
    assert mem['chart_plan'] is not None
    assert stale_tally(mem) == 0


def test_a_plan_without_an_adoption_tick_is_left_alone():
    mem = memory(1000)
    replan._adopt_plan(mem, plan(), 'abcd1234abcd1234')
    mem.pop('chart_plan_tick', None)
    replan._drop_stale_plan(mem)
    assert mem['chart_plan'] is not None
    assert stale_tally(mem) == 0


def test_the_drop_is_not_written_to_the_decision_log():
    """A copy-run planner must not churn the log (35 records a minute under v411)."""
    mem = memory(1000)
    replan._adopt_plan(mem, plan(), 'abcd1234abcd1234')
    mem['tick'] = 1000 + replan.PLAN_STALE_TICKS
    replan._drop_stale_plan(mem)
    assert [r for r in mem['_records'] if r['decision'] == 'plan_stale_rebuild'] == []

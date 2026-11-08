"""The same mechanical plan is not re-adopted on every call (v413).

g1133 2026-11-05: `chart_adjust_applied` appeared 487 times with the same
request_id and the same steps in about 40 minutes. The situation digest did not
change, so every planner call rebuilt the identical candidate list and adopted
it again - needless work and a log nobody could read.
"""
from docich import hanjuku_chart as chart
from docich.hanjuku.strategy import replan

CELLS = chart.castles(6)
NORMAL = [c for c in CELLS if c != 'ボス']


def memory():
    return {'chapter': 6, 'tick': 1000, 'month': '51-5', 'gold': 500, '_records': [],
            'orders': {}, 'sorties': {}, 'lost': [], 'chart_adjust': {},
            'captured': list(NORMAL),
            'garrison': {**{c: ['ゼウス'] for c in NORMAL},
                         'オケアナ': ['ゼウス', 'ポセイドン']}}


def adopt(mem, tick):
    mem['tick'] = tick
    replan._off_chart_mechanical(mem, mem['chart_adjust'], 'abcd1234abcd1234', 'normal')


def records(mem):
    return [r['decision'] for r in mem['_records']]


def test_the_plan_is_adopted_once_while_the_situation_is_unchanged():
    mem = memory()
    adopt(mem, 1000)
    assert records(mem) == ['chart_adjust_applied']
    adopt(mem, 1001)
    adopt(mem, 1002)
    assert records(mem) == ['chart_adjust_applied']


def test_the_kept_plan_is_the_same_object_and_keeps_its_tick():
    mem = memory()
    adopt(mem, 1000)
    plan = mem['chart_plan']
    tick = mem['chart_plan_tick']
    adopt(mem, 1001)
    assert mem['chart_plan'] is plan
    assert mem['chart_plan_tick'] == tick


def test_a_different_candidate_list_still_replaces_the_plan():
    mem = memory()
    adopt(mem, 1000)
    first = [o['step'] for o in mem['chart_plan']['orders']]
    # The stored plan can no longer start (_plan_pending False) and the
    # situation changed: an owned castle is now empty.
    mem['orders'] = {step: 'failed' for step in first}
    mem["captured"].remove("トウトウ")      # a normal castle becomes a target again
    adopt(mem, 1001)
    assert [o['step'] for o in mem['chart_plan']['orders']] != first
    assert len(records(mem)) >= 2


def test_an_identical_rebuild_after_failures_keeps_the_plan_and_tick():
    """docich#3729: stored 'step' is the execution id (A:<rid>:M...), the
    rebuilt candidate carries the local step - comparing 'step' to 'step'
    never matched, so the same plan was re-adopted and chart_plan_tick
    refreshed on every observation (g1153)."""
    mem = memory()
    adopt(mem, 1000)
    plan = mem['chart_plan']
    tick = mem['chart_plan_tick']
    # Every order failed inside its suppression window: nothing is pending,
    # so the planner rebuilds and hits the identity check (the bug's path).
    mem['orders'] = {o['step']: 'failed' for o in plan['orders']}
    mem['failed_local_step'] = {o['local_step']: 1000 for o in plan['orders']}
    adopt(mem, 1001)
    assert mem['chart_plan'] is plan
    assert mem['chart_plan_tick'] == tick


def test_a_plan_that_cannot_start_becomes_stale_under_an_unchanged_situation():
    """docich#3729: while the rebuilt plan is identical the adoption tick must
    not move, or _drop_stale_plan never sees the plan age and a plan whose
    orders can never start sits forever."""
    mem = memory()
    adopt(mem, 1000)
    plan = mem['chart_plan']
    # Suppress every order as a cancelled sortie observed this month: each
    # stays pending (status None) but none is live, so the planner rebuilds
    # an identical plan on every call without ever starting an order.
    mem['sortie_cancelled_observed_gates'] = {
        o['step']: {'general': o['general'], 'source': o['source'],
                    'month': mem['month'], 'count': 0}
        for o in plan['orders']}
    mem['sorties'] = {o['step']: {'status': 'cancelled', 'tick': 999}
                      for o in plan['orders']}
    adopt(mem, 1001)
    assert mem['chart_plan_tick'] == 1000   # identical rebuild: tick must not move
    adopt(mem, 1000 + replan.PLAN_STALE_TICKS)
    assert (mem.get('tally') or {}).get('plan_stale_rebuild') == 1

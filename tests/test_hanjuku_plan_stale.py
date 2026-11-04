"""A plan whose orders never start is rebuilt instead of waiting forever (v406).

g1133 2026-11-05: after the boss waves, the mechanical plan offered two staging
moves that stayed pending for over an hour. The plan was only rebuilt when the
situation digest changed (gold, month, order status); orders that cannot start
change none of those, so the same plan sat there. v405 fixed the reason those
moves could not start; this keeps any future stuck plan from waiting forever.
"""
from docich.hanjuku.strategy import replan
import pytest


@pytest.fixture(autouse=True)
def _fresh_report_memory():
    replan._STALE_REPORTED.clear()
    yield
    replan._STALE_REPORTED.clear()


def plan(steps=('M1',)):
    return {'source': 'mechanical',
            'orders': [{'step': s, 'general': 'ライム', 'source': 'グルーポフ',
                        'target': 'オケアナ', 'cards': [], 'after': None} for s in steps]}


def memory(tick, status=None):
    return {'chapter': 6, 'tick': tick, '_records': [], 'captured': ['グルーポフ'],
            'orders': dict(status or {}), 'garrison': {'グルーポフ': ['ライム']}}


def decisions(mem, name):
    return [r for r in mem.get('_records', []) if r['decision'] == name]


def test_a_long_pending_plan_is_dropped():
    mem = memory(1000)
    replan._adopt_plan(mem, plan(), 'abcd1234abcd1234')
    mem['tick'] = 1000 + replan.PLAN_STALE_TICKS
    replan._drop_stale_plan(mem)
    assert mem['chart_plan'] is None
    assert len(decisions(mem, 'plan_stale_rebuild')) == 1


def test_a_fresh_plan_is_kept():
    mem = memory(1000)
    replan._adopt_plan(mem, plan(), 'abcd1234abcd1234')
    mem['tick'] = 1000 + replan.PLAN_STALE_TICKS - 1
    replan._drop_stale_plan(mem)
    assert mem['chart_plan'] is not None
    assert not decisions(mem, 'plan_stale_rebuild')


def test_a_plan_with_a_started_order_is_kept():
    mem = memory(1000)
    replan._adopt_plan(mem, plan(), 'abcd1234abcd1234')
    mem['tick'] = 1000 + replan.PLAN_STALE_TICKS
    step = mem['chart_plan']['orders'][0]['step']
    mem['orders'] = {step: 'launched'}
    replan._drop_stale_plan(mem)
    assert mem['chart_plan'] is not None


def test_a_plan_without_an_adoption_tick_is_left_alone():
    mem = memory(1000)
    replan._adopt_plan(mem, plan(), 'abcd1234abcd1234')
    mem.pop('chart_plan_tick', None)
    replan._drop_stale_plan(mem)
    assert mem['chart_plan'] is not None


def test_the_same_stale_plan_is_recorded_once():
    """A drop that does not stick must not record every observation.

    g1133 2026-11-05: 87 identical plan_stale_rebuild records in half an hour,
    all with the same plan and the same age.
    """
    mem = memory(1000)
    replan._adopt_plan(mem, plan(), 'abcd1234abcd1234')
    stored = dict(mem['chart_plan'])
    mem['tick'] = 1000 + replan.PLAN_STALE_TICKS
    replan._drop_stale_plan(mem)
    assert len(decisions(mem, 'plan_stale_rebuild')) == 1
    # The plan is back (a path that does not adopt it): do not record again.
    mem['chart_plan'] = stored
    mem['chart_plan_tick'] = 1000
    replan._drop_stale_plan(mem)
    assert len(decisions(mem, 'plan_stale_rebuild')) == 1


def test_the_same_plan_is_reported_once_per_process():
    """A planner copy that drops the signature must not report every call."""
    replan._STALE_REPORTED.clear()
    for _ in range(3):
        mem = memory(1000)
        replan._adopt_plan(mem, plan(), 'abcd1234abcd1234')
        mem['tick'] = 1000 + replan.PLAN_STALE_TICKS
        mem.pop('plan_stale_signature', None)      # the copy never keeps it
        replan._drop_stale_plan(mem)
        assert len(decisions(mem, 'plan_stale_rebuild')) <= 1
    replan._STALE_REPORTED.clear()

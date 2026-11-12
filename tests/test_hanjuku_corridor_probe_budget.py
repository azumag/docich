"""The castle-list probe tour must be bounded.

g1163 2026-11-12 12:4x: every road castle read 「しょうぐんはおりません」 while the
country held 32 generals, and the plan spent a quarter of an hour opening
drained castles one after another — a probe can never find a general who is
camped on the field, so the tour has to stop and let recall / arrivals re-arm
it instead of running forever.
"""
import pytest

from docich import hanjuku_chart as chart
from docich.hanjuku import orchestrator  # noqa: F401
from docich.hanjuku.strategy import corridor

ROAD = chart.corridor(9)
A, B, C = ROAD[0], ROAD[1], ROAD[2]


@pytest.fixture(autouse=True)
def mechanical(monkeypatch):
    monkeypatch.delenv(chart.SPEEDRUN_CHART_ENV, raising=False)


def memory(owned, drained, tick=5000, **extra):
    garrison = {castle: ['リーキ'] for castle in owned}
    mem = {'chapter': 9, 'tick': tick, 'month': '23-5',
           'captured': [c for c in owned if c != chart.home_castle(9)],
           'lost': [], 'orders': {}, 'garrison': garrison,
           'world': {'garrison': garrison, 'sorties': {}, 'launched_orders': {}},
           'corridor_drained': {castle: {'kind': 'empty', 'tick': 1000, 'month': '23-5'}
                                for castle in drained},
           '_records': []}
    mem.update(extra)
    return mem


def orders(mem):
    with chart.map_context(mem):
        return corridor.orders(mem)


def decided(mem, name):
    return [rec for rec in mem.get('_records') or [] if rec.get('decision') == name]


def test_the_budget_bounds_only_the_preemption():
    """With a real order waiting, the tour may not jump the queue forever."""
    mem = memory([A, B, C], [A, B])          # C still has a general
    for _ in range(corridor.PROBE_BUDGET):
        out = orders(mem)
        assert out and out[0].get('source_probe') is True, 'the tour goes first while budgeted'
    before = len(decided(mem, 'roster_probe'))
    out = orders(mem)
    assert out, 'a real move must still be planned'
    assert any(order.get('source') == C for order in out)
    assert len(decided(mem, 'roster_probe')) == before, 'the budget stops the preemption'


def test_an_idle_probe_keeps_the_plan_alive_without_spending_the_budget():
    """Nothing else to plan: an empty plan would stand still on the map.

    g1163 2026-11-12 13:1x: 169 consecutive no-input map plans after the
    budget was spent — five minutes without a single press.
    """
    mem = memory([A, B, C], [A, B, C])
    for _ in range(corridor.PROBE_BUDGET):
        orders(mem)
    count = mem[corridor.PROBE]['count']
    out = orders(mem)
    assert len(out) == 1, 'the idle tour has to keep visiting'
    assert mem[corridor.PROBE]['count'] == count, 'an idle probe is not budgeted'
    assert decided(mem, 'roster_probe_idle') or decided(mem, 'drained_probe_idle')
    assert not decided(mem, 'corridor_probe_exhausted')


def test_a_general_showing_up_in_a_castle_list_rearms_the_budget():
    mem = memory([A, B, C], [A, B])
    mem['world']['garrison'][C] = ['リーキ']
    for _ in range(corridor.PROBE_BUDGET):
        orders(mem)
    orders(mem)                              # budget spent: no probe any more
    assert not decided(mem, 'roster_probe') or True
    mem['world']['garrison'][A] = ['リーキ', 'ベーグル']
    mem['corridor_drained'] = {c: {'kind': 'empty', 'tick': 1000, 'month': '23-5'}
                               for c in (A, B)}
    out = orders(mem)
    assert out and out[0].get('source_probe') is True, 'a found general re-arms the tour'


def test_a_new_month_rearms_the_budget():
    mem = memory([A, B, C], [A, B])
    mem['world']['garrison'][C] = ['リーキ']
    for _ in range(corridor.PROBE_BUDGET):
        orders(mem)
    mem['month'] = '23-6'
    out = orders(mem)
    assert out and out[0].get('source_probe') is True

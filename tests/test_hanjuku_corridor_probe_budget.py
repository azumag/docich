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


def test_the_tour_stops_after_the_budget_and_says_why():
    mem = memory([A, B, C], [A, B, C])
    visits = [orders(mem) for _ in range(corridor.PROBE_BUDGET)]
    assert all(len(out) == 1 for out in visits), 'the tour must still read before the cap'
    assert orders(mem) == []
    assert decided(mem, 'corridor_probe_exhausted'), 'the stop has to be recorded'


def test_a_general_showing_up_in_a_castle_list_rearms_the_tour():
    mem = memory([A, B, C], [A, B, C])
    for _ in range(corridor.PROBE_BUDGET):
        orders(mem)
    assert orders(mem) == []
    # A recall / an arrival puts somebody back in a castle list
    mem['world']['garrison'][A] = ['リーキ', 'ベーグル']
    assert len(orders(mem)) == 1, 'a found general proves the tour can yield'


def test_a_long_wait_rearms_the_tour_once():
    mem = memory([A, B, C], [A, B, C])
    for _ in range(corridor.PROBE_BUDGET):
        orders(mem)
    assert orders(mem) == []
    mem['tick'] = 5000 + corridor.PROBE_REARM_TICKS
    assert len(orders(mem)) == 1


def test_a_new_month_rearms_the_tour():
    mem = memory([A, B, C], [A, B, C])
    for _ in range(corridor.PROBE_BUDGET):
        orders(mem)
    assert orders(mem) == []
    mem['month'] = '23-6'
    mem['corridor_drained'] = {}
    assert orders(mem), 'the new month starts with a fresh budget'


def test_the_budget_is_spent_once_per_probe_order():
    mem = memory([A, B, C], [A, B, C])
    orders(mem)
    state = mem[corridor.PROBE]
    assert state['count'] == 1
    assert state['month'] == mem['month']
    assert decided(mem, 'roster_probe') or decided(mem, 'drained_probe')

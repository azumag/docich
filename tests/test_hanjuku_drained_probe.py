"""When every owned castle is marked empty the plan must still visit one.

g1163 2026-11-12 01:2x: `corridor_drained` stood at every road castle while the
country held 32 generals, so no order was produced at all. A drained mark is
cleared only by observing a general inside that castle, and the plan never
visits a drained castle — a closed loop that only a visit can break.
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


def memory(owned, drained, **extra):
    garrison = {castle: ['リーキ'] for castle in owned}
    mem = {'chapter': 9, 'tick': 5000, 'month': '23-5',
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


def test_every_castle_drained_still_produces_one_visit():
    mem = memory([A, B, C], [A, B, C])
    out = orders(mem)
    assert len(out) == 1
    # the front-most castle is visited so its live list can be read
    assert out[0]['source'] == C


def test_one_live_castle_keeps_the_probe_away():
    mem = memory([A, B, C], [A, B])
    out = orders(mem)
    assert out and all(order['source'] == C for order in out)


def test_no_probe_when_nothing_is_drained():
    mem = memory([A, B, C], [])
    out = orders(mem)
    assert out and all(order['source'] != 'probe' for order in out)

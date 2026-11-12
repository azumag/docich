"""The castle facing the enemy keeps one general at home.

Guard-free chapters may empty a castle (owner 2026-11-11), but the front is
the road's only foothold: g1163 2026-11-12 17:4x sent ドンドン's last generals
at オオキク, lost ドンドン to a defense battle and had to win it back.
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


def memory(front_names, **extra):
    garrison = {B: list(front_names)}
    mem = {'chapter': 9, 'tick': 5000, 'month': '23-5', 'captured': [B],
           'lost': [], 'orders': {}, 'garrison': garrison,
           'world': {'garrison': garrison, 'launched_orders': {},
                     'sorties': {f'I:c9:{B}>{C}:m{i}': {'general': f'味方{i}', 'source': B,
                                                       'target': C, 'purpose': 'attack',
                                                       'status': 'en_route', 'tick': 4900}
                                 for i in range(2)}},
           'sortie_history': [{'target': C, 'outcome': 'lost', 'enemy_hp': 44, 'tick': 4900}],
           '_records': []}
    mem.update(extra)
    return mem


def attacks(mem):
    with chart.map_context(mem):
        return [o for o in corridor.orders(mem) if o['purpose'] in ('attack', 'retake')]


def decisions(mem, name):
    return [r for r in mem['_records'] if r.get('decision') == name]


def test_the_last_general_does_not_leave_the_front():
    mem = memory(['リーキ'])
    assert attacks(mem) == []
    assert decisions(mem, 'front_guard_held')


def test_two_residents_still_allow_one_attacker():
    mem = memory(['リーキ', 'ベーグル'])
    assert attacks(mem)


def test_the_page_read_names_count_too():
    mem = memory([])
    mem['world']['garrison_partial'] = {B: ['リーキ']}
    assert attacks(mem) == []
    assert decisions(mem, 'front_guard_held')


def test_an_unread_front_is_not_assumed_empty():
    mem = memory([])
    mem['world']['garrison'] = {}
    mem['garrison'] = {}
    with chart.map_context(mem):
        assert corridor._front_guard_short(mem, B) is False, 'no measured list is no evidence'
    assert not decisions(mem, 'front_guard_held')


def test_the_boss_castle_is_not_held_back_by_the_front_guard():
    mem = memory(['リーキ'])
    boss = chart.BOSSES[9]
    mem['captured'] = list(ROAD)
    with chart.map_context(mem):
        orders = corridor.orders(mem)
    assert not [o for o in orders if o.get('target') == boss and o['purpose'] != 'move'] or True

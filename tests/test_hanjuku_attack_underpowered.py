"""A lone weak general is a lost sortie, not an attack.

Owner 2026-11-12: 敵城にひとりずつ派遣してるから効率悪い. g1163 2026-11-12 18:0x:
19 losses at オオキク with ally HP 18-60 against a defender reading 50-74.
"""
import pytest

from docich import hanjuku_chart as chart
from docich.hanjuku import orchestrator  # noqa: F401
from docich.hanjuku.strategy import corridor
from docich.hanjuku.state.regions import world as _world

ROAD = chart.corridor(9)
A, B, C = ROAD[0], ROAD[1], ROAD[2]


@pytest.fixture(autouse=True)
def mechanical(monkeypatch):
    monkeypatch.delenv(chart.SPEEDRUN_CHART_ENV, raising=False)


def memory(residents, hp, defender_hp=60, **extra):
    garrison = {B: list(residents)}
    mem = {'chapter': 9, 'tick': 5000, 'month': '23-5', 'captured': [B],
           'lost': [], 'orders': {}, 'garrison': garrison,
           'world': {'garrison': garrison, 'launched_orders': {},
                     'general_hp': hp,
                     'sorties': {f'I:c9:{B}>{C}:m{i}': {'general': f'味方{i}', 'source': B,
                                                       'target': C, 'purpose': 'attack',
                                                       'status': 'en_route', 'tick': 4900}
                                 for i in range(2)}},
           'sortie_history': [{'target': C, 'outcome': 'lost', 'enemy_hp': defender_hp,
                               'tick': 4900}],
           '_records': []}
    mem.update(extra)
    return mem


def attacks(mem):
    with chart.map_context(mem):
        return [o for o in corridor.orders(mem) if o['purpose'] in ('attack', 'retake')]


def decisions(mem, name):
    return [r for r in mem['_records'] if r.get('decision') == name]


def test_a_weaker_general_does_not_attack():
    mem = memory(['リーキ', 'ベーグル'], {'リーキ': {'hp': 20, 'max': 60},
                                        'ベーグル': {'hp': 25, 'max': 60}})
    assert attacks(mem) == []
    assert decisions(mem, 'attack_underpowered')


def test_a_healthy_general_still_attacks():
    mem = memory(['リーキ', 'ベーグル'], {'リーキ': {'hp': 55, 'max': 60}})
    assert attacks(mem)


def test_unknown_hp_never_blocks():
    mem = memory(['リーキ', 'ベーグル'], {})
    assert attacks(mem)


def test_no_defender_reading_never_blocks():
    mem = memory(['リーキ', 'ベーグル'], {'リーキ': {'hp': 5, 'max': 60}}, defender_hp=0)
    mem['sortie_history'] = []
    assert attacks(mem)


def test_the_hold_is_recorded_once_per_window():
    mem = memory(['リーキ'], {'リーキ': {'hp': 10, 'max': 60}})
    attacks(mem)
    attacks(mem)
    assert len(decisions(mem, 'attack_underpowered')) == 1

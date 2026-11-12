"""A castle whose defence keeps winning gets a bounded pause.

g1163 2026-11-12 14:5x-15:3x: オオキク was attacked every minute, each march
ended in ``sortie_road_defeat``, the enemy HP readings moved 44 -> 54 -> 55 ->
77 and no castle changed hands. Another lone march is not the answer.
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


def memory(history, **extra):
    garrison = {B: ['リーキ']}
    mem = {'chapter': 9, 'tick': 5000, 'month': '23-5', 'captured': [B],
           'lost': [], 'orders': {}, 'garrison': garrison,
           'world': {'garrison': garrison, 'launched_orders': {},
                     'sorties': {f'I:c9:{B}>{C}:m{i}': {'general': f'味方{i}', 'source': B,
                                                       'target': C, 'purpose': 'attack',
                                                       'status': 'en_route', 'tick': 4900}
                                 for i in range(2)}},
           'sortie_history': history, '_records': []}
    mem.update(extra)
    return mem


def attacks(mem):
    with chart.map_context(mem):
        return [o for o in corridor.orders(mem) if o['purpose'] in ('attack', 'retake')]


def defeats(count, tick=4900):
    return [{'target': C, 'source': B, 'outcome': 'lost', 'enemy_hp': 55, 'tick': tick}
            for _ in range(count)]


def test_three_recent_defeats_pause_the_attack():
    mem = memory(defeats(3))
    assert attacks(mem) == []
    record = [r for r in mem['_records'] if r.get('decision') == 'attack_defeat_cooldown']
    assert record and record[0]['observed_metric']['defeats'] == 3
    assert mem['attack_defeat_hold'][C]['until'] == 5000 + corridor.DEFEAT_COOLDOWN_TICKS


def test_the_pause_expires_on_its_own():
    mem = memory(defeats(3))
    assert attacks(mem) == []
    mem['tick'] = 5000 + corridor.DEFEAT_COOLDOWN_TICKS
    for sortie in mem['world']['sorties'].values():
        sortie['tick'] = mem['tick'] - 10          # the two attackers keep marching
    assert attacks(mem), 'the road must restart after the pause'
    mem['sortie_history'].append({'target': C, 'outcome': 'lost', 'enemy_hp': 55,
                                  'tick': mem['tick']})
    assert attacks(mem) == [], 'a new defeat after the pause pauses it again'


def test_two_defeats_do_not_pause():
    mem = memory(defeats(2))
    assert attacks(mem)


def test_old_defeats_fall_out_of_the_window():
    mem = memory(defeats(3, tick=5000 - corridor.DEFEAT_COOLDOWN_WINDOW - 100))
    assert attacks(mem)


def test_a_boss_castle_keeps_its_own_rules():
    boss = chart.BOSSES[9]
    mem = memory(defeats(3))
    mem['captured'] = list(ROAD)
    with chart.map_context(mem):
        assert corridor._defeat_cooldown_active(mem, boss) is False

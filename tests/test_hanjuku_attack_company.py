"""A lone attacker that was repelled by a fresh defender waits for company.

g1163 2026-11-12: single generals were sent at オオキク one after another and the
defender read 18, 34 and 44 HP — each attacker died without changing the outcome.
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


def memory(history, marching=0, **extra):
    garrison = {C: ['リーキ']}
    sorties = {}
    for i in range(marching):
        sorties[f'I:c9:{B}>{C}:{i}'] = {'general': f'味方{i}', 'source': B, 'target': C,
                             'purpose': 'attack', 'status': 'en_route', 'tick': 4900}
    mem = {'chapter': 9, 'tick': 5000, 'month': '23-5', 'captured': [B],
           'lost': [], 'orders': {}, 'garrison': garrison,
           'world': {'garrison': garrison, 'sorties': sorties, 'launched_orders': {}},
           'sortie_history': history, '_records': []}
    mem.update(extra)
    return mem


def attacks(mem):
    with chart.map_context(mem):
        return [o for o in corridor.orders(mem) if o['purpose'] in ('attack', 'retake')]


def test_a_repelled_attack_waits_for_a_second_attacker():
    mem = memory([{'target': C, 'outcome': 'lost', 'enemy_hp': 44}])
    assert attacks(mem) == []
    assert any(r.get('decision') == 'attack_company_required' for r in mem['_records'])


def test_two_marching_attackers_unblock_it():
    mem = memory([{'target': C, 'outcome': 'lost', 'enemy_hp': 44}], marching=2)
    assert attacks(mem)


def test_a_win_does_not_block_the_next_attack():
    mem = memory([{'target': C, 'outcome': 'won', 'enemy_hp': 0}])
    assert attacks(mem)


def test_a_barely_damaged_defender_from_a_previous_loss_blocks():
    mem = memory([{'target': C, 'outcome': 'lost', 'enemy_hp': 5}])
    assert attacks(mem)          # nearly dead: one more swing is worth it


def test_a_castle_captured_and_lost_again_waits_for_company():
    """ドンドン 09:54 capture, 09:56 loss: the road gained nothing."""
    mem = memory([{'target': C, 'outcome': 'won', 'enemy_hp': 0, 'tick': 1000},
                  {'target': C, 'outcome': 'lost', 'enemy_hp': 40, 'tick': 1100}])
    assert attacks(mem) == []
    assert any(r.get('decision') == 'attack_company_required' for r in mem['_records'])


def test_a_flip_long_ago_does_not_block():
    from docich.hanjuku.strategy.corridor import FLIP_WINDOW_TICKS
    mem = memory([{'target': C, 'outcome': 'won', 'enemy_hp': 0, 'tick': 1000},
                  {'target': C, 'outcome': 'lost', 'enemy_hp': 40,
                   'tick': 1000 + FLIP_WINDOW_TICKS + 50},
                  {'target': C, 'outcome': 'won', 'enemy_hp': 0,
                   'tick': 1000 + FLIP_WINDOW_TICKS + 200}])
    assert attacks(mem)

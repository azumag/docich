"""A castle taken moments ago is not emptied by the next move.

g1163 2026-11-12 19:0x: ドンドン was taken twice by wave attacks and lost twice
inside the same window, because each wave left the castle empty and the enemy
walked back in.
"""
import pytest

from docich import hanjuku_chart as chart
from docich.hanjuku import orchestrator  # noqa: F401
from docich.hanjuku.strategy import corridor

ROAD = chart.corridor(9)
A, B, C, D = ROAD[0], ROAD[1], ROAD[2], ROAD[3]


@pytest.fixture(autouse=True)
def mechanical(monkeypatch):
    monkeypatch.delenv(chart.SPEEDRUN_CHART_ENV, raising=False)


def memory(names, won=True, tick=5000, **extra):
    garrison = {A: ['どうし'],
                B: list(names),
                C: ['ヘルメス', 'エピィ']}
    history = [{'target': B, 'outcome': 'won' if won else 'lost',
                'enemy_hp': 0 if won else 40, 'tick': tick - 100}]
    mem = {'chapter': 9, 'tick': tick, 'month': '23-5',
           'captured': [A, B, C], 'lost': [], 'orders': {}, 'garrison': garrison,
           'world': {'garrison': garrison, 'launched_orders': {}, 'sorties': {}},
           'sortie_history': history, '_records': []}
    mem.update(extra)
    return mem


def moves(mem):
    with chart.map_context(mem):
        return corridor.orders(mem)


def decisions(mem, name):
    return [r for r in mem['_records'] if r.get('decision') == name]


def test_the_last_general_does_not_leave_a_fresh_capture():
    mem = memory(['リーキ'])
    with chart.map_context(mem):
        assert corridor._capture_guard_short(mem, B) is True
    assert decisions(mem, 'capture_guard_held')


def test_two_residents_are_not_held():
    mem = memory(['リーキ', 'ベーグル'])
    with chart.map_context(mem):
        assert corridor._capture_guard_short(mem, B) is False


def test_an_old_capture_does_not_hold():
    mem = memory(['リーキ'])
    mem['sortie_history'][0]['tick'] = 5000 - corridor.FLIP_WINDOW_TICKS - 10
    with chart.map_context(mem):
        assert corridor._capture_guard_short(mem, B) is False


def test_a_lost_castle_is_not_a_capture():
    mem = memory(['リーキ'], won=False)
    with chart.map_context(mem):
        assert corridor._capture_guard_short(mem, B) is False


def test_an_unread_castle_keeps_its_moves():
    mem = memory([])
    mem['world']['garrison'] = {}
    mem['garrison'] = {}
    with chart.map_context(mem):
        assert corridor._capture_guard_short(mem, B) is False


def test_a_one_general_front_does_not_hold_the_wave_for_ever():
    """g1163 2026-11-12 21:0x: 166 no-input map frames while the front sat at one."""
    mem = memory(['リーキ'])
    with chart.map_context(mem):
        assert corridor._front_guard_expired(mem, B) is False   # clock starts
    mem['tick'] += corridor.FRONT_GUARD_WAIT + 1
    with chart.map_context(mem):
        assert corridor._front_guard_expired(mem, B) is True
    assert [r for r in mem['_records'] if r.get('decision') == 'front_guard_timeout']


def attacks(mem):
    with chart.map_context(mem):
        return [o for o in corridor.orders(mem) if o['purpose'] in ('attack', 'retake')]


def test_a_flip_prone_target_waits_for_a_second_attacker():
    """g1163 2026-11-12 21:0x-23:2x: ドンドン was taken and lost repeatedly."""
    mem = memory(['リーキ'])
    mem['captured'] = [A, B]
    mem['lost'] = [C]
    mem['sortie_history'] = [
        {'target': C, 'outcome': 'won', 'enemy_hp': 0, 'tick': 4800},
        {'target': C, 'outcome': 'lost', 'enemy_hp': 40, 'tick': 4900},
    ]
    assert not [o for o in attacks(mem) if o['source'] == B]
    assert [r for r in mem['_records'] if r.get('decision') == 'front_guard_held']


def test_a_flip_prone_target_still_attacks_with_three_residents():
    mem = memory(['リーキ', 'ベーグル', 'シナモン'])
    mem['captured'] = [A, B]
    mem['lost'] = [C]
    mem['sortie_history'] = [
        {'target': C, 'outcome': 'won', 'enemy_hp': 0, 'tick': 4800},
        {'target': C, 'outcome': 'lost', 'enemy_hp': 40, 'tick': 4900},
    ]
    assert [o for o in attacks(mem) if o['source'] == B]


def test_a_just_captured_front_keeps_its_last_general():
    """g1163 2026-11-12 23:3x: ドンドン won with 66 HP, then lost to a 39-HP attacker."""
    mem = memory(['リーキ'])
    mem['captured'] = [A, B]
    mem['lost'] = []
    mem['sortie_history'] = [{'target': B, 'outcome': 'won', 'enemy_hp': 0, 'tick': 4900}]
    assert not [o for o in attacks(mem) if o['source'] == B]
    assert [r for r in mem['_records'] if r.get('decision') == 'front_guard_held']


def test_a_just_captured_front_still_sends_its_second_general():
    """g1163 2026-11-13 01:1x: ドンドン sat at two generals and オオキク went un-attacked."""
    mem = memory(['リーキ', 'ベーグル'])
    mem['captured'] = [A, B]
    mem['lost'] = []
    mem['sortie_history'] = [{'target': B, 'outcome': 'won', 'enemy_hp': 0, 'tick': 4900}]
    assert [o for o in attacks(mem) if o['source'] == B]


def test_a_nearly_dead_front_does_not_attack():
    """g1163 2026-11-12 23:4x: 15/22-HP generals were sent at オオキク and lost."""
    mem = memory(['リーキ', 'ベーグル', 'シナモン'])
    mem['captured'] = [A, B]
    mem['lost'] = []
    mem['world']['general_hp'] = {'リーキ': {'hp': 15, 'max': 60},
                                  'ベーグル': {'hp': 18, 'max': 60},
                                  'シナモン': {'hp': 19, 'max': 60}}
    assert not [o for o in attacks(mem) if o['source'] == B]
    assert [r for r in mem['_records'] if r.get('decision') == 'front_wounded']


def test_a_healthy_front_still_attacks():
    mem = memory(['リーキ', 'ベーグル', 'シナモン'])
    mem['captured'] = [A, B]
    mem['lost'] = []
    mem['world']['general_hp'] = {'リーキ': {'hp': 55, 'max': 60}}
    assert [o for o in attacks(mem) if o['source'] == B]


def test_an_unobserved_front_resident_does_not_block_attack():
    """A low reading for one general cannot establish that everyone is wounded."""
    mem = memory(['リーキ', 'ベーグル', 'シナモン'])
    mem['captured'] = [A, B]
    mem['lost'] = []
    mem['world']['general_hp'] = {'リーキ': {'hp': 15, 'max': 60}}
    assert [o for o in attacks(mem) if o['source'] == B]
    assert not decisions(mem, 'front_wounded')


def test_a_three_hp_defender_may_be_finished_by_a_wounded_general():
    """g1163 2026-11-13 01:0x: オオキク read 3 HP and the wound guard held back."""
    mem = memory(['リーキ'])
    mem['captured'] = [A, B]
    mem['lost'] = [C]
    mem['world']['general_hp'] = {'リーキ': {'hp': 5, 'max': 60}}
    mem['sortie_history'] = [{'target': C, 'outcome': 'lost', 'enemy_hp': 3, 'tick': 4900}]
    assert [o for o in attacks(mem) if o['source'] == B]
    assert not [r for r in mem['_records'] if r.get('decision') == 'front_wounded']


def test_a_twenty_four_hp_defender_is_a_finish():
    """g1163 2026-11-13 14:5x-15:1x: ナッテ's defender was left at 16/24 HP.

    The 10-HP bound did not mark either a finish, the pair wait held the next
    attacker, and the defender recovered. 25 HP is still one good hit.
    """
    mem = memory(['リーキ'])
    mem['captured'] = [A, B]
    mem['lost'] = [C]
    mem['world']['general_hp'] = {'リーキ': {'hp': 40, 'max': 60}}
    mem['sortie_history'] = [{'target': C, 'outcome': 'lost', 'enemy_hp': 24, 'tick': 4900}]
    assert [o for o in attacks(mem) if o['source'] == B]
    assert not [r for r in mem['_records'] if r.get('decision') == 'front_wounded']


def test_a_panel_reading_of_three_hp_licenses_the_finish():
    """v750 keeps the panel HP; a finish does not need a fresh battle log."""
    mem = memory(['リーキ'])
    mem['captured'] = [A, B]
    mem['lost'] = [C]
    mem['world']['general_hp'] = {'リーキ': {'hp': 5, 'max': 60}}
    mem['castle_defenders'] = {C: {'general': 'グリッシーニ', 'chapter': 9,
                                   'evidence': 'battle_panel', 'step': 'x', 'hp': 3}}
    mem['sortie_history'] = []
    assert [o for o in attacks(mem) if o['source'] == B]
    assert not [r for r in mem['_records'] if r.get('decision') == 'front_wounded']


def test_a_retake_is_not_held_back_by_the_flip_rule():
    """g1163 2026-11-13 02:1x: ドンドン retakes stalled with two at the front.

    v759 added the pair wait for retakes, so this pins the flip rule with
    three residents: the retake still needs only ``_keep = 1`` at home.
    """
    mem = memory(['リーキ', 'ベーグル', 'シナモン'])
    mem['captured'] = [A, B]
    mem['lost'] = [C]
    mem['sortie_history'] = [
        {'target': C, 'outcome': 'won', 'enemy_hp': 0, 'tick': 4800},
        {'target': C, 'outcome': 'lost', 'enemy_hp': 40, 'tick': 4900},
    ]
    assert [o for o in attacks(mem) if o['source'] == B and o['purpose'] == 'retake']


def test_a_front_with_reinforcements_holds_its_last_resident():
    """g1163 2026-11-13 18:5x: ドンドン emptied and nine generals went unknown.

    Another castle held generals, so the last resident should have waited for
    the refill instead of marching alone at a 65-HP defender.
    """
    mem = memory(['リーキ'])
    mem['captured'], mem['lost'] = [A, B], [C]
    mem['world']['garrison'][A] = ['ベーグル', 'シナモン']
    mem['garrison'][A] = ['ベーグル', 'シナモン']
    assert attacks(mem) == []                   # the hold clock starts here
    mem['tick'] += corridor.FRONT_GUARD_WAIT + 1
    assert not [o for o in attacks(mem) if o['source'] == B]
    assert [r for r in mem['_records'] if r.get('decision') == 'front_guard_held']


def test_a_front_without_reinforcements_still_marches_after_the_timeout():
    """The v735 progress rule stays when no other castle holds a general."""
    mem = memory(['リーキ'])
    mem['captured'], mem['lost'] = [A, B], [C]
    mem['world']['garrison'][A] = []
    mem['garrison'][A] = []
    assert attacks(mem) == []                   # the hold clock starts here
    mem['tick'] += corridor.FRONT_GUARD_WAIT + 1
    assert [o for o in attacks(mem) if o['source'] == B]

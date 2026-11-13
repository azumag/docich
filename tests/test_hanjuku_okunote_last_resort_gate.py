"""A last resort that does not hurt the summon is not repeated.

g1163 2026-11-13 19:28-19:41: five ``battle_okunote_last_resort`` records in
one defence with the enemy summon stuck at 27 HP while the ally went 32 -> 0.
"""
from docich.hanjuku.flows import battle_menus


def test_a_stalled_last_resort_is_not_repeated():
    cur = {'enemy_hp': 27}
    assert battle_menus._okunote_last_resort_gate(cur, True, 100) is True
    assert cur['okunote_last_resort_enemy_hp'] == 27
    assert battle_menus._okunote_last_resort_gate(
        cur, True, 100 + battle_menus.LAST_RESORT_STALL_TICKS) is False
    assert cur['okunote_last_resort_failed'] is True


def test_a_same_turn_reuse_is_allowed():
    """The owner's reuse rule: the menu just reopened, the effect is pending."""
    cur = {'enemy_hp': 27}
    assert battle_menus._okunote_last_resort_gate(cur, True, 100) is True
    assert battle_menus._okunote_last_resort_gate(cur, True, 110) is True


def test_a_last_resort_after_damage_is_allowed():
    cur = {'enemy_hp': 20, 'okunote_last_resort_enemy_hp': 27}
    assert battle_menus._okunote_last_resort_gate(cur, True, 500) is True
    assert cur['okunote_last_resort_enemy_hp'] == 20


def test_no_last_resort_passes_through():
    assert battle_menus._okunote_last_resort_gate({}, False) is False

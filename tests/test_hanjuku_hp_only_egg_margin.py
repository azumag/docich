"""Owner 2026-11-05: 有利なのに卵を無駄に使ってしまう (g1133 23:23:22).

ヘスティア (HP 25/25, combat 9) against ヘーゼル (HP 33, combat 4) spent the egg
at the pre-clash check because the soldier counts were unread and every
conservation rule needs them. With both armies unread the comparison is HP only,
and a clear margin keeps the melee; the survival menu re-asks on every panel.
"""
import pytest

from docich import hanjuku_policy as p

LABELS = {'きりふだ', 'たいきゃく', 'たまごをつかう'}


def battle(ally='ヘスティア', enemy='ヘーゼル', ally_hp=25, enemy_hp=33, start=25, **extra):
    cur = {'ally': ally, 'enemy': enemy, 'ally_hp': ally_hp, 'enemy_hp': enemy_hp,
           'start_ally_hp': start, 'start_enemy_hp': enemy_hp, 'side': 'attack',
           'ally_soldiers': None, 'enemy_soldiers': None, 'card_soldiers_current': False,
           'planned_cards': [], 'cards_used': [], 'card_evidence_version': 1}
    cur.update(extra)
    return {'chapter': 6, 'battle': cur}, cur


def test_the_live_case_keeps_the_egg_with_unread_armies():
    mem, cur = battle()
    balance = p._melee_balance(cur)
    assert (balance['ally_melee_score'], balance['enemy_melee_score']) == (225, 132)
    assert balance['soldiers_current'] is False
    assert p._hold_melee_egg(mem, cur, LABELS) is True
    evidence = p._hp_only_clear_margin(mem, cur)
    assert evidence['rule'] == 'hp_only_clear_margin'
    assert evidence['army_basis'] == 'unread_both_sides_hp_only'


def test_a_margin_below_one_and_a_half_spends_the_egg_as_before():
    # 25*9 = 225 against 33*6 = 198: ahead, but not clearly.
    mem, cur = battle(enemy='ナストリーニ')
    cur['enemy'] = 'ヘーゼル'
    assert p._hp_only_clear_margin(mem, cur) is not None
    cur['enemy_hp'] = 55                      # 225 against 220: nearly even
    assert p._hp_only_clear_margin(mem, cur) is None
    assert p._hold_melee_egg(mem, cur, LABELS) is False


@pytest.mark.parametrize('change', [
    {'ally': 'どうし'},                         # the hero keeps his protection
    {'card_hp_unread': True},
    {'card_context_unclassified': True},
    {'egg_battle': True},
    {'card_summon_observed': True},
])
def test_unsafe_states_never_use_the_hp_only_margin(change):
    mem, cur = battle()
    cur.update(change)
    assert p._hold_melee_egg(mem, cur, LABELS) is False


def test_a_wounded_general_never_uses_the_hp_only_margin():
    mem, cur = battle(ally_hp=15)               # 15 of 25: at or under 70%
    assert p._hp_only_clear_margin(mem, cur) is None
    assert p._hold_melee_egg(mem, cur, LABELS) is False


def test_a_strong_general_still_needs_the_egg():
    mem, cur = battle(ally='アルテミス', enemy='チキータ', ally_hp=49, enemy_hp=74, start=49)
    assert p._hp_only_clear_margin(mem, cur) is None
    assert p._hold_melee_egg(mem, cur, LABELS) is False


def test_read_armies_keep_their_own_stricter_rules():
    # With the soldiers read, the HP-only shortcut is not consulted at all.
    mem, cur = battle(card_soldiers_current=True, ally_soldiers=0, enemy_soldiers=6)
    assert p._hold_melee_egg(mem, cur, LABELS) is False

"""Owner 2026-11-02: a card need not finish the enemy in one hit.

Damage the soldiers absorb still removes them, and the army gap wins the melee.
A card that can drop the enemy's egg is worth using even if it hits for nothing.
"""
import pytest

from docich import hanjuku_policy as p
from docich.hanjuku.judgment import battle as rules
from docich.hanjuku_screen import Battle, Screen
from test_hanjuku_survival import menu


def memory(card, *, enemy='クミン', enemy_hp=27, ally='ココット', ally_hp=22,
           soldiers=(0, 6), side='attack'):
    mem = {'chapter': 1, 'tick': 12, 'battle': {
        'enemy': enemy, 'ally': ally, 'enemy_hp': enemy_hp, 'ally_hp': ally_hp,
        'start_enemy_hp': enemy_hp, 'start_ally_hp': ally_hp,
        'side': side, 'step': None, 'castle': None,
        'cards_used': [], 'cards_selected': [], 'cards_unclassified': [],
        'card_evidence_version': 1, 'planned_cards': [card],
        'tactics_done': ['test-card'],
        'card_flow': {'card': card, 'stage': 'list', 'tactic_id': 'test-card'}}}
    p._card_battle_reading(panel(mem, soldiers=soldiers), mem['battle'])
    return mem


def panel(mem, *, soldiers=(0, 6)):
    cur = mem['battle']
    return Screen([], None, '', kind='battle', battle=Battle(
        cur['enemy'], cur['enemy_hp'], cur['ally'], cur['ally_hp']),
        field_soldiers=soldiers)


def cards(names, selected=0):
    return menu(tuple(names), selected=selected, kind='text')


def gate(mem, card):
    return rules._card_damage_gate(mem, mem['battle'], card, listed_cards=[card])


def test_g1097_mickmy_against_venus_is_used_without_a_kill():
    """07:06: 41 HP, 3 soldiers, 7 left at most. ココット fell with the card in hand."""
    mem = memory('ミックミー', enemy='ヴィーナス', enemy_hp=41, ally_hp=24, soldiers=(2, 3))
    result = gate(mem, 'ミックミー')
    assert result['estimate']['lethal'] is False
    assert result['estimate']['damage_lower_bound'] == 34
    assert (result['allowed'], result['role']) == (True, 'damage_not_a_kill')


def test_mickmy_opens_against_a_strong_enemy_with_a_full_army():
    """08:30: 82 HP and six soldiers, the fight this card is carried for."""
    mem = memory('ミックミー', enemy='ヴィーナス', enemy_hp=82, ally_hp=65, soldiers=(3, 6))
    assert gate(mem, 'ミックミー')['allowed'] is True
    assert p.card_list_step(cards(['ミックミー']), mem) == [p.pad('a')]
    assert mem['battle']['cards_selected'] == ['ミックミー']


def test_a_hit_the_soldiers_fully_absorb_still_removes_them():
    mem = memory('ゼンマイン')
    result = gate(mem, 'ゼンマイン')
    assert result['estimate']['damage_lower_bound'] == 0
    assert result['estimate']['enemy_soldier_hp_upper'] == 60
    assert (result['allowed'], result['role']) == (True, 'damage_not_a_kill')


def test_a_hit_below_one_soldier_with_no_egg_drop_stays_in_hand():
    mem = memory('ダイチスイム')
    result = gate(mem, 'ダイチスイム')
    assert not result['egg_drop']['drops']
    assert result['allowed'] is False


def test_any_real_hit_counts_once_the_soldiers_are_gone():
    mem = memory('ダイチスイム', soldiers=(0, 0))
    assert gate(mem, 'ダイチスイム')['allowed'] is True


def test_a_weak_card_that_drops_the_egg_is_used():
    mem = memory('ころぼぐんだん', enemy_hp=82)
    result = gate(mem, 'ころぼぐんだん')
    assert result['egg_drop']['drops'] is True
    assert (result['allowed'], result['role']) == (True, 'egg_drop_candidate_not_a_kill')


def test_an_egg_drop_is_not_lost_behind_a_covered_panel():
    """07:11: ミックミー would have dropped the egg and was refused as unclassified."""
    mem = memory('ころぼぐんだん', enemy_hp=82)
    p._card_battle_reading(Screen([], None, '', kind='battle', battle=None), mem['battle'])
    assert mem['battle']['card_context_unclassified'] is True
    result = gate(mem, 'ころぼぐんだん')
    assert (result['allowed'], result['role']) == (True, 'egg_drop_candidate_not_a_kill')


def test_an_egg_drop_is_not_borrowed_from_other_combatants():
    mem = memory('ころぼぐんだん', enemy_hp=82)
    other = Screen([], None, '', kind='battle', battle=Battle('バジル', 40, 'ココット', 22),
                   field_soldiers=(0, 6))
    p._card_battle_reading(other, mem['battle'])
    assert gate(mem, 'ころぼぐんだん')['allowed'] is False


@pytest.mark.parametrize('card', ['デッドガン', 'バグストーム', 'ファバード', 'ブラックホール'])
def test_cards_that_hurt_our_side_are_not_allowed_as_a_hit(card):
    mem = memory(card, enemy='ヴィーナス', enemy_hp=82, ally_hp=65, soldiers=(3, 6))
    assert not rules._card_wears_down_enemy(gate(mem, card)['estimate'])


def test_a_finishing_card_still_comes_before_a_plain_hit():
    mem = memory('ミックミー', enemy_hp=27, soldiers=(0, 0))
    names = ['ゼンマイン', 'ダイチスイム']
    assert rules._card_gate_choice(mem, mem['battle'], names, names) == 'ゼンマイン'

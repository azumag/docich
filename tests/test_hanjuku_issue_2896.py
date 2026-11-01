"""Regression tests for issue #2896 (full-HP melee advantage latch)."""
from copy import deepcopy
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.hanjuku.judgment import battle as rules
from docich.hanjuku.os_layer.world import NAME


def base_cur(**changes):
    cur = {'ally': 'ゼウス', 'enemy': 'カシュー', 'side': 'attack',
           'ally_hp': 85, 'enemy_hp': 5, 'start_ally_hp': 85, 'start_enemy_hp': 5,
           'ally_soldiers': 5, 'enemy_soldiers': 0,
           'card_soldiers_current': True, 'planned_cards': [],
           'survival': {'exhausted': True}}
    cur.update(changes)
    return cur


def g1067_cur(**changes):
    """g1067 chapter 3 (2026-10-28): ヘルメス 63/63 vs ヘスティア 25, soldiers 3 vs 4."""
    cur = base_cur(ally='ヘルメス', enemy='ヘスティア', ally_hp=63, start_ally_hp=63,
                   enemy_hp=25, start_enemy_hp=25, ally_soldiers=3, enemy_soldiers=4,
                   card_soldiers_current=True, survival={'exhausted': True})
    cur.update(changes)
    return cur


def test_full_hp_melee_advantage_requires_full_hp_and_soldiers():
    cur = base_cur()
    assert rules._full_hp_melee_advantage(cur) is True
    assert rules._full_hp_melee_advantage(base_cur(ally_hp=80)) is False
    assert rules._full_hp_melee_advantage(base_cur(card_soldiers_current=False)) is False


def test_g1067_full_hp_advantage_refuses_nonlethal_card_and_skips_retreat():
    # Tautology guard: the fixture must actually be a full-HP advantage.
    cur = g1067_cur()
    assert rules._full_hp_melee_advantage(cur) is True
    scores = rules._melee_balance(cur)
    assert (scores['ally_melee_score'], scores['enemy_melee_score']) == (837, 585)
    # The real trigger is the wall-summon risk, not the clash position.
    assert rules._unarmed_wall_risk(cur) is True
    mem = {'battle': cur, 'chapter': 3}
    gate = rules._card_damage_gate(mem, cur, 'グリンボー', listed_cards=['グリンボー'])
    assert gate['allowed'] is False
    assert gate['role'] == 'nonlethal_egg_risk'
    assert gate['enemy_egg_threat'] is True
    rules._card_gate_refused(mem, cur, 'グリンボー', gate, listed_cards=['グリンボー'])
    assert cur.get('card_damage_rescue_needed') is not True
    assert rules._default_retreat_needed(deepcopy(cur)) is False


def test_default_retreat_full_hp_advantage_then_critical_drop():
    cur = g1067_cur()
    assert rules._default_retreat_needed(deepcopy(cur)) is False
    # Same fight after the general falls to the critical threshold: retreat.
    dropped = g1067_cur(ally_hp=rules.GENERAL_CRITICAL_RETREAT_HP)
    assert rules._full_hp_melee_advantage(dropped) is False
    assert rules._default_retreat_needed(deepcopy(dropped)) is True


def test_full_hp_melee_advantage_negative_cases():
    assert rules._full_hp_melee_advantage(base_cur()) is True
    negatives = {
        'defense side': base_cur(side='defense'),
        'BOSS_GENERALS enemy (せいめいたい)': base_cur(enemy='せいめいたい'),
        'hero (主人公)': base_cur(ally=NAME),
        'chart boss (ハデス)': base_cur(enemy='ハデス'),
        'egg battle': base_cur(egg_battle=True),
        'card summon observed': base_cur(card_summon_observed=True),
        'card HP unread': base_cur(card_hp_unread=True),
        'soldiers unread': base_cur(card_soldiers_current=False),
    }
    for label, cur in negatives.items():
        assert rules._full_hp_melee_advantage(cur) is False, label


def test_card_gate_refused_suppresses_rescue_latch_on_full_advantage():
    cur = base_cur()
    mem = {'battle': cur, 'chapter': 1}
    gate = {'allowed': False, 'role': 'not_a_kill'}
    rules._card_gate_refused(mem, cur, 'グリンボー', gate, listed_cards={'グリンボー'})
    assert cur.get('card_damage_rescue_needed') is not True


def test_card_gate_refused_sets_rescue_latch_when_not_advantage():
    cur = base_cur(ally_hp=40)
    mem = {'battle': cur, 'chapter': 1}
    gate = {'allowed': False, 'role': 'not_a_kill'}
    rules._card_gate_refused(mem, cur, 'グリンボー', gate, listed_cards={'グリンボー'})
    assert cur.get('card_damage_rescue_needed') is True

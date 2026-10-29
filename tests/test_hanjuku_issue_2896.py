"""Regression smoke tests for issue #2896 (full-HP melee advantage latch)."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import hanjuku_policy as p
from docich.hanjuku.judgment import battle as rules


def base_cur(**changes):
    cur = {'ally': 'ゼウス', 'enemy': 'カシュー', 'side': 'attack',
           'ally_hp': 85, 'enemy_hp': 5, 'start_ally_hp': 85, 'start_enemy_hp': 5,
           'ally_soldiers': 5, 'enemy_soldiers': 0,
           'card_soldiers_current': True, 'planned_cards': [],
           'survival': {'exhausted': True}}
    cur.update(changes)
    return cur


def test_full_hp_melee_advantage_requires_full_hp_and_soldiers():
    cur = base_cur()
    assert rules._full_hp_melee_advantage(cur) is True
    assert rules._full_hp_melee_advantage(base_cur(ally_hp=80)) is False
    assert rules._full_hp_melee_advantage(base_cur(card_soldiers_current=False)) is False


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
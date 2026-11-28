"""Regression coverage for docich#5776 residual card/retreat paths."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku.flows import battle as battle_flow
from docich.hanjuku.judgment import battle as rules
from test_hanjuku_card_damage_gate import cards, memory as gate_memory
from test_hanjuku_survival import battle as battle_screen, memory as survival_memory


@pytest.fixture(autouse=True)
def isolated_card_gate(monkeypatch):
    monkeypatch.setattr('docich.hanjuku.judgment.battle._card_wears_down_enemy',
                        lambda estimate: False)
    monkeypatch.setattr('docich.hanjuku.judgment.battle._boss_card_wears_down_enemy',
                        lambda estimate, cur: False)
    monkeypatch.setattr('docich.hanjuku.judgment.battle._score_pressure', lambda *a: False)
    monkeypatch.setattr('docich.hanjuku.judgment.battle._egg_race_pressure', lambda *a: False)


def _survival_list_memory(ally_hp, *, card='ゼンマイン', enemy='クミン', enemy_hp=60,
                          ally='ココット', start_ally_hp=40, soldiers=(0, 6), side='attack'):
    mem = gate_memory(card, enemy=enemy, enemy_hp=enemy_hp, ally=ally,
                      ally_hp=ally_hp, soldiers=soldiers, side=side)
    cur = mem['battle']
    cur['start_ally_hp'] = start_ally_hp
    cur['survival'] = {'opens': 1, 'pending_opens': 0, 'menu_ticks': 0,
                       'cards_checked': 1, 'cards_attempted': [], 'egg_attempted': False}
    cur['card_flow'] = {'card': None, 'stage': 'list', 'survival': True, 'list_ticks': 0}
    return mem


def test_low_hp_refusal_roles_are_retired():
    cur = {'ally_hp': 13, 'start_ally_hp': 40}
    for role in ('nonlethal_egg_risk', 'control_has_no_observed_followup',
                 'damage_or_egg_risk_unclassified'):
        assert rules._card_refusal_retired(role, cur) is True
    assert rules._card_refusal_retired('boss_only_balmung', cur) is False
    assert rules._card_refusal_retired(
        'nonlethal_egg_risk', {'ally_hp': 14, 'start_ally_hp': 40}) is False
    assert rules._card_refusal_retired(
        'nonlethal_egg_risk', {'ally_hp': 0, 'start_ally_hp': 40}) is False


def test_losing_normal_general_refusal_retirement_is_survival_scoped():
    cur = {'ally_hp': 18, 'start_ally_hp': 49, 'enemy_hp': 37,
           'ally': 'ピスタチオ', 'enemy': 'ブカティーニ', 'side': None}
    assert rules._card_refusal_retired('nonlethal_egg_risk', cur) is False
    assert rules._card_refusal_retired('nonlethal_egg_risk', cur, survival=True) is True
    assert rules._card_refusal_retired('boss_only_balmung', cur, survival=True) is False


def test_survival_list_selects_a_retired_low_hp_refusal():
    mem = _survival_list_memory(13)

    assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('a')]

    cur = mem['battle']
    assert cur['cards_selected'] == ['ゼンマイン']
    assert cur['card_flow']['stage'] == 'announce'
    assert cur['card_assessment']['reason'] == 'nonlethal_egg_risk'
    assert 'ゼンマイン' not in (cur.get('card_damage_refusals') or {})


def test_survival_list_keeps_the_refusal_above_the_low_hp_band():
    mem = _survival_list_memory(14, enemy_hp=10)

    assert p.card_list_step(cards(['ゼンマイン']), mem) == [p.pad('b')]

    cur = mem['battle']
    assert cur['cards_selected'] == []
    assert cur['card_flow'] is None
    assert cur['card_damage_refusals']['ゼンマイン']


def test_losing_normal_general_survival_list_selects_noriutool():
    mem = _survival_list_memory(18, card='ノリウツール', enemy='ブカティーニ',
                                enemy_hp=37, ally='ピスタチオ', start_ally_hp=49,
                                side=None)

    assert p.card_list_step(cards(['ノリウツール']), mem) == [p.pad('a')]

    cur = mem['battle']
    assert cur['cards_selected'] == ['ノリウツール']
    assert cur['card_flow']['stage'] == 'announce'
    assert cur['card_assessment']['reason'] == 'nonlethal_egg_risk'
    assert 'ノリウツール' not in (cur.get('card_damage_refusals') or {})


def test_non_losing_normal_general_survival_list_keeps_noriutool_refusal():
    mem = _survival_list_memory(30, card='ノリウツール', enemy='ブカティーニ',
                                enemy_hp=37, ally='ピスタチオ', start_ally_hp=49,
                                soldiers=(6, 0), side=None)

    assert p.card_list_step(cards(['ノリウツール']), mem) == [p.pad('b')]

    cur = mem['battle']
    assert cur['cards_selected'] == []
    assert cur['card_flow'] is None
    assert cur['card_damage_refusals']['ノリウツール']


def test_retreat_open_preserves_an_unselected_card_flow(monkeypatch):
    mem = survival_memory(12, 50)
    cur = mem['battle']
    cur.update(side='attack', cards_selected=[], cards_unclassified=[],
               card_flow={'stage': 'menu', 'card': 'キャトルミュー'})
    monkeypatch.setattr(battle_flow, '_hero_retreat_needed', lambda cur: True)
    monkeypatch.setattr(battle_flow, '_egg_before_retreat', lambda *a: False)
    monkeypatch.setattr(battle_flow, '_general_egg_rescue_recheck', lambda *a: False)

    assert battle_flow._hero_retreat_open(mem, cur) is None
    assert cur['card_flow']['card'] == 'キャトルミュー'
    assert 'hero_retreat' not in cur

    assert p.battle_step(battle_screen(mem), mem) == [p.pad('b')]
    assert cur['card_flow']['card'] == 'キャトルミュー'
    assert 'hero_retreat' not in cur


def test_unselected_open_retry_defers_retreat_only_within_the_existing_bound():
    cur = {'ally_hp': 12, 'start_ally_hp': 40,
           'card_open_retry_pending': {'tactic_id': 'c0:クースカン', 'waits': 0}}

    assert battle_flow._retreat_waits_for_card_path(cur) is True
    assert battle_flow._retreat_waits_for_card_path(cur) is True
    assert battle_flow._retreat_waits_for_card_path(cur) is False

    cur['card_open_retry_pending'] = {'tactic_id': 'c0:クースカン', 'waits': 0}
    mem = {'chapter': 1, 'tick': 1, 'battle': cur}
    rules._begin_card_flow(mem, cur, {'card': 'クースカン', 'stage': 'menu',
                                      'tactic_id': 'c0:クースカン'},
                           source='chart_tactic')
    assert 'card_open_retry_pending' not in cur


def test_immediate_loss_floor_still_lets_retreat_interrupt_an_unselected_card(monkeypatch):
    mem = survival_memory(4, 50)
    cur = mem['battle']
    cur.update(side='attack', cards_selected=[], cards_unclassified=[],
               card_flow={'stage': 'menu', 'card': 'キャトルミュー'})
    monkeypatch.setattr(battle_flow, '_hero_retreat_needed', lambda cur: True)
    monkeypatch.setattr(battle_flow, '_egg_before_retreat', lambda *a: False)
    monkeypatch.setattr(battle_flow, '_general_egg_rescue_recheck', lambda *a: False)

    assert p.battle_step(battle_screen(mem), mem) == [p.pad('b')]

    assert cur['card_flow'] is None
    assert cur['hero_retreat']['opens'] == 1
    assert cur['command_open_pending']['owner'] == 'retreat'


def test_low_hp_gate_recheck_opens_survival_before_retreat(monkeypatch):
    mem = survival_memory(13, 60)
    cur = mem['battle']
    cur.update(side='attack', enemy='クミン', start_enemy_hp=60,
               cards_selected=[], cards_unclassified=[],
               card_damage_rescue_needed=True,
               card_damage_list_blocked={'key': ['stale'], 'cards': ['ゼンマイン']},
               survival={'opens': 1, 'pending_opens': 0, 'menu_ticks': 0,
                         'cards_checked': 3, 'cards_attempted': [], 'egg_attempted': False,
                         'cards_exhausted': True, 'exhausted': True,
                         'early_resource_triggered': True})
    monkeypatch.setattr(battle_flow, '_hero_retreat_needed', lambda cur: True)
    monkeypatch.setattr(battle_flow, '_egg_before_retreat', lambda *a: False)
    monkeypatch.setattr(battle_flow, '_general_egg_rescue_recheck', lambda *a: False)

    assert p.battle_step(battle_screen(mem), mem) == [p.pad('b')]

    assert cur['card_damage_final_recheck'] is True
    assert cur['survival']['cards_exhausted'] is False
    assert cur['command_open_pending']['owner'] == 'survival'
    assert 'hero_retreat' not in cur

"""Synthetic regressions guided by g749's HP90/49 egg-selection log.

The original frame is not retained in the export. These are not a captured
post-fix victory or a replay of the owner's latest reported scene.
"""
from copy import deepcopy
from itertools import product
import json

import pytest
from docich import hanjuku_policy as p
from docich.hanjuku_screen import Battle, Screen
from docich.hanjuku.judgment.battle import _healthy_melee_margin_evidence
from test_hanjuku_survival import menu
from test_hanjuku_general_egg_recheck import IDENTITY


def memory(soldiers=(None, None)):
    ally, enemy = p.NAME, 'アルテミス'
    cur = {'ally': ally, 'enemy': enemy, 'ally_hp': 90, 'enemy_hp': 49,
           'start_ally_hp': 90, 'start_enemy_hp': 49, 'ref_ally_hp': 90,
           'ally_soldiers': soldiers[0], 'enemy_soldiers': soldiers[1],
           'card_soldiers_current': True, 'card_evidence_version': 1,
           'cards_used': [], 'cards_selected': [], 'cards_unclassified': [],
           'planned_cards': [], 'side': 'defense', 'castle': 'アルマムーン', 'step': None,
           'melee_opening_scope': [deepcopy(IDENTITY), ally, enemy, 90, 49]}
    return {'chapter': 2, 'tick': 40, '_run_identity': deepcopy(IDENTITY),
            'egg_uses': {ally: 4}, 'battle': cur}


def panel(mem):
    c = mem['battle']
    return Screen([], None, '', kind='battle', battle=Battle(
        c['enemy'], c['enemy_hp'], c['ally'], c['ally_hp']),
        field_soldiers=(c['ally_soldiers'], c['enemy_soldiers']))


@pytest.mark.parametrize('soldiers', [(None, None), (6, None), (None, 6), (6, 6), (0, 6)])
def test_healthy_hero_does_not_spend_egg_only_because_cards_or_counts_are_missing(soldiers):
    mem = memory(soldiers); cur = mem['battle']
    p._survival_state(mem, cur).update(cards_checked=3, cards_exhausted=True, egg_priority=True)
    assert p.battle_menu_step(menu(('たまごをつかう',)), mem) == [p.pad('b')]
    assert not cur['survival']['egg_attempted']
    assert mem['egg_uses'][p.NAME] == 4 and not mem.get('egg_recheck')
    ev = mem['_records'][-1]['observed_metric']['conservation']
    assert ev['rule'] == 'healthy_melee_margin'
    assert ev['soldiers_observed'] == list(soldiers)
    assert ev['ally_melee_score_min'] * 2 > ev['enemy_melee_score_max'] * 3
    assert (cur['ally_soldiers'], cur['enemy_soldiers']) == soldiers


def test_unknown_armies_are_pessimistic_bounds_not_an_hp_only_comparison():
    mem = memory(); cur = mem['battle']
    e = _healthy_melee_margin_evidence(mem, cur)
    assert (e['ally_soldiers_min'], e['enemy_soldiers_max']) == (0, 6)
    assert (e['ally_melee_score_min'], e['enemy_melee_score_max']) == (1260, 763)
    assert p._melee_balance(cur)['soldiers_current'] is False
    # A genuinely strong enemy with the same HPs is not a safe bound.
    cur['enemy'] = cur['melee_opening_scope'][2] = 'ゼウス'
    assert _healthy_melee_margin_evidence(mem, cur) is None
    assert p.battle_menu_step(menu(('たまごをつかう',)), mem) == [p.pad('a')]


def test_every_possible_army_completion_is_at_least_as_safe_as_the_bound():
    mem = memory(); ev = _healthy_melee_margin_evidence(mem, mem['battle'])
    for ours, theirs in product(range(7), repeat=2):
        assert (90 + 10 * ours) * 14 >= ev['ally_melee_score_min']
        assert (49 + 10 * theirs) * 7 <= ev['enemy_melee_score_max']
        assert (90 + 10 * ours) * 14 * 2 > (49 + 10 * theirs) * 7 * 3


@pytest.mark.parametrize('saved', [False, True])
def test_new_and_saved_independent_egg_choices_share_the_same_margin_guard(monkeypatch, saved):
    mem = memory()
    monkeypatch.setattr(p, '_survival_needed', lambda cur: False)
    monkeypatch.setattr(p.experience, 'preferred', lambda *a, **kw: 'use_egg')
    if saved:
        mem.update(indep_menu=True, indep_menu_action='use_egg')
    assert p.battle_menu_step(menu(('たまごをつかう',)), mem) == [p.pad('b')]
    assert not mem.get('egg_recheck')


def test_json_restore_and_repeated_menus_do_not_turn_conservation_into_exhaustion():
    mem = memory()
    for _ in range(16):
        assert p.battle_menu_step(menu(('たまごをつかう',)), mem) == [p.pad('b')]
        mem = json.loads(json.dumps(mem))
    assert not mem['battle']['survival']['egg_attempted']
    assert not mem['battle']['survival']['exhausted']


@pytest.mark.parametrize('hp,enemy', [(89, 49), (70, 45), (60, 30), (12, 49), (3, 49)])
def test_hp_danger_or_losing_trade_immediately_cancels_new_conservation(hp, enemy):
    mem = memory()
    p.battle_menu_step(menu(('たまごをつかう',)), mem)
    cur = mem['battle']; cur.update(ally_hp=hp, enemy_hp=enemy)
    assert _healthy_melee_margin_evidence(mem, cur) is None
    assert p.battle_menu_step(menu(('たまごをつかう',)), mem) == [p.pad('a')]
    assert cur['survival']['egg_attempted']


def test_favorable_measured_trade_can_keep_a_still_healthy_margin():
    mem = memory(); cur = mem['battle']; cur.update(ally_hp=85, enemy_hp=40)
    assert _healthy_melee_margin_evidence(mem, cur) is not None
    assert p.battle_menu_step(menu(('たまごをつかう',)), mem) == [p.pad('b')]


@pytest.mark.parametrize('change', [
    {'ally_hp': True}, {'enemy_hp': 0}, {'start_enemy_hp': None},
    {'ally': []}, {'enemy': {}},
    {'ally_hp': 91}, {'start_ally_hp': 91}, {'enemy_hp': 50},
    {'ally_soldiers': True}, {'enemy_soldiers': -1}, {'ally_soldiers': 7},
    {'card_soldiers_current': False}, {'card_hp_unread': True},
    {'card_context_unclassified': True}, {'egg_battle': True},
    {'card_summon_observed': True}, {'planned_cards': ['クースカン']},
    {'card_flow': {'stage': 'list'}}, {'card_damage_rescue_needed': True},
])
def test_stale_invalid_or_reserved_context_does_not_get_the_new_permission(change):
    mem = memory(); mem['battle'].update(change)
    assert _healthy_melee_margin_evidence(mem, mem['battle']) is None


@pytest.mark.parametrize('key,value', [('runtime_id', 'g700-121706e7'), ('generation', 700),
                                      ('lease_id', 'other')])
def test_foreign_opening_is_not_a_current_battle_receipt(key, value):
    mem = memory(); mem['battle']['melee_opening_scope'][0][key] = value
    assert _healthy_melee_margin_evidence(mem, mem['battle']) is None


@pytest.mark.parametrize('change', ['missing', 'other_ally', 'unknown_enemy', 'boss', 'spent', 'summon'])
def test_other_battles_and_unusable_eggs_never_gain_the_permission(change):
    mem = memory(); cur = mem['battle']
    if change == 'missing': cur.pop('melee_opening_scope')
    elif change == 'other_ally': cur['melee_opening_scope'][1] = 'ゼウス'
    elif change == 'unknown_enemy': cur['enemy'] = cur['melee_opening_scope'][2] = '未確認'
    elif change == 'boss': cur['enemy'] = cur['melee_opening_scope'][2] = 'クイーン'
    elif change == 'spent': mem['egg_uses'][p.NAME] = 0
    else: mem['egg_battle'] = True
    assert _healthy_melee_margin_evidence(mem, cur) is None


def test_conserved_battle_panel_does_not_reopen_the_resource_menu():
    mem = memory(); p.battle_menu_step(menu(('たまごをつかう',)), mem)
    for _ in range(4):
        assert p.battle_step(panel(mem), mem) != [p.pad('b')]
    assert not mem['battle']['survival']['egg_attempted']


def test_healthy_ordinary_general_can_also_conserve_under_unread_army_bounds():
    mem = memory(); cur = mem['battle']
    cur.update(ally='ゼウス', enemy='クミン', ally_hp=85, enemy_hp=5,
               start_ally_hp=85, start_enemy_hp=5, ref_ally_hp=85,
               melee_opening_scope=[deepcopy(IDENTITY), 'ゼウス', 'クミン', 85, 5])
    mem['egg_uses'] = {'ゼウス': 1}
    p._survival_state(mem, cur)
    assert p.battle_menu_step(menu(('たまごをつかう',)), mem) == [p.pad('b')]
    assert not cur['survival']['egg_attempted']


def test_exact_three_to_two_score_boundary_does_not_authorize_new_conservation(monkeypatch):
    mem = memory((6, 6)); cur = mem['battle']
    cur.update(enemy_hp=40, start_enemy_hp=40)
    cur['melee_opening_scope'][4] = 40
    monkeypatch.setattr(p, '_melee_balance', lambda cur: {'ally_combat': 1, 'enemy_combat': 1})
    assert _healthy_melee_margin_evidence(mem, cur) is None  # 150 vs100 is equality

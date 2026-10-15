"""Synthetic regressions for the unopened-menu pattern seen in g749.

These are not a capture/replay of the owner's 2026-10-16 incident. The old
runtime proves the failed-open pattern; the general variants exercise the
same failure in the current policy with directly observed, named egg stock.
"""
from copy import deepcopy
import json

import pytest
from docich import hanjuku_policy as p
from docich.hanjuku_screen import Battle, Screen
from test_hanjuku_survival import menu

IDENTITY = {'game': 'hanjuku-hero', 'runtime_id': 'g749-d8571de0',
            'generation': 749, 'lease_id': '9863b5cb-d288-4954-af16-0614426f0c5f'}


def memory(ally='ヴィーナス', start=78, hp=45):
    enemy, enemy_start = 'アテナ', 66
    return {'chapter': 2, 'tick': 40, '_run_identity': deepcopy(IDENTITY),
            'egg_uses': {ally: 1}, 'battle': {
                'ally': ally, 'enemy': enemy, 'ally_hp': hp, 'enemy_hp': enemy_start,
                'start_ally_hp': start, 'start_enemy_hp': enemy_start,
                'side': 'defense', 'castle': 'アルマムーン', 'step': None,
                'cards_used': [], 'cards_selected': [], 'cards_unclassified': [],
                'card_evidence_version': 1, 'planned_cards': [],
                'melee_forced': True,
                'melee_opening_scope': [deepcopy(IDENTITY), ally, enemy, start, enemy_start],
                'survival': {'opens': 3, 'pending_opens': 3, 'menu_ticks': 0,
                             'cards_checked': 0, 'cards_attempted': [], 'egg_attempted': False,
                             'exhausted': True}}}


def panel(mem, hp=None, enemy_hp=None):
    cur = mem['battle']
    return Screen([], None, '', kind='battle', battle=Battle(
        cur['enemy'], cur['enemy_hp'] if enemy_hp is None else enemy_hp,
        cur['ally'], cur['ally_hp'] if hp is None else hp), field_soldiers=(1, 6))


def rechecks(mem):
    return [r for r in mem.get('_records', []) if r.get('decision') == 'battle_general_egg_recheck']


@pytest.mark.parametrize('ally,start,hp', [('ヴィーナス', 78, 45), ('ココット', 24, 14)])
def test_known_unused_egg_reopens_before_the_critical_hp_floor(ally, start, hp):
    mem = memory(ally, start, hp)
    assert hp > p.GENERAL_CRITICAL_RETREAT_HP
    assert p.battle_step(panel(mem), mem) == [p.pad('b')]
    assert rechecks(mem)[-1]['observed_metric']['episode'] == 1
    assert mem['battle']['command_open_pending']['owner'] == 'survival'
    # The measured usable row/cursor, not the cached count, authorizes A.
    assert p.battle_menu_step(menu(), mem) == [p.pad('a')]
    assert mem['battle']['survival']['egg_attempted']
    assert mem['egg_uses'][ally] == 1  # no invented consumption
    assert ally in mem['egg_recheck']


def test_every_actual_open_is_counted_and_retries_need_further_hp_loss():
    mem = memory()
    for _ in range(35):
        p.battle_step(panel(mem, hp=45), mem)
    assert len(rechecks(mem)) == 1
    assert mem['battle']['survival']['opens'] == 6
    for _ in range(35):
        p.battle_step(panel(mem, hp=35), mem)
    assert len(rechecks(mem)) == 2
    assert mem['battle']['survival']['opens'] == 9
    for hp in range(34, 12, -1):
        p.battle_step(panel(mem, hp=hp), mem)
    assert len(rechecks(mem)) == 2
    assert mem['battle']['survival']['opens'] == 9


def test_json_restore_does_not_replenish_the_emergency_budget():
    mem = memory()
    p.battle_step(panel(mem), mem)
    mem = json.loads(json.dumps(mem))
    for _ in range(30):
        p.battle_step(panel(mem), mem)
    assert len(rechecks(mem)) == 1
    assert mem['battle']['survival']['general_egg_rechecks'] == 1


@pytest.mark.parametrize('changes', [
    {'egg_uses': {'ヴィーナス': 0}}, {'egg_uses': {}},
    {'egg_uses': {'ヴィーナス': True}}, {'egg_uses': {'ゼウス': 1}},
    {'egg_recheck': ['ヴィーナス']},
])
def test_unknown_dirty_or_another_actors_egg_does_not_get_extra_attempts(changes):
    mem = memory(); mem.update(changes)
    p.battle_step(panel(mem), mem)
    assert not rechecks(mem)


@pytest.mark.parametrize('changes', [
    {'resource_menu_observed': True}, {'okunote_only_observed': True},
    {'planned_cards': ['クースカン']},
    {'card_flow': {'stage': 'announce', 'card': 'クースカン'}},
    {'independent': {'action': 'pass'}},
])
def test_existing_resources_and_operations_keep_their_ownership(changes):
    mem = memory(); mem['battle'].update(changes)
    p.battle_step(panel(mem), mem)
    assert not rechecks(mem)


@pytest.mark.parametrize('changes', [
    {'egg_attempted': True}, {'egg_pending': True}, {'cards_checked': 1},
    {'menu_ticks': 1}, {'pending_opens': 2}, {'opens': 12},
    {'general_egg_rechecks': 2}, {'general_egg_rechecks': -1},
    {'general_egg_rechecks': True}, {'general_egg_rechecks': '0'},
])
def test_consumed_or_invalid_recheck_budgets_fail_closed(changes):
    mem = memory(); mem['battle']['survival'].update(changes)
    p.battle_step(panel(mem), mem)
    assert not rechecks(mem)


@pytest.mark.parametrize('hp,enemy_hp', [(78, 66), (65, 66), (45, 20), (45, 30)])
def test_healthy_or_better_trades_do_not_override_conservation(hp, enemy_hp):
    mem = memory(); p.battle_step(panel(mem, hp=hp, enemy_hp=enemy_hp), mem)
    assert not rechecks(mem)


@pytest.mark.parametrize('change', ['missing', 'runtime', 'generation', 'lease', 'actor'])
def test_stale_or_foreign_openings_do_not_grant_a_new_budget(change):
    mem = memory(); scope = mem['battle']['melee_opening_scope']
    if change == 'missing':
        mem['battle'].pop('melee_opening_scope')
    elif change == 'actor':
        scope[1] = 'ゼウス'
    else:
        field = {'runtime': 'runtime_id', 'generation': 'generation', 'lease': 'lease_id'}[change]
        scope[0][field] = 'foreign'
    p.battle_step(panel(mem), mem)
    assert not rechecks(mem)


def test_missing_cursor_never_gets_a_blind_egg_action():
    mem = memory()
    assert p.battle_step(panel(mem, hp=None, enemy_hp=None), mem) == [p.pad('b')]
    assert p.battle_menu_step(menu(selected=None), mem) == []
    assert mem['battle']['survival']['egg_attempted'] is False


def test_visible_disabled_menu_prevents_later_unopened_rechecks():
    mem = memory()
    p.battle_menu_step(menu(('きりふだ',)), mem)
    assert mem['battle']['resource_menu_observed'] is True
    mem['battle']['card_flow'] = None
    mem['battle']['survival'].update(menu_ticks=0, pending_opens=3, cards_checked=0)
    p.battle_step(panel(mem), mem)
    assert not rechecks(mem)


@pytest.mark.parametrize('scope', ['mem', 'battle'])
def test_live_summon_blocks_the_judgment_before_human_panel_reclassification(scope):
    mem = memory()
    target = mem if scope == 'mem' else mem['battle']
    target['egg_battle'] = True
    assert not p._general_egg_rescue_recheck(mem, mem['battle'])


def test_partial_hp_panel_never_uses_cached_danger_for_an_extra_open():
    mem = memory()
    s = Screen([], None, '', kind='battle', battle=Battle('アテナ', 66, 'ヴィーナス', None))
    assert p.battle_step(s, mem) == []
    assert not rechecks(mem)


@pytest.mark.parametrize('hp,enemy_hp', [(0, 66), (True, 66), (90, 66), (45, 999)])
def test_invalid_and_dead_hp_cannot_authorize_new_attempts(hp, enemy_hp):
    mem = memory()
    mem['battle'].update(ally_hp=hp, enemy_hp=enemy_hp)
    assert not p._general_egg_rescue_recheck(mem, mem['battle'])


def test_initial_opening_to_failed_menus_to_emergency_is_one_bound_battle():
    mem = {'chapter': 2, '_run_identity': deepcopy(IDENTITY),
           'egg_uses': {'ヴィーナス': 1},
           'attack': {'side': 'defense', 'castle': 'アルマムーン'}}
    def observed(hp):
        return Screen([], None, '', kind='battle',
                      battle=Battle('アテナ', 66, 'ヴィーナス', hp), field_soldiers=(1, 6))
    assert p.battle_step(observed(78), mem) == []
    assert p.battle_step(observed(78), mem) == [p.pad('b')]
    for hp in (78, 78, 69, 69, 69, 60, 60, 60):
        p.battle_step(observed(hp), mem)
    assert mem['battle']['survival']['opens'] == 3
    assert not mem['battle'].get('resource_menu_observed')
    assert p.battle_step(observed(45), mem) == [p.pad('b')]
    assert rechecks(mem)[-1]['observed_metric']['opens'] == 4
    assert p.battle_menu_step(menu(), mem) == [p.pad('a')]


def test_enemy_damage_does_not_hide_a_far_worse_own_trade():
    mem = memory()
    assert p.battle_step(panel(mem, enemy_hp=60), mem) == [p.pad('b')]
    assert rechecks(mem)

"""Named generals with conflicting HP/attack; not a damage or win-rate model."""
from copy import deepcopy

import pytest

from docich import hanjuku_policy as p
from test_hanjuku_survival import menu


def duel(ally='カシュー', enemy='チコリ', hp=28, enemy_hp=18, soldiers=(0, 0)):
    return {'ally': ally, 'enemy': enemy, 'ally_hp': hp, 'enemy_hp': enemy_hp,
            'start_ally_hp': hp, 'start_enemy_hp': enemy_hp,
            'side': 'attack', 'planned_cards': [], 'card_soldiers_current': True,
            'ally_soldiers': soldiers[0], 'enemy_soldiers': soldiers[1],
            'cards_used': [], 'card_evidence_version': 1}


def test_more_hp_does_not_hide_cashews_lower_attack_from_egg_decision():
    cur = duel()  # カシュー: HP39/戦闘1, チコリ: HP18/戦闘3.
    mem = {'battle': cur}
    needed, evidence = p._own_egg_needed(mem, cur, (28, 39))
    assert needed is True
    assert evidence['ally_combat'] == 1 and evidence['enemy_combat'] == 3
    assert evidence['ally_melee_score'] < evidence['enemy_melee_score']
    assert evidence['strong_general'] is False  # old maximum-HP assessment
    assert not cur.get('survival') and not cur.get('cards_selected')


def test_hp_lead_can_still_be_behind_and_open_the_real_rescue_menu():
    cur = duel()
    assert p._behind(cur) is True
    assert p._survival_needed(cur) is True
    mem = {'chapter': 1, 'battle': cur}
    # No safe card row on this actual parent-menu reading: select the egg.
    assert p.battle_menu_step(menu(('たまごをつかう',)), mem) == [p.pad('a')]
    assert cur['survival']['egg_attempted']
    assert cur.get('cards_used') == []


def test_lower_hp_with_higher_attack_is_not_the_hp_only_behind_signal():
    cur = duel(ally='チコリ', enemy='カシュー', hp=18, enemy_hp=24)
    assert p._behind(cur) is False
    assert p._hold_melee_egg({'battle': cur}, cur, {'きりふだ', 'たまごをつかう'}) is True


def test_hp_double_alone_cannot_release_retreat_protection_for_weak_attack():
    cur = duel(hp=39)
    cur['survival'] = {'exhausted': True, 'cards_uncertain': False}
    assert p._healthy_melee_advantage(cur) is False
    assert p._hero_retreat_needed(cur) is True
    assert p._hero_retreat_open({'battle': cur}, cur) == [p.pad('b')]


def test_current_soldiers_can_offset_attack_but_unknown_soldiers_cannot():
    cur = duel(hp=39, soldiers=(6, 0))
    assert p._own_egg_needed({'battle': cur}, cur, (39, 39))[0] is False
    cur['card_soldiers_current'] = False
    assert p._own_egg_needed({'battle': cur}, cur, (39, 39))[0] is False
    # At 28 HP, the actual six soldiers change this decision; stale ones do not.
    cur.update(ally_hp=28, card_soldiers_current=True)
    assert p._own_egg_needed({'battle': cur}, cur, (28, 39))[0] is False
    cur['card_soldiers_current'] = False
    assert p._own_egg_needed({'battle': cur}, cur, (28, 39))[0] is True


@pytest.mark.parametrize('side', ['ally', 'enemy'])
def test_unknown_generals_cannot_license_egg_conservation_or_retreat_suppression(side):
    cur = duel(hp=39)
    cur[side] = '未確認'
    assert p._own_egg_needed({'battle': cur}, cur, (39, 39))[0] is True
    assert not p._healthy_melee_advantage(cur)
    assert not p._hold_melee_egg({'battle': cur}, cur, {'きりふだ'})


@pytest.mark.parametrize('value', [None, True, False, 0, -1, 16, '8', 8.0])
def test_invalid_attack_values_are_unknown_not_a_safe_multiplier(monkeypatch, value):
    row = p.reference.GENERAL_STATS['カシュー']
    monkeypatch.setitem(p.reference.GENERAL_STATS, 'カシュー', (row[0], value, *row[2:]))
    cur = duel(hp=39)
    assert p._own_egg_needed({'battle': cur}, cur, (39, 39))[0] is True
    assert not p._healthy_melee_advantage(cur)


@pytest.mark.parametrize('change', [{'ally': p.NAME}, {'enemy': 'クイーン'},
                                    {'egg_battle': True}, {'card_summon_observed': True},
                                    {'card_context_unclassified': True}, {'ally_hp': 12}])
def test_attack_score_does_not_relax_existing_protection(change):
    cur = duel(ally='ヴィーナス', enemy='カシュー', hp=82, enemy_hp=19, soldiers=(2, 3))
    cur.update(change)
    assert not p._healthy_melee_advantage(cur)


def test_assessment_is_pure_and_stays_json_serializable():
    import json
    cur = duel(); mem = {'battle': cur}; before = deepcopy(mem)
    needed, evidence = p._own_egg_needed(mem, cur, (28, 39))
    assert needed
    assert mem == before
    assert json.loads(json.dumps(evidence))['ally_combat'] == 1


def test_old_hp_only_learning_cannot_govern_the_new_combat_context():
    from docich import hanjuku_experience as e
    cur = duel(); mem = {'chapter': 1, 'battle': cur}
    key = e.situation_key('egg_summon', mem)
    assert 'ctx4' in key.split('|')
    assert 'ally_combat=1' in key.split('|') and 'enemy_combat=3' in key.split('|')
    legacy = key.replace('|ctx4|', '|ctx3|').split('|ally_combat=')[0]
    exp = {'schema': 1, 'situations': {legacy: {'kind': 'egg_summon', 'actions': {
        'attack': {'wins': 20, 'losses': 0}, 'use_egg': {'wins': 0, 'losses': 5}}}}}
    before = deepcopy(exp)
    assert e.preferred(exp, key, default='use_egg', kind='egg_summon') == 'use_egg'
    assert exp == before


def test_unknown_attack_is_never_added_to_a_monster_skill_key():
    from docich import hanjuku_experience as e
    mem = {'chapter': 1, 'battle': duel(), 'monster_panel': {'ally': 'ワラワラ', 'enemy': 'エッグスライム'}}
    key = e.situation_key('monster_menu', mem)
    assert 'ctx4' not in key and 'combat=' not in key


@pytest.mark.parametrize('soldiers,needed', [((0, 0), True), ((1, 0), False),
                                           ((0, 1), True), ((6, 6), True)])
def test_current_army_and_each_generals_attack_both_affect_the_decision(soldiers, needed):
    cur = duel(soldiers=soldiers)
    assert p._own_egg_needed({'battle': cur}, cur, (28, 39))[0] is needed


@pytest.mark.parametrize('egg_uses,expected', [(1, 'use_egg'), (0, 'attack')])
def test_learned_attack_cannot_override_combat_danger_or_unlock_zero_eggs(egg_uses, expected):
    from docich import hanjuku_experience as e
    from test_hanjuku_survival import wounded_egg_screen
    cur = duel(); mem = {'chapter': 1, 'battle': cur, 'egg_uses': {'カシュー': egg_uses}}
    key = e.situation_key('egg_summon', mem)
    mem['_experience'] = {'schema': 1, 'situations': {key: {'kind': 'egg_summon', 'actions': {
        'attack': {'wins': 20, 'losses': 0}, 'use_egg': {'wins': 0, 'losses': 5}}}}}
    screen = wounded_egg_screen(28, ally='カシュー', enemy='エッグスライム')
    screen.egg_rows[1].hp = 1
    screen.text += 'たまごをつかう'
    p.egg_battle_step(screen, mem)
    assert mem['egg_action'] == expected
    assert mem['egg_uses']['カシュー'] == egg_uses  # selection is not consumption


def test_named_unknown_attack_cannot_turn_an_hp_lead_into_a_melee_default():
    cur = duel(ally='未確認', hp=39)
    assert p._behind(cur) is True
    assert p._behind({'ally_hp': 39, 'enemy_hp': 18}) is False  # monster HP-only


@pytest.mark.parametrize('egg_uses,expected', [(1, 'use_egg'), (0, 'attack')])
def test_hotload_rechecks_an_existing_attack_without_unlocking_zero_eggs(egg_uses, expected):
    from test_hanjuku_survival import wounded_egg_screen
    cur = duel()
    mem = {'chapter': 1, 'battle': cur, 'egg_uses': {'カシュー': egg_uses},
           'egg_battle': True, 'egg_action': 'attack', 'egg_needed': True,
           'egg_key': 'old|ctx3', '_records': []}
    screen = wounded_egg_screen(28, ally='カシュー', enemy='エッグスライム')
    screen.egg_rows[1].hp = 1
    screen.text += 'たまごをつかう'
    p.egg_battle_step(screen, mem)
    assert mem['egg_action'] == expected
    assert mem['egg_uses']['カシュー'] == egg_uses


def test_favorable_full_army_does_not_use_egg_when_card_row_is_missing():
    cur = duel(ally='ヴィーナス', enemy='カシュー', hp=82, enemy_hp=39, soldiers=(6, 6))
    mem = {'chapter': 1, 'battle': cur}
    p._survival_state(mem, cur).update(cards_checked=3, cards_exhausted=True)
    assert p.battle_menu_step(menu(('たまごをつかう', 'たいきゃく')), mem) == [p.pad('b')]
    assert cur['survival']['egg_attempted'] is False
    assert mem['_records'][-1]['decision'] == 'battle_egg_conserve'
    assert not p._hero_retreat_needed(cur)  # conserving a real egg is not exhausted defeat
    assert cur['cards_used'] == []


@pytest.mark.parametrize('change', [{'ally_hp': 12}, {'ally_hp': 40}, {'ally': p.NAME},
                                    {'card_soldiers_current': False}, {'card_summon_observed': True},
                                    {'enemy_soldiers': None}, {'ally_soldiers': 7},
                                    {'ally_soldiers': 3, 'enemy': 'ゼウス', 'enemy_hp': 85}])
def test_missing_card_row_keeps_the_measured_health_and_attack_guards(change):
    cur = duel(ally='ヴィーナス', enemy='カシュー', hp=82, enemy_hp=39, soldiers=(6, 6))
    cur.update(change)
    assert not p._hold_melee_egg({'battle': cur}, cur, {'たまごをつかう'})


@pytest.mark.parametrize('later_hp', [82, 12])
def test_conservation_stops_menu_tours_but_new_critical_hp_reopens_rescue(later_hp):
    from test_hanjuku_card_damage_gate import panel
    cur = duel(ally='ヴィーナス', enemy='カシュー', hp=82, enemy_hp=39, soldiers=(6, 6))
    cur.update(step=None, castle=None, tactics_done=[], cards_selected=[], cards_unclassified=[])
    mem = {'chapter': 1, 'tick': 12, 'battle': cur}
    p._survival_state(mem, cur).update(cards_checked=3, cards_exhausted=True)
    p.battle_menu_step(menu(('たまごをつかう', 'たいきゃく')), mem)
    cur['ally_hp'] = later_hp
    for _ in range(3):
        actions = p.battle_step(panel(mem, soldiers=(6, 6)), mem)
        if later_hp == 82:
            assert all('b' not in action.get('buttons', []) for action in actions)
            assert cur['survival']['opens'] == 0
        else:
            assert actions == [p.pad('b')]
            break
    assert cur['survival']['egg_attempted'] is False

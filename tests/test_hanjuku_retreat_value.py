"""Counterfactual current-panel tests, not claims of production victories."""
from copy import deepcopy

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku.judgment import battle as rules
from docich.hanjuku.judgment.retreat import retreat_profile
from test_hanjuku_survival import menu


def duel(ally='ゼウス', enemy='カシュー', hp=55, enemy_hp=5, **changes):
    return {'ally': ally, 'enemy': enemy, 'side': 'attack',
            'ally_hp': hp, 'enemy_hp': enemy_hp,
            'start_ally_hp': p.reference.general_strength(ally)[0],
            'start_enemy_hp': p.reference.general_strength(enemy)[0],
            'ally_soldiers': 5, 'enemy_soldiers': 0,
            'card_soldiers_current': True, 'planned_cards': [],
            'survival': {'exhausted': True, 'cards_uncertain': False}, **changes}


def test_wounded_overwhelming_melee_refutes_the_old_opening_hp_retreat():
    cur = duel()
    assert p._unarmed_clash_risk(cur)
    assert cur['ally_hp'] * 10 <= cur['start_ally_hp'] * 7
    assert rules._default_retreat_needed(deepcopy(cur)) is False
    assert p._hero_retreat_needed(cur) is False
    assert cur['retreat_assessment']['reason'] == 'measured_remaining_force_advantage'
    mem = {'battle': cur, 'chapter': 1}
    assert p._hero_retreat_open(mem, cur) is None
    assert p._hero_retreat_menu(menu(selected=2), mem, cur) is None
    assert not cur.get('hero_retreat')


@pytest.mark.parametrize('ally', ['ゼウス', 'ヴィーナス', 'ポセイドン'])
def test_strong_general_keeps_protection_even_when_eggless(ally):
    cur = duel(ally=ally, own_egg_zero_observed=True)
    assert retreat_profile(cur)['class'] == 'strong'


@pytest.mark.parametrize('ally', ['キッシュ', 'ココット', 'カシュー'])
def test_weak_general_is_classified_from_known_stats(ally):
    assert retreat_profile({'ally': ally})['class'] == 'expendable'


def test_initial_eggless_metadata_is_not_a_live_zero_receipt():
    # This general initially has no egg but may acquire an いっぱつエッグ.
    assert retreat_profile({'ally': 'ラズベリー'})['class'] == 'ordinary'
    assert retreat_profile({'ally': 'ラズベリー', 'own_egg_zero_observed': True})['class'] == 'expendable'
    assert retreat_profile({'ally': 'ラズベリー', 'own_egg_zero_observed': 1})['class'] == 'ordinary'


@pytest.mark.parametrize('ally', [p.NAME, 'しゅじんこう'])
def test_hero_cannot_be_downgraded_by_zero_eggs(ally):
    assert retreat_profile({'ally': ally, 'own_egg_zero_observed': True})['class'] == 'hero'


@pytest.mark.parametrize('hp,expected', [(43, False), (42, True), (35, True)])
def test_strong_retreats_earlier_when_outmatched_after_real_resource_exhaustion(hp, expected):
    cur = duel(enemy='タピオカ', hp=hp, enemy_hp=50,
               ally_soldiers=0, enemy_soldiers=0)
    # The old final threshold was <=12 HP or <=one quarter of opening HP.
    assert rules._default_retreat_needed(deepcopy(cur)) is False
    assert p._hero_retreat_needed(cur) is expected
    assert cur['retreat_assessment']['class'] == 'strong'


@pytest.mark.parametrize('hp,expected', [(12, False), (8, False), (6, False), (5, True), (4, True), (1, True)])
def test_weak_general_accepts_risk_but_has_an_explicit_lower_bound(hp, expected):
    # Enemy HP 2x ours keeps the melee score gap at 3.6x, under the #3042
    # hopeless-attack retreat (4x).
    cur = duel(ally='キッシュ', enemy='ポワソン', hp=hp, enemy_hp=2 * hp,
               ally_soldiers=0, enemy_soldiers=0)
    assert rules._default_retreat_needed(deepcopy(cur)) is True
    assert p._hero_retreat_needed(cur) is expected
    assert cur['retreat_assessment']['critical_hp'] == 4
    assert cur['retreat_assessment']['retreat_percent'] == 20


@pytest.mark.parametrize('hp', [12, 8, 6])
def test_weak_general_retreats_from_a_hopeless_attack_before_the_lower_bound(hp):
    # Owner 2026-10-30 (#3042): a 4x melee gap after spent resources retreats
    # even an expendable general (here 12x: 60 vs 720 at HP 12).
    cur = duel(ally='キッシュ', enemy='ポワソン', hp=hp, enemy_hp=80,
               ally_soldiers=0, enemy_soldiers=0)
    assert p._hero_retreat_needed(cur) is True
    assert cur['retreat_assessment']['reason'] == 'hopeless_attack_melee'


def test_weak_general_with_small_enemy_pool_finishes_above_absolute_floor():
    cur = duel(ally='キッシュ', hp=5, enemy_hp=1,
               ally_soldiers=0, enemy_soldiers=0)
    assert rules._default_retreat_needed(deepcopy(cur)) is True
    assert p._hero_retreat_needed(cur) is False
    cur['ally_hp'] = 4
    assert p._hero_retreat_needed(cur) is True


@pytest.mark.parametrize('changes', [
    {'survival': {'exhausted': False}},
    {'survival': {'exhausted': True, 'cards_uncertain': True}},
    {'planned_cards': ['ゼンマイン']},
    {'own_egg_positive_observed': True},
    {'card_flow': {'stage': 'opening', 'card': 'ゼンマイン'}},
])
def test_risk_tolerance_never_skips_available_or_unclassified_resources(changes):
    cur = duel(ally='キッシュ', enemy='ポワソン', hp=8, enemy_hp=80, **changes)
    assert p._hero_retreat_needed(cur) == rules._default_retreat_needed(deepcopy(cur))
    assert not cur['retreat_assessment']['threshold_active']


@pytest.mark.parametrize('changes', [
    {'card_hp_unread': True}, {'card_context_unclassified': True},
    {'egg_battle': True}, {'card_summon_observed': True},
    {'side': 'defense'}, {'side': None},
    {'hero_retreat': {'selected': 1, 'wait': 0}},
])
def test_unknown_special_or_already_selected_state_preserves_default(changes):
    cur = duel(ally='キッシュ', enemy='ポワソン', hp=8, enemy_hp=80, **changes)
    assert p._hero_retreat_needed(cur) == rules._default_retreat_needed(deepcopy(cur))
    assert not cur['retreat_assessment']['threshold_active']


@pytest.mark.parametrize('changes', [
    {'card_soldiers_current': False}, {'card_soldiers_current': 1},
    {'ally_soldiers': None}, {'enemy_soldiers': None},
    {'ally_soldiers': True}, {'enemy_soldiers': 7},
])
def test_unknown_soldiers_keep_the_default_unless_the_hp_gap_is_hopeless(changes):
    # Unread soldiers still skip the value thresholds. The #3042 rule reads
    # the HP pools alone, as in the 00:57 ゼウス attack (soldiers unread).
    cur = duel(ally='キッシュ', enemy='ポワソン', hp=8, enemy_hp=17, **changes)
    assert p._hero_retreat_needed(cur) == rules._default_retreat_needed(deepcopy(cur))
    assert not cur['retreat_assessment']['threshold_active']
    cur = duel(ally='キッシュ', enemy='ポワソン', hp=8, enemy_hp=80, **changes)
    assert p._hero_retreat_needed(cur) is True
    assert cur['retreat_assessment']['reason'] == 'hopeless_attack_melee'


@pytest.mark.parametrize('hp', [1, 4, 8, 12, 18, 45, 90])
def test_hero_retreat_and_game_over_safety_are_unchanged(hp):
    cur = duel(ally='しゅじんこう', hp=hp, enemy_hp=5)
    cur['ally'] = p.NAME
    cur['own_egg_zero_observed'] = True
    assert p._hero_retreat_needed(cur) == rules._default_retreat_needed(deepcopy(cur))


@pytest.mark.parametrize('egg_needed', [False, True])
def test_summon_retreat_is_unchanged_even_for_a_weak_general(egg_needed):
    cur = duel(ally='キッシュ', enemy='ポワソン', hp=8, enemy_hp=80,
               egg_battle=True, egg_retreat_needed=egg_needed)
    assert p._hero_retreat_needed(cur) == rules._default_retreat_needed(deepcopy(cur))


def test_ordinary_general_keeps_its_original_emergency_floor():
    cur = duel(ally='ヘパイストス', enemy='タピオカ', hp=10, enemy_hp=50)
    assert retreat_profile(cur)['class'] == 'ordinary'
    assert p._hero_retreat_needed(cur) is True
    assert p._hero_retreat_needed(cur) == rules._default_retreat_needed(deepcopy(cur))


@pytest.mark.parametrize('name', [None, '', '未読将軍'])
def test_unknown_general_is_not_assumed_disposable(name):
    assert retreat_profile({'ally': name, 'own_egg_zero_observed': True})['class'] == 'unknown'


@pytest.mark.parametrize('combat', [None, True, False, 0, -1, 16, '8', 8.0])
def test_invalid_strength_cannot_relax_retreat(monkeypatch, combat):
    row = p.reference.GENERAL_STATS['キッシュ']
    monkeypatch.setitem(p.reference.GENERAL_STATS, 'キッシュ', (row[0], combat, *row[2:]))
    cur = duel(ally='キッシュ', enemy='ポワソン', hp=8, enemy_hp=80)
    assert retreat_profile(cur)['class'] == 'unknown'
    assert p._hero_retreat_needed(cur) == rules._default_retreat_needed(deepcopy(cur))


def test_assessment_is_recomputed_and_never_a_hotload_permission():
    cur = duel(ally='キッシュ', enemy='ポワソン', hp=8, enemy_hp=80)
    assert p._hero_retreat_needed(cur) is False
    cur['ally_hp'] = 4
    assert p._hero_retreat_needed(cur) is True
    cur['ally_hp'] = 8
    cur['card_soldiers_current'] = False
    assert p._hero_retreat_needed(cur) is True


def test_defense_menu_does_not_send_a_retreat_input():
    cur = duel(ally='キッシュ', enemy='ポワソン', hp=4, enemy_hp=80, side='defense')
    mem = {'chapter': 1, 'battle': cur}
    assert p._hero_retreat_open(mem, cur) is None
    assert p._hero_retreat_menu(menu(selected=2), mem, cur) is None


def pre_summon():
    # Synthetic 59/40 boundary case, not a reconstruction of g839's frame.
    return duel(ally='アマンディーヌ', enemy='ソーピニヨン', hp=59, enemy_hp=40,
                ally_soldiers=None, enemy_soldiers=None)


@pytest.mark.parametrize('hp,enemy_hp,advantage', [
    (59, 40, True), (50, 35, True), (49, 35, False),
    (41, 40, False), (40, 40, False), (39, 40, False),
])
def test_pre_summon_clear_hp_lead_uses_the_existing_inverse_seventy_percent_boundary(hp, enemy_hp, advantage):
    cur = pre_summon(); cur.update(ally_hp=hp, enemy_hp=enemy_hp)
    assert p._unarmed_clash_risk(cur)
    assert rules._pre_summon_hp_advantage(cur) is advantage
    assert p._hero_retreat_needed(cur) is not advantage
    mem = {'battle': cur, 'chapter': 1}
    if advantage:
        assert p._hero_retreat_open(mem, cur) is None
        assert p._hero_retreat_menu(menu(selected=2), mem, cur) is None
        assert not cur.get('hero_retreat')


@pytest.mark.parametrize('ours,theirs,current', [(None, None, False), (6, None, True),
    (None, 6, True), (0, 6, True), (True, 20, False)])
def test_hp_exception_never_invents_missing_armies_or_a_combat_advantage(ours, theirs, current):
    cur = pre_summon(); cur.update(ally_soldiers=ours, enemy_soldiers=theirs,
                                  card_soldiers_current=current)
    before = deepcopy(cur)
    assert rules._pre_summon_hp_advantage(cur)
    assert not p._healthy_melee_advantage(cur)  # the stronger old force claim stays false
    assert not p._hero_retreat_needed(cur)
    assert (cur['ally_soldiers'], cur['enemy_soldiers'], cur['card_soldiers_current']) == (
        before['ally_soldiers'], before['enemy_soldiers'], before['card_soldiers_current'])


@pytest.mark.parametrize('change', [
    {'ally': p.NAME}, {'ally': 'しゅじんこう'}, {'ally': '未確認'}, {'enemy': '未確認'},
    {'enemy': 'クイーン'}, {'side': 'defense'}, {'side': None},
    {'egg_battle': True, 'egg_retreat_needed': True}, {'card_summon_observed': True},
    {'hero_retreat': {'selected': 1}}, {'card_hp_unread': True},
    {'card_context_unclassified': True}, {'card_damage_rescue_needed': True},
    {'event_duel': True}, {'card_flow': {'stage': 'announce'}},
    {'planned_cards': ['ゼンマイン']}, {'own_egg_positive_observed': True},
    {'survival': {'exhausted': False}},
    {'survival': {'exhausted': True, 'cards_uncertain': True}},
    {'ally_hp': None}, {'enemy_hp': None}, {'ally_hp': True}, {'enemy_hp': False},
    {'ally_hp': 0}, {'enemy_hp': 0}, {'ally_hp': 60}, {'enemy_hp': 49},
    {'ally_hp': '59'}, {'enemy_hp': 40.0},
])
def test_pre_summon_hp_exception_does_not_override_special_unread_or_resource_guards(change):
    cur = pre_summon(); cur.update(change)
    assert not rules._pre_summon_hp_advantage(cur)
    assert p._hero_retreat_needed(cur) == rules._default_retreat_needed(deepcopy(cur))


@pytest.mark.parametrize('ally,hp,enemy_hp,advantage', [
    ('アマンディーヌ', 12, 5, False), ('アマンディーヌ', 13, 5, True),
    ('ゼウス', 18, 5, False), ('ゼウス', 19, 5, True),
])
def test_hp_advantage_keeps_the_critical_and_strong_general_floors(ally, hp, enemy_hp, advantage):
    cur = pre_summon(); cur.update(ally=ally, ally_hp=hp, enemy_hp=enemy_hp)
    assert rules._pre_summon_hp_advantage(cur) is advantage
    assert p._hero_retreat_needed(cur) is not advantage


def test_hp_exception_is_recomputed_after_damage_and_actual_summoning():
    import json
    cur = pre_summon()
    assert not p._hero_retreat_needed(cur)
    cur = json.loads(json.dumps(cur))
    cur['ally_hp'] = 41
    assert p._hero_retreat_needed(cur)
    cur.update(ally_hp=59, egg_battle=True, egg_retreat_needed=True)
    assert p._hero_retreat_needed(cur)

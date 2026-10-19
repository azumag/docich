"""Offline regressions for measured defenders across ownership observations."""
from copy import deepcopy

import pytest

from docich import hanjuku_chart as chart, hanjuku_policy as policy, hanjuku_reference as reference
from docich.hanjuku_egg_reference import general_max_hp
from test_hanjuku_dropper_carry import sortie

HOME = 'アルマムーン'
CUR = {'side': 'defense', 'castle': HOME, 'enemy': 'ガスパチョ',
       'enemy_hp': 30, 'ally_hp': 0, 'ally': 'どうし', 'step': None,
       'cards_used': [], 'card_evidence_version': 1}


def loss_state(castle=HOME):
    mem = {'chapter': 1, 'battle': {**CUR, 'castle': castle}, '_records': []}
    policy.battle_end(mem, 'map')
    policy.battle_end(mem, 'map')
    assert policy._sortie_defender(mem, {'target': castle}) == 'ガスパチョ'
    return mem


def carry_comparison(general=None):
    measured = loss_state()
    order, mem, inventory = sortie('1-A2')
    order = {**order, 'target': HOME}
    if general is not None:
        order['general'] = general
    # Fresh sortie inventory observation from the existing helper, with the
    # exact defender record obtained from battle_end, rather than a guessed foe.
    for key in ('castle_defenders', 'captured', 'lost'):
        mem[key] = deepcopy(measured[key])
    output = {}
    for label, apply_flags in [('before', False), ('after', True)]:
        branch = deepcopy(mem)
        if apply_flags:
            policy._apply_world_flags(branch, {HOME: 'enemy'})
        wanted = policy._deploy_cards(order, branch)
        selected = policy._dropper_card_inventory(branch, order, deepcopy(inventory), wanted)
        output[label] = {'defender': policy._sortie_defender(branch, order),
                         'selected': selected, 'kit': branch.get('dropper_card_kit'),
                         'cards': policy._deploy_cards(order, branch)}
    return output


def test_measured_home_capturer_survives_normal_enemy_world_flag():
    mem = loss_state()
    record = deepcopy(mem['castle_defenders'][HOME])
    policy._apply_world_flags(mem, {HOME: 'enemy'})
    assert mem.get('castle_defenders', {}).get(HOME) == record
    assert policy._sortie_defender(mem, {'target': HOME}) == 'ガスパチョ'


def test_nonhome_capture_control_survives_same_enemy_flag():
    mem = loss_state('ゴーメン')
    policy._apply_world_flags(mem, {'ゴーメン': 'enemy'})
    assert policy._sortie_defender(mem, {'target': 'ゴーメン'}) == 'ガスパチョ'


def test_exact_hero_carry_is_ineligible_even_before_flags():
    assert general_max_hp('ガスパチョ') == 37
    assert general_max_hp('しゅじんこう') == 90
    assert reference.egg_drop_plan(37, 90) == {'max_hp_sum': 127, 'threshold': 16}
    assert reference.egg_drop_value('グルミー') == 15
    result = carry_comparison()
    assert result['before']['defender'] == 'ガスパチョ'
    assert result['after']['defender'] == 'ガスパチョ'
    assert result['before']['selected'] is False
    assert result['after']['selected'] is False


def test_eligible_alternate_ally_carry_should_survive_same_enemy_flag():
    # Additional synthetic order control; not the user's unchanged hero order.
    assert general_max_hp('ココット') == 24
    assert reference.egg_drop_plan(37, 24) == {'max_hp_sum': 61, 'threshold': 14}
    result = carry_comparison('ココット')
    assert result['before']['selected'] is True
    assert result['before']['kit']['1-A2']['card'] == 'グルミー'
    assert result['after']['selected'] is True, result
    assert result['after']['kit'] == result['before']['kit']


@pytest.mark.parametrize('home_lost', [False, True])
def test_repeated_enemy_flags_preserve_the_known_home_capturer(home_lost):
    mem = loss_state()
    mem['home_lost'] = home_lost
    record = deepcopy(mem['castle_defenders'][HOME])
    # An older own-flag observation must not outrank the completed battle.
    mem['world_flags'] = {HOME: {'kind': 'own', 'tick': 1, 'chapter': 1}}
    for _ in range(3):
        policy._apply_world_flags(mem, {HOME: 'enemy'})
        assert mem['castle_defenders'][HOME] == record
        assert mem['home_lost'] is True and HOME in mem['lost']
        assert HOME not in mem['captured']


@pytest.mark.parametrize('ownership', [
    {}, {'home_lost': False}, {'captured': [HOME]},
    {'home_lost': True, 'lost': [HOME], 'captured': [HOME]},
])
def test_new_home_loss_invalidates_the_previous_defender(ownership):
    mem = {'chapter': 1, 'castle_defenders': {
        HOME: {'general': 'ガスパチョ', 'chapter': 1},
        'ゴーメン': {'general': 'ガルバンゾー', 'chapter': 1}}, **deepcopy(ownership)}
    policy._apply_world_flags(mem, {HOME: 'enemy'})
    assert HOME not in mem['castle_defenders']
    assert mem['castle_defenders']['ゴーメン']['general'] == 'ガルバンゾー'
    assert mem['home_lost'] is True and HOME in mem['lost']
    assert HOME not in mem['captured']


def test_own_flag_then_new_loss_invalidates_the_old_home_defender():
    mem = loss_state()
    policy._apply_world_flags(mem, {HOME: 'enemy'})
    policy._apply_world_flags(mem, {HOME: 'own'})
    assert mem['home_lost'] is False and HOME not in mem['lost']
    policy._apply_world_flags(mem, {HOME: 'enemy'})
    assert HOME not in mem['castle_defenders']


def test_new_nonhome_loss_invalidates_the_previous_defender():
    mem = loss_state('ゴーメン')
    mem['captured'] = ['ゴーメン']
    mem['lost'] = []
    policy._apply_world_flags(mem, {'ゴーメン': 'enemy'})
    assert 'ゴーメン' not in mem['castle_defenders']


def test_confirmed_enemy_roofs_still_invalidate_a_new_nonhome_loss():
    mem = loss_state('ゴーメン')
    mem['captured'] = ['ゴーメン']
    mem['lost'] = []
    roofs = [{'kind': kind, 'target': chart.castles(1)[castle]}
             for castle, kind in [('ゴーメン', 'enemy'), ('キカンドン', 'own')]]
    policy.observe_owners(mem, roofs, (0, 0))
    assert 'ゴーメン' in mem['castle_defenders']
    policy.observe_owners(mem, roofs, (0, 0))
    assert 'ゴーメン' not in mem['castle_defenders']


@pytest.mark.parametrize('castle', [HOME, 'ゴーメン'])
def test_attack_victory_still_invalidates_the_captured_castle_defender(castle):
    mem = loss_state(castle)
    mem['battle'] = {**CUR, 'castle': castle, 'side': 'attack',
                     'ally_hp': 40, 'enemy_hp': 0}
    policy.battle_end(mem, 'map')
    policy.battle_end(mem, 'map')
    assert castle not in mem['castle_defenders']
    assert castle in mem['captured'] and castle not in mem['lost']


def test_defense_victory_does_not_replace_the_record_with_the_attacker():
    mem = loss_state()
    record = deepcopy(mem['castle_defenders'][HOME])
    mem['battle'] = {**CUR, 'enemy': 'ガルバンゾー', 'ally_hp': 40, 'enemy_hp': 0}
    policy.battle_end(mem, 'map')
    policy.battle_end(mem, 'map')
    assert mem['castle_defenders'][HOME] == record
    assert mem['stats']['wins'] == 1


def test_new_defense_loss_replaces_the_record_with_the_new_capturer():
    mem = loss_state()
    mem.update(captured=[HOME], lost=[], home_lost=False)
    mem['battle'] = {**CUR, 'enemy': 'ガルバンゾー'}
    policy.battle_end(mem, 'map')
    policy.battle_end(mem, 'map')
    assert policy._sortie_defender(mem, {'target': HOME}) == 'ガルバンゾー'
    policy._apply_world_flags(mem, {HOME: 'enemy'})
    assert policy._sortie_defender(mem, {'target': HOME}) == 'ガルバンゾー'


def test_enemy_flags_around_defense_finalization_keep_the_new_capturer():
    mem = {'chapter': 1, 'battle': deepcopy(CUR), 'castle_defenders': {
        HOME: {'general': 'ガルバンゾー', 'chapter': 1}}, '_records': []}
    policy._apply_world_flags(mem, {HOME: 'enemy'})
    assert HOME not in mem['castle_defenders']
    assert mem['battle']['defeat_owner']['owner'] == 'enemy'
    policy.battle_end(mem, 'map')
    policy.battle_end(mem, 'map')
    policy._apply_world_flags(mem, {HOME: 'enemy'})
    assert policy._sortie_defender(mem, {'target': HOME}) == 'ガスパチョ'


@pytest.mark.parametrize('retained', [False, True])
def test_continued_or_retained_defense_does_not_record_a_capturer(retained):
    mem = {'chapter': 1, 'battle': deepcopy(CUR), '_records': []}
    if retained:
        policy._apply_world_flags(mem, {HOME: 'own'})
    policy.battle_end(mem, 'map', defense_continues=not retained)
    policy.battle_end(mem, 'map', defense_continues=not retained)
    assert HOME not in (mem.get('castle_defenders') or {})
    assert HOME not in (mem.get('lost') or [])


def test_chapter_transition_still_invalidates_all_defender_records():
    mem = loss_state()
    policy._enter_chapter(mem, 2, reason='measured chapter transition')
    assert 'castle_defenders' not in mem
    assert policy._sortie_defender(mem, {'target': HOME}) is None


def test_another_chapter_record_is_not_usable_after_an_enemy_flag():
    mem = loss_state()
    mem['castle_defenders'][HOME]['chapter'] = 2
    policy._apply_world_flags(mem, {HOME: 'enemy'})
    assert policy._sortie_defender(mem, {'target': HOME}) is None

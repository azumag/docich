"""No list-position, castle-level, or unknown-reading shortcut proves hero safety."""
from copy import deepcopy

import pytest

from docich import hanjuku_defense_read_model as model, hanjuku_chart as chart

ID = {'game': 'hanjuku-hero', 'runtime_id': 'read-model-test', 'generation': 1,
      'lease_id': 'read-model-lease'}
CONTEXT = {'identity': ID, 'chapter': 1, 'month': '1-6', 'tick': 100}
CASTLE = 'アルマムーン'


def receipts(names=('ゼウス', chart.HERO), hp=3, uses=0):
    scope = {**CONTEXT, 'castle': CASTLE, 'tick': 99}
    return ({**scope, 'kind': 'castle_status', 'owner': 'own', 'level': 5, 'general_count': len(names)},
            {**scope, 'kind': 'castle_generals', 'names': list(names), 'complete': True},
            {**scope, 'kind': 'general_status', 'general': chart.HERO, 'location': 'castle',
             'location_observed': True, 'hp': hp, 'max_hp': 90, 'uses': uses})


def project(cs, gs, hs, context=CONTEXT):
    return model.assess(context, CASTLE, castle_status=cs, castle_generals=gs, hero_status=hs)


def test_last_in_list_and_a_high_castle_level_never_prove_safe_interception_order():
    result = project(*receipts())
    assert result['hero_list_tail'] is True
    assert result['hero_defense_tail'] is None
    assert result['hero_protection_needed'] is True
    assert result['castle_level'] == 5 and result['general_count'] == 2
    assert result['reorder_actions'] == []


def test_a_lone_healthy_hero_is_still_a_protection_concern():
    result = project(*receipts((chart.HERO,), hp=90, uses=4))
    assert result['hero_protection_needed'] is True and result['reorder_actions'] == []


def test_healthy_hp_with_companions_is_unknown_safety_not_a_claim_that_the_hero_is_safe():
    result = project(*receipts(hp=90, uses=4))
    assert result['hero_protection_needed'] is None and result['hero_defense_tail'] is None


@pytest.mark.parametrize('change', [ {'identity': {**ID, 'generation': 2}},
    {'identity': {**ID, 'generation': True}}, {'identity': None}, {'month': '1-7'},
    {'chapter': True}, {'chapter': 2}, {'tick': 116} ])
def test_old_or_unknown_scope_does_not_reuse_a_guard_receipt(change):
    result = project(*receipts(), context={**CONTEXT, **change})
    assert result['hero_present'] is result['general_count'] is result['hero_protection_needed'] is None
    assert result['reorder_actions'] == []


def test_partial_or_global_rosters_do_not_prove_hero_absence_or_tail():
    cs, gs, hs = receipts()
    gs.update(names=['ゼウス'], complete=False)
    result = project(cs, gs, hs)
    assert result['hero_present'] is result['hero_list_tail'] is None
    gs.update(kind='global_roster', names=[chart.HERO, 'ゼウス'])
    assert project(cs, gs, hs)['hero_present'] is None


@pytest.mark.parametrize('field,value', [('hp', None), ('hp', True), ('max_hp', None),
                                       ('location_observed', False), ('general', 'ゼウス'),
                                       ('castle', 'キカンドン')])
def test_other_or_unread_status_never_supplies_a_current_hero_hp(field, value):
    cs, gs, hs = receipts(uses=4)
    hs[field] = value
    result = project(cs, gs, hs)
    assert result['hero_hp'] is None
    assert result['reorder_actions'] == []


def test_contradictory_castle_count_and_visible_names_are_not_confirmed_membership():
    cs, gs, hs = receipts()
    cs['general_count'] = 0
    assert project(cs, gs, hs)['hero_present'] is None


def test_a_name_is_not_present_after_a_new_complete_castle_list():
    result = project(*receipts(('ゼウス',), hp=3))
    assert result['hero_present'] is False and result['hero_hp'] is None
    assert result['hero_protection_needed'] is False


def test_assessment_does_not_change_receipts_or_emit_game_input():
    values = receipts()
    before = deepcopy(values)
    assert project(*values)['reorder_actions'] == []
    assert values == before


@pytest.mark.parametrize('context', [None, [], {}, {**CONTEXT, 'chapter': []},
                                    {**CONTEXT, 'chapter': {}}, {**CONTEXT, 'chapter': 1.0}])
def test_malformed_context_is_an_unknown_receipt_not_an_exception(context):
    result = project(*receipts(), context=context)
    assert result['hero_present'] is result['hero_protection_needed'] is None
    assert result['reorder_actions'] == []


@pytest.mark.parametrize('names', [[{}], [[]], [None], [True], [''], ['a b'], ['\ufffd'],
                                  [chart.HERO, chart.HERO]])
def test_malformed_or_duplicate_names_do_not_confirm_membership(names):
    cs, gs, hs = receipts()
    gs['names'] = names
    assert project(cs, gs, hs)['hero_present'] is None


@pytest.mark.parametrize('castle', [None, [], {}, True])
def test_malformed_castle_is_not_a_current_owned_castle(castle):
    cs, gs, hs = receipts()
    result = model.assess(CONTEXT, castle, castle_status=cs, castle_generals=gs, hero_status=hs)
    assert result['hero_present'] is result['hero_protection_needed'] is None

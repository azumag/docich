"""An observed substitute is one execution, not a mutable chart actor.

Confirmation/target UI below are doubles. They do not prove a native dispatch.
"""
from copy import deepcopy
import json

import pytest

from docich import hanjuku_chart as chart, hanjuku_policy as p
from docich.hanjuku.os_layer.screen import Screen
from docich.hanjuku.strategy.execution import confirm_departure_order
from test_hanjuku_sortie_evidence import actor_panel, measured_loaded_sortie


def memory():
    return {'chapter': 1, 'tick': 20, '_run_identity': {
        'game': 'hanjuku-hero', 'runtime_id': 'test-runtime', 'generation': 741,
        'lease_id': 'test-lease'},
        'active': '1-A3', 'orders': {'1-A3': 'pending'},
        'general_override': {'1-A3': 'ゼウス'}, 'source_override': {'1-A3': 'カストーラ'},
        'order_context': {}, 'garrison': {'カストーラ': ['ゼウス', 'ヴィーナス']}}


def confirm(mem):
    order = next(o for o in chart.orders(1) if o['step'] == '1-A3')
    frozen = confirm_departure_order(order, mem, 'ゼウス', 'カストーラ', ['クースカン'])
    assert frozen is not None
    mem['order_context']['1-A3'] = {'actual_general': 'ゼウス', 'departure_order': frozen,
                                  'observed_metric': {'general': 'ゼウス', 'cards': ['クースカン']}}
    mem['sortie_attempt'] = {'step': '1-A3', 'target_seen': True, 'confirmation_tick': 20}
    return order


def interrupt(mem):
    p.observe_sortie_transition(Screen([], None, '', kind='defense_started'), mem, 'map_target')
    assert mem['orders']['1-A3'] == 'launched_unconfirmed'


def mutate_plan(mem):
    old = next(o for o in chart.orders(1) if o['step'] == '1-A3')
    mem['chart_plan'] = {'orders': [{**old, 'general': 'どうし', 'source': 'アルマムーン',
                                   'target': 'けっかい', 'cards': ['イッテツーン']}]}
    mem['general_override']['1-A3'] = 'ヴィーナス'
    mem['source_override']['1-A3'] = 'ゴーメン'
    mem['card_override'] = {'1-A3': ['イッテツーン']}
    mem['rare_card_kit'] = {'1-A3': ['キャトルミュー']}


def assert_frozen(mem):
    order = p._order_for_step(mem, '1-A3')
    assert order['general'] == p._sortie_general(order, mem) == 'ゼウス'
    assert order['source'] == p._source(order, mem) == 'カストーラ'
    assert order['target'] == 'スペンソニア'
    assert p._deploy_cards(order, mem) == ['クースカン']
    return order


def test_confirmed_attempt_does_not_follow_later_plan_overrides():
    mem = memory(); confirm(mem); mutate_plan(mem)
    assert_frozen(json.loads(json.dumps(mem)))


def test_unconfirmed_departure_saves_actual_actor_source_and_unknown_destination():
    mem = memory(); confirm(mem); mutate_plan(mem); interrupt(mem)
    assert mem['launched_orders']['1-A3']['general'] == 'ゼウス'
    assert mem['launched_orders']['1-A3']['source'] == 'カストーラ'
    assert mem['sorties']['1-A3']['target'] is None
    assert mem['sorties']['1-A3']['planned_target'] == 'スペンソニア'
    assert_frozen(json.loads(json.dumps(mem)))


def test_independent_saved_binding_survives_mutation_of_confirmation_context():
    mem = memory(); confirm(mem); interrupt(mem)
    mem['sortie_attempt'] = None
    mem['order_context']['1-A3']['actual_general'] = 'どうし'
    mem['order_context']['1-A3']['departure_order']['general'] = 'どうし'
    mutate_plan(mem); assert_frozen(mem)


def test_selected_target_saves_actual_actor_even_after_plan_replacement(monkeypatch):
    mem = memory(); confirm(mem); mutate_plan(mem)
    monkeypatch.setattr(p, 'nav_step', lambda *args: 'arrived')
    monkeypatch.setattr(p, '_target_roof_under_marker', lambda *args: True)
    monkeypatch.setattr(p, '_want_y_jump', lambda *args: False)
    mem['cursor'] = list(chart.castles(1)['スペンソニア'])
    actions = p.target_step(Screen([], None, '', kind='map_target'), mem, None)
    assert actions == [p.pad('a')]
    assert mem['sorties']['1-A3']['status'] == 'en_route'
    assert mem['sorties']['1-A3']['source'] == 'カストーラ'
    assert mem['launched_orders']['1-A3']['general'] == 'ゼウス'
    assert_frozen(mem)


def test_marker_return_is_same_execution_but_new_retry_requires_fresh_confirmation():
    mem = memory(); confirm(mem); interrupt(mem); mutate_plan(mem)
    p.observe_sortie_transition(Screen([], None, '', kind='map_target'), mem, 'unknown')
    assert mem['orders']['1-A3'] == 'pending'
    assert_frozen(mem)
    # A lost/cancelled sortie can be replanned. No old confirmation owns it.
    mem.pop('sortie_attempt'); mem['sorties'] = {}
    assert p._order_for_step(mem, '1-A3')['target'] == 'けっかい'
    assert p._sortie_general(p._order_for_step(mem, '1-A3'), mem) == 'ヴィーナス'


@pytest.mark.parametrize('change', ['runtime_id', 'generation', 'lease_id', 'chapter',
                                   'actor', 'source', 'cards', 'serial', 'future'])
def test_foreign_or_corrupt_saved_execution_cannot_fall_back_to_the_hero(change):
    mem = memory(); confirm(mem); interrupt(mem); mem.pop('sortie_attempt'); mutate_plan(mem)
    if change in ('runtime_id', 'generation', 'lease_id'):
        mem['_run_identity'][change] = 742 if change == 'generation' else 'different'
    elif change == 'chapter': mem['chapter'] = 2
    elif change == 'future': mem['tick'] = 19
    elif change == 'serial': mem['sorties']['1-A3']['confirmation_tick'] = True
    else:
        key = {'actor': 'general', 'source': 'source', 'cards': 'cards'}[change]
        mem['launched_orders']['1-A3'][key] = {'actor': 'どうし', 'source': 'ゴーメン',
                                            'cards': ['イッテツーン']}[change]
    assert p._order_for_step(mem, '1-A3') is None
    assert not p._tactics(mem, '1-A3')


def test_spent_copies_and_actual_kit_are_not_replaced_by_later_card_plans():
    mem = memory(); confirm(mem); interrupt(mem); mutate_plan(mem)
    mem['kit_spent'] = {'1-A3': ['クースカン']}
    assert p._deploy_cards(p._order_for_step(mem, '1-A3'), mem) == []


def test_legacy_context_can_record_metadata_but_does_not_gain_a_bound_execution():
    mem = memory(); mem.pop('_run_identity')
    mem['order_context']['1-A3'] = {'actual_general': 'ゼウス'}
    mem['sortie_attempt'] = {'step': '1-A3', 'target_seen': True}
    interrupt(mem)
    assert mem['launched_orders']['1-A3']['general'] == 'ゼウス'
    assert 'departure_binding' not in mem['launched_orders']['1-A3']


@pytest.mark.parametrize('field,value', [('general','unknown'), ('source','unknown'),
                                       ('cards',None), ('cards',['unknown']), ('cards',['クースカン']*4)])
def test_unknown_confirmation_is_not_a_binding(field, value):
    mem = memory(); order = next(o for o in chart.orders(1) if o['step']=='1-A3')
    args = {'general':'ゼウス', 'source':'カストーラ', 'cards':['クースカン']}; args[field] = value
    assert confirm_departure_order(order, mem, **args) is None


def test_real_confirmation_handler_binds_named_actor_actual_source_and_carried_copies():
    mem = memory(); mem.update(picked=['クースカン'], captured=['カストーラ'],
                                card_override={'1-A3':['クースカン']})
    panel = actor_panel(measured_loaded_sortie(('クースカン',)), 'ゼウス')
    assert p.deploy_step(panel, mem) == [p.pad('a')]
    stored = mem['order_context']['1-A3']['departure_order']
    assert stored['general'] == 'ゼウス' and stored['source'] == 'カストーラ'
    assert stored['cards'] == ['クースカン'] and 'departure_binding' in stored
    assert mem['sortie_attempt']['target_seen'] is False
    assert not mem.get('sorties')  # planned confirmation is not actual departure


@pytest.mark.parametrize('name', ['どうし', 'unknown', 'ゼウス□'])
def test_scoped_ordinary_sortie_checks_actual_name_before_confirming(name):
    mem = memory(); mem.update(picked=['クースカン'], captured=['カストーラ'],
                                card_override={'1-A3':['クースカン']})
    panel = actor_panel(measured_loaded_sortie(('クースカン',)), name)
    assert p.deploy_step(panel, mem) != [p.pad('a')]
    assert not mem.get('sortie_attempt')
    assert not any(c.get('departure_order') for c in mem['order_context'].values())


def test_boolean_one_marker_is_not_the_integer_one_execution():
    mem = memory(); mem['tick'] = 1
    order = next(o for o in chart.orders(1) if o['step']=='1-A3')
    frozen = confirm_departure_order(order, mem, 'ゼウス', 'カストーラ', [])
    mem['order_context']['1-A3'] = {'departure_order': frozen}
    mem['sortie_attempt'] = {'step':'1-A3', 'confirmation_tick':True}
    assert p._order_for_step(mem, '1-A3') is None


def test_mutated_bound_copy_never_borrows_a_later_live_actor_source_or_kit():
    mem = memory(); confirm(mem); mutate_plan(mem)
    order = p._order_for_step(mem, '1-A3'); order['general'] = 'どうし'
    assert p._sortie_general(order, mem) is None
    assert p._source(order, mem) is None
    assert p._deploy_cards(order, mem) == []


def test_later_rare_and_override_kits_do_not_rewrite_saved_tactic_roles():
    mem = memory(); confirm(mem); interrupt(mem); mem.pop('sortie_attempt')
    original = deepcopy(p._tactics(mem, '1-A3'))
    mutate_plan(mem)
    assert p._tactics(mem, '1-A3') == original
    assert not any(t['card'] == 'キャトルミュー' and t.get('step') == '1-A3'
                   for t in p._tactics(mem, '1-A3'))


def test_strong_card_role_is_same_owner_and_same_execution():
    mem = memory(); mem['strong_card_kit'] = {'1-A3':'クースカン'}
    confirm(mem); interrupt(mem); mem.pop('sortie_attempt')
    mem['strong_card_kit']['1-A3'] = 'イッテツーン'
    assert p._strong_cards_carried(mem, {'step':'1-A3','ally':'ゼウス'}) == ['クースカン']
    assert p._strong_cards_carried(mem, {'step':'1-A3','ally':'どうし'}) == []
    mem['kit_spent'] = {'1-A3':['クースカン']}
    assert p._strong_cards_carried(mem, {'step':'1-A3','ally':'ゼウス'}) == []


def test_boolean_generation_one_is_not_an_integer_run_identity():
    mem = memory(); mem['_run_identity']['generation'] = 1
    confirm(mem); interrupt(mem); mem.pop('sortie_attempt')
    mem['launched_orders']['1-A3']['departure_binding']['identity']['generation'] = True
    assert p._order_for_step(mem, '1-A3') is None

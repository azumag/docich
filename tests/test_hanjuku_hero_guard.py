"""No boss hero fallback; measured healthy melee is not resource exhaustion."""
from copy import deepcopy
from pathlib import Path

import pytest
from docich import hanjuku_chart as chart, hanjuku_policy as p
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import Screen, parse
from test_hanjuku_sortie_evidence import selected_list, actor_panel, measured_card_select, measured_loaded_sortie
from test_hanjuku_survival import menu

F = Path(__file__).parent / 'fixtures' / 'hanjuku'


def boss_memory(chapter=2):
    order = next(o for o in chart.orders(chapter) if o['target'] == chart.boss_castle(chapter))
    return order, {'chapter': chapter, 'active': order['step'], 'tick': 100,
                   'orders': {o['step']: 'launched' for o in chart.orders(chapter)},
                   'captured': list(chart.castles(chapter)), 'picked': []}


@pytest.mark.parametrize('names', [[p.NAME]])
def test_readable_boss_list_without_any_companion_never_selects_hero(names):
    order, mem = boss_memory(); mem['orders'][order['step']] = 'pending'
    actions = p.deploy_step(selected_list(names), mem)
    assert actions == [p.pad('b'), p.pad('b')]
    assert mem['active'] is None and mem['orders'][order['step']] == 'pending'
    assert not mem.get('sortie_general') and not mem.get('order_context')
    assert mem['_records'][-1]['decision'] == 'boss_companion_required'


@pytest.mark.parametrize('names', [[p.NAME, 'シェーブル'], ['シェーブル']])
def test_weak_but_fit_companion_is_sent_and_the_hero_never_is(names):
    """Owner (2026-10-14): an unfavourable substitute still attacks the boss.

    Waiting for a comparable general left the boss castle unattacked for a
    whole run. The hero is still never the actor (主人公boss禁止).
    """
    order, mem = boss_memory(); mem['orders'][order['step']] = 'pending'
    actions = p.deploy_step(selected_list(names), mem)
    assert p.pad('b') not in actions
    assert mem['general_override'][order['step']] == 'シェーブル'
    assert p.NAME not in mem['general_override'].values()


@pytest.mark.parametrize('kind', ['card_select', 'sortie_confirm'])
def test_hotloaded_hero_selection_backs_out_before_departure(kind):
    order, mem = boss_memory(); mem['sortie_general'] = {order['step']: p.NAME}
    mem['orders'][order['step']] = 'pending'
    screen = measured_card_select() if kind == 'card_select' else measured_loaded_sortie()
    assert p.deploy_step(actor_panel(screen, p.NAME), mem) == [p.pad('b'), p.pad('b')]
    assert not mem.get('order_context') and not mem.get('sorties')


def test_adequate_companion_elsewhere_redirects_source_without_inventing_departure():
    order, mem = boss_memory(); mem['orders'][order['step']] = 'pending'
    mem['garrison'] = {order['source']: [p.NAME], chart.home_castle(2): ['ゼウス', p.NAME]}
    mem['house_eggs'] = {p.NAME: {'broken': True}}
    assert p.next_order(mem) == order
    assert mem['source_override'][order['step']] == chart.home_castle(2)
    assert not mem.get('sortie_general') and not mem.get('sorties')
    assert p.deploy_step(selected_list(['ゼウス', p.NAME]), mem) == [p.pad('a')]
    assert mem['sortie_general'][order['step']] == 'ゼウス'


@pytest.mark.parametrize('status', [{'broken': True}, {'uses': 0}, {'hp': 10, 'max_hp': 85}])
def test_companion_preparation_waits_for_known_injury_or_egg_failure(status):
    order, mem = boss_memory(); mem['orders'][order['step']] = 'pending'
    mem['garrison'] = {order['source']: [p.NAME, 'ゼウス']}
    mem['house_eggs'] = {'ゼウス': status}
    assert p.next_order(mem) is None
    assert not mem.get('sortie_general')


def test_busy_unknown_location_and_duplicate_names_cannot_license_substitute():
    order, mem = boss_memory()
    mem['sorties'] = {'old': {'general': 'ゼウス', 'tick': 99, 'status': 'en_route'}}
    assert p._order_hero_alternatives(order, mem, [p.NAME, 'ゼウス']) == []
    mem.pop('sorties'); mem['general_location_unknown'] = ['ゼウス']
    assert p._order_hero_alternatives(order, mem, ['ゼウス']) == []
    mem.pop('general_location_unknown')
    assert p._order_hero_alternatives(order, mem, ['ゼウス', 'ゼウス']) == []


def test_unconfirmed_existing_departure_keeps_source_reconciliation(monkeypatch):
    order, mem = boss_memory(); step = order['step']
    mem['orders'][step] = 'launched_unconfirmed'
    mem['order_context'] = {step: {'actual_general': p.NAME}}
    monkeypatch.setattr(p, '_verify_sortie_source', lambda *args: [])
    before = deepcopy(mem['order_context'])
    assert p.deploy_step(selected_list([p.NAME]), mem) == []
    assert mem['order_context'] == before


def test_hotloaded_unsubmitted_hero_target_never_sends_departure(monkeypatch):
    order, mem = boss_memory(); step = order['step']
    mem['orders'][step] = 'pending'
    mem['sortie_general'] = {step: p.NAME}
    mem['order_context'] = {step: {'actual_general': p.NAME,
        'observed_metric': {'cards': sorted(order['cards'])}}}
    mem['sortie_attempt'] = {'step': step, 'target_seen': True}
    monkeypatch.setattr(p, 'nav_step', lambda *args: pytest.fail('hero target navigation'))
    assert p.target_step(Screen([], None, '', kind='map_target'), mem, None) == [p.pad('b'), p.pad('b')]
    assert not mem.get('sortie_attempt') and not mem.get('sorties')
    assert mem['orders'][step] == 'pending'


def test_explicit_companion_boss_plan_cannot_confirm_an_actual_hero_panel():
    order, mem = boss_memory(); step = order['step']
    mem['chart_plan'] = {'orders': [{**order, 'general': 'ゼウス'}]}
    mem['sortie_general'] = {step: 'ゼウス'}
    mem['orders'][step] = 'pending'
    assert p.deploy_step(actor_panel(measured_loaded_sortie(), p.NAME), mem) == [p.pad('b'), p.pad('b')]
    assert not mem.get('order_context')


@pytest.mark.parametrize('file,digest', [
    ('g713-venus-cashew-healthy.png', '14fdb297d123fdf11d19061edf34eaee0f53a403bab8538bc98a867151bd8cb2'),
    ('g713-zeus-cumin-healthy.png', '0b286956a504901e79bf0eee6e3459104b567d3bb75d82f01c1317b078b2a889'),
])
def test_native_healthy_advantage_does_not_retreat_after_resource_probe(file, digest):
    frame = read_png(F / file); assert frame.digest() == digest
    screen = parse(frame); b = screen.battle
    cur = {'ally': b.ally, 'enemy': b.enemy, 'ally_hp': b.ally_hp, 'enemy_hp': b.enemy_hp,
           'start_ally_hp': b.ally_hp, 'start_enemy_hp': b.enemy_hp, 'side': 'attack',
           'planned_cards': [], 'survival': {'exhausted': True, 'cards_uncertain': False}}
    mem = {'chapter': 1, 'battle': cur}
    p._card_battle_reading(screen, cur, mem)
    assert p._unarmed_clash_risk(cur)  # keep inspecting the enemy egg
    assert p._healthy_melee_advantage(cur)
    assert not p._hero_retreat_needed(cur)
    assert p._hero_retreat_open(mem, cur) is None
    assert p._hero_retreat_menu(menu(selected=2), mem, cur) is None
    assert not cur.get('hero_retreat')


@pytest.mark.parametrize('change', [
    {'ally_hp': 12}, {'card_soldiers_current': False}, {'ally_soldiers': None},
    {'enemy_soldiers': None}, {'enemy_soldiers': 20}, {'card_hp_unread': True},
    {'egg_battle': True}, {'card_summon_observed': True}, {'ally': p.NAME},
])
def test_unknown_or_dangerous_state_keeps_existing_retreat_protection(change):
    cur = {'ally': 'ヴィーナス', 'enemy': 'カシュー', 'ally_hp': 82, 'enemy_hp': 19,
           'start_ally_hp': 82, 'start_enemy_hp': 19, 'side': 'attack',
           'planned_cards': [], 'card_soldiers_current': True,
           'ally_soldiers': 2, 'enemy_soldiers': 3,
           'survival': {'exhausted': True, 'cards_uncertain': False}, **change}
    assert not p._healthy_melee_advantage(cur)
    assert p._hero_retreat_needed(cur)

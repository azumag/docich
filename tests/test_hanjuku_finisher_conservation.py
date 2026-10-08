"""Conserve firepower only among observed, proven-lethal automatic choices.

Synthetic panels exercise the existing damage gate and real menu policy.
Selection is not a consumption, delivered input, kill, or live-run receipt.
"""
from copy import deepcopy

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku_screen import Battle
from test_hanjuku_card_damage_gate import memory as card_memory, cards


def memory(*, hp=27, soldiers=(0, 0), ally_hp=90, survival=True, card='ブラッキー'):
    mem = card_memory(card, enemy='ソーピニヨン', enemy_hp=hp,
                      ally='どうし', ally_hp=ally_hp, soldiers=soldiers)
    if survival:
        cur = mem['battle']
        cur['planned_cards'] = []
        cur['card_flow'] = {'card': None, 'stage': 'list', 'survival': True, 'list_ticks': 0}
        p._survival_state(mem, cur)
    return mem


def choose(mem, names):
    cur = mem['battle']
    eligible = p._card_gate_alternatives(mem, cur, names)
    return p._card_gate_choice(mem, cur, eligible, names)


@pytest.mark.parametrize('survival', [False, True])
def test_both_automatic_routes_save_cattle_myu_when_zenmain_finishes(survival):
    mem = memory(survival=survival)
    names = ['ブラッキー', 'キャトルミュー', 'ゼンマイン']
    assert p.card_list_step(cards(names), mem) == [p.pad('down')]
    cur = mem['battle']
    # Chart replanning stores the alternative immediately; survival stores it
    # only at the selected row, so use the assessment before confirmation.
    assert cur['card_assessment']['card'] == 'ゼンマイン'
    assert cur['cards_selected'] == cur['cards_used'] == []
    assert p.card_list_step(cards(names, 2), mem) == [p.pad('a')]
    assert cur['cards_selected'] == ['ゼンマイン']
    assert cur['cards_used'] == [] and not mem.get('kit_spent')
    assert cur['card_assessment']['lethal'] is True
    assert 'キャトルミュー' not in cur['cards_selected']


@pytest.mark.parametrize('hp,enemy_soldiers,choice', [
    (22, 1, 'ゼンマイン'), (23, 1, 'ミックミー'),
    (32, 0, 'ゼンマイン'), (33, 0, 'ミックミー'),
    (27, 4, 'キャトルミュー'), (27, 6, 'キャトルミュー'),
])
def test_smallest_finisher_is_computed_after_enemy_soldier_absorption(hp, enemy_soldiers, choice):
    mem = memory(hp=hp, soldiers=(0, enemy_soldiers))
    assert choose(mem, ['キャトルミュー', 'ミックミー', 'ゼンマイン']) == choice
    assessment = mem['battle']['card_assessment']
    assert assessment['card'] == choice and assessment['lethal'] is True
    assert assessment['remaining_hp_upper'] == 0
    assert assessment['enemy_soldier_hp_upper'] == 10 * enemy_soldiers


@pytest.mark.parametrize('soldiers', [None, (None, None), (0, None)])
def test_unread_armies_never_license_an_insufficient_finisher(soldiers):
    mem = memory(soldiers=soldiers)
    assert choose(mem, ['キャトルミュー', 'ゼンマイン']) == 'キャトルミュー'
    assert mem['battle']['card_assessment']['enemy_soldier_hp_upper'] == 60


def test_old_zero_soldiers_cannot_make_a_cheap_card_lethal():
    mem = memory()
    mem['battle'].pop('card_soldiers_current')
    assert choose(mem, ['キャトルミュー', 'ゼンマイン']) == 'キャトルミュー'


def test_own_soldier_bonus_can_make_a_smaller_card_sufficient():
    mem = memory(hp=27, soldiers=(6, 0))
    assert choose(mem, ['キャトルミュー', 'ミックミー', 'グリンボー']) == 'グリンボー'
    assert mem['battle']['card_assessment']['raw_damage_min'] == 38


@pytest.mark.parametrize('hp', [1, 12])
def test_critical_hp_keeps_existing_rescue_priority_without_conservation(hp):
    mem = memory(ally_hp=hp)
    assert choose(mem, ['キャトルミュー', 'ゼンマイン']) == 'キャトルミュー'


def test_above_existing_critical_threshold_allows_conservation():
    mem = memory(ally_hp=13)
    assert choose(mem, ['キャトルミュー', 'ゼンマイン']) == 'ゼンマイン'


@pytest.mark.parametrize('hp', [1, 12, 89])
def test_needed_healing_still_precedes_all_finishers(hp):
    mem = memory(ally_hp=hp)
    assert choose(mem, ['キャトルミュー', 'エンジェリン', 'ゼンマイン']) == 'エンジェリン'


def test_full_health_does_not_waste_healing_before_a_sufficient_finisher():
    mem = memory()
    assert choose(mem, ['キャトルミュー', 'エンジェリン', 'ゼンマイン']) == 'ゼンマイン'


def test_explicit_charted_card_is_not_replaced_by_automatic_conservation():
    mem = memory(survival=False, card='キャトルミュー')
    assert p.card_list_step(cards(['キャトルミュー', 'ゼンマイン']), mem) == [p.pad('a')]
    assert mem['battle']['cards_selected'] == ['キャトルミュー']
    assert mem['battle']['cards_used'] == []


def test_chart_inventory_alone_cannot_supply_an_unlisted_small_finisher():
    mem = memory()
    mem['battle']['planned_cards'] = ['ゼンマイン']
    assert choose(mem, ['キャトルミュー']) == 'キャトルミュー'


def test_a_later_live_panel_rechecks_sufficiency_before_final_a():
    mem = memory()
    names = ['キャトルミュー', 'ゼンマイン']
    assert p.card_list_step(cards(names), mem) == [p.pad('down')]
    screen = cards(names, 1)
    screen.battle = Battle('ソーピニヨン', 27, 'どうし', 90)
    screen.field_soldiers = (0, 6)
    assert p.card_list_step(screen, mem) == [p.pad('up')]
    assert mem['battle']['cards_selected'] == mem['battle']['cards_used'] == []
    assert mem['battle']['card_assessment']['card'] == 'キャトルミュー'


def test_missing_cursor_never_becomes_permission_to_select_the_smaller_card():
    mem = memory()
    names = ['キャトルミュー', 'ゼンマイン']
    assert p.card_list_step(cards(names, None), mem) != [p.pad('a')]
    assert mem['battle']['cards_selected'] == mem['battle']['cards_used'] == []


def test_summoned_target_keeps_existing_priority_not_saved_general_hp_math():
    mem = memory()
    mem['battle']['card_summon_observed'] = True
    assert choose(mem, ['キャトルミュー', 'ゼンマイン']) == 'キャトルミュー'
    assert mem['battle']['card_assessment']['reason'] == 'summon_already_observed'
    assert mem['battle']['card_assessment']['lethal'] is None


def test_boss_general_uses_boss_damage_not_the_ordinary_general_column():
    mem = card_memory(enemy='クイーン', enemy_hp=20, ally='どうし', ally_hp=90,
                      soldiers=(0, 0))
    assert choose(mem, ['キャトルミュー', 'ミックミー', 'ゼンマイン']) == 'ミックミー'
    assert mem['battle']['card_assessment']['target_kind'] == 'boss_general'
    assert mem['battle']['card_assessment']['raw_damage_min'] == 32


def test_equal_damage_keeps_existing_tiebreaker_and_is_order_independent():
    mem = memory(hp=27)
    # With no ally soldiers these are both 32 damage. Keep existing egg-drop
    # and survival-priority ties rather than letting arbitrary list order win.
    names = ['グリンボー', 'ゼンマイン']
    expected = p._rescue_card(names, mem['battle'])
    assert choose(mem, names) == expected
    assert choose(mem, list(reversed(names))) == expected


def test_decision_does_not_invent_stock_consumption_or_change_health():
    mem = memory()
    before = deepcopy(mem)
    assert choose(mem, ['キャトルミュー', 'ゼンマイン']) == 'ゼンマイン'
    # Existing assessment fields are the only side effect of evaluating gates.
    mem.pop('last_card_assessment', None)
    mem['battle'].pop('card_assessment', None)
    before.pop('last_card_assessment', None)
    before['battle'].pop('card_assessment', None)
    assert mem == before

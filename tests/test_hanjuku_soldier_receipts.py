"""Monthly army totals use a paid refill once, across persisted observations."""
from copy import deepcopy

import pytest

from docich import hanjuku_policy as policy
from docich.hanjuku_bot import decide
from docich.hanjuku_font import TextLine
from docich.hanjuku_screen import Screen, parse
from test_hanjuku_chart_bot import Canvas, MONTH_GRID, month_canvas


def memory(count=40, seen_month='1-8'):
    mem = {'chapter': 1, 'month': '1-8', 'gold': 79,
           'shop': {'key': '1-8', 'items': [], 'merchant_done': True,
                    'soldiers': 24, 'soldiers_done': False, 'gold_start': 79,
                    'egg': None, 'recruit': None, 'chikujou': 'skipped'}}
    if count is not None:
        mem.update(soldiers_seen=count, soldiers_seen_key=seen_month)
    return mem


def quantity(gold=79):
    cells = tuple((24 + i * 8, ch) for i, ch in enumerate('かわりますぞ'))
    return Screen(lines=[TextLine(199, cells + ((216, '2'), (224, '4')))],
                  hand=(186, 193, 204, 206), kind='month_menu', text='じゅうじキー',
                  header={'year': 1, 'month': 8, 'gold': gold})


def commit(mem):
    assert policy.quantity_step(quantity(), mem, soldiers=True) == [policy.pad('a')]
    assert mem['shop']['soldier_receipt']['status'] == 'pending'


def returned(gold=55, month=8):
    return parse(month_canvas(gold, month=month))


def test_paid_refill_is_added_once_and_keeps_totals_above_purchase_cap():
    mem = memory(count=110)
    commit(mem)
    assert mem['soldiers_seen'] == 110  # sending A is only an intention
    restored = deepcopy(mem)  # the next observation is a fresh Python process
    policy.observe_events(returned(), restored)
    assert restored['soldiers_seen'] == 134
    assert restored['soldiers_seen_key'] == '1-8'
    assert restored['soldiers_seen_basis'] == 'observed_plus_paid_refill'
    assert restored['shop']['soldier_receipt']['status'] == 'paid'
    policy.observe_events(returned(), restored)
    assert restored['soldiers_seen'] == 134
    assert sum(r['decision'] == 'soldier_refill_receipt' for r in restored['_records']) == 1
    assert policy.quantity_step(quantity(), restored, soldiers=True) == [policy.pad('b')]


def test_repeated_quantity_frame_does_not_add_or_replace_original_receipt():
    mem = memory()
    commit(mem)
    receipt = deepcopy(mem['shop']['soldier_receipt'])
    for _ in range(3):
        policy.observe_events(quantity(), mem)
        assert policy.quantity_step(quantity(), mem, soldiers=True) == [policy.pad('a')]
    assert mem['soldiers_seen'] == 40
    assert mem['shop']['soldier_receipt'] == receipt
    assert sum(r['decision'] == 'soldier_refill' for r in mem['_records']) == 1


@pytest.mark.parametrize('count,seen_month', [(None, None), (40, '1-7')])
def test_paid_quantity_cannot_create_a_total_from_missing_or_old_count(count, seen_month):
    mem = memory(count, seen_month)
    commit(mem)
    policy.observe_events(returned(), mem)
    assert mem.get('soldiers_seen') == count
    assert mem.get('soldiers_seen_key') is None
    assert mem['shop']['soldier_receipt']['status'] == 'paid'


@pytest.mark.parametrize('gold', [54, 80])
def test_wrong_payment_never_adds_planned_soldiers(gold):
    mem = memory()
    commit(mem)
    policy.observe_events(returned(gold), mem)
    assert mem['soldiers_seen'] == 40
    assert mem.get('soldiers_seen_key') is None
    assert mem['shop']['soldier_receipt']['status'] == 'unverified'


@pytest.mark.parametrize('unreadable', [False, True])
def test_unchanged_or_unreadable_balance_waits_then_invalidates_old_total(unreadable):
    mem = memory()
    commit(mem)
    screen = returned(79)
    if unreadable:
        screen.header = None
    for _ in range(policy.MONTH_SUB_MENU_WAIT - 1):
        policy.observe_events(screen, mem)
        assert mem['shop']['soldier_receipt']['status'] == 'pending'
        assert policy.month_step(screen, mem) == []
    policy.observe_events(screen, mem)
    assert mem['shop']['soldier_receipt']['status'] == 'unverified'
    assert mem['soldiers_seen'] == 40
    assert mem.get('soldiers_seen_key') is None


def test_new_month_cannot_mistake_unrelated_gold_loss_for_soldier_payment():
    mem = memory()
    commit(mem)
    policy.observe_events(returned(month=9), mem)
    assert mem['soldiers_seen'] == 40
    assert mem.get('soldiers_seen_key') is None
    assert mem['shop']['soldier_receipt']['status'] == 'unverified'


def test_return_to_field_without_a_receipt_does_not_claim_purchased_soldiers():
    mem = memory()
    commit(mem)
    policy.observe_events(Screen(lines=[], hand=None, text='', kind='map'), mem)
    assert mem['soldiers_seen'] == 40
    assert mem.get('soldiers_seen_key') is None


def test_direct_total_after_purchase_wins_over_arithmetic_and_is_not_added_twice():
    mem = memory()
    commit(mem)
    screen = returned()
    screen.text += 'げんざいわがぐんのへいしすうは６２めいです'
    policy.observe_events(screen, mem)
    assert mem['soldiers_seen'] == 62
    assert mem['soldiers_seen_basis'] == 'screen_total'
    policy.observe_events(screen, mem)
    assert mem['soldiers_seen'] == 62


def test_paid_total_from_an_intermediate_result_survives_menu_return():
    mem = memory()
    commit(mem)
    result = Screen(lines=[], hand=None, kind='text',
                    text='へいしすうは62めいです',
                    header={'year': 1, 'month': 8, 'gold': 55})
    policy.observe_events(result, mem)
    assert mem['soldiers_seen'] == 62
    assert policy.quantity_step(quantity(gold=55), mem, soldiers=True) == [policy.pad('b')]
    policy.observe_events(returned(), mem)
    assert mem['soldiers_seen'] == 62
    assert mem['soldiers_seen_basis'] == 'screen_total'


def test_pre_payment_total_does_not_override_the_paid_addition():
    mem = memory()
    commit(mem)
    result = Screen(lines=[], hand=None, kind='text', text='へいしすうは40めいです',
                    header={'year': 1, 'month': 8, 'gold': 79})
    policy.observe_events(result, mem)
    policy.observe_events(returned(), mem)
    assert mem['soldiers_seen'] == 64


def test_confirmed_count_drives_egg_reserve_and_summer_loss_choice():
    mem = memory()
    commit(mem)
    policy.observe_events(returned(), mem)
    assert mem['soldiers_seen'] == 64
    mem['egg_uses'] = {'ココット': 0}
    assert policy._extras_reserve(mem, {'year': 1, 'month': 8, 'gold': 45})[0] == 45 - policy.WAGE_RESERVE
    from test_hanjuku_summer_bonus import prompt_canvas
    frame = prompt_canvas()
    screen = parse(frame)
    screen.header = {'year': 1, 'month': 8, 'gold': 55}
    policy.summer_bonus_step(screen, mem)
    assert mem['summer_bonus_plan']['target'] == 'bonus'


def test_old_hotloaded_shop_never_invents_a_purchase_receipt():
    mem = memory()
    mem['shop']['soldiers_done'] = True
    policy.observe_events(returned(), mem)
    assert mem['soldiers_seen'] == 40
    assert 'soldier_receipt' not in mem['shop']


def test_new_chapter_invalidates_the_old_army_count():
    mem = memory()
    policy._enter_chapter(mem, 2, reason='test chapter transition')
    assert 'soldiers_seen' not in mem
    assert 'soldiers_seen_key' not in mem


def test_bot_reads_total_then_records_paid_refill_after_real_menu_return():
    c = Canvas()
    c.text(48, 15, '1ねん 8のつき 79G')
    for label, (x, y) in MONTH_GRID.items():
        c.text(x, y, label)
    c.text(24, 135, 'へいしすうは40めいです')
    c.text(24, 167, 'じゅうじキー')
    c.text(24, 199, 'かわりますぞ')
    c.text(216, 199, '24')
    c.hand(186, 193)
    actions, state = decide(c.frame(), {'policy': memory(count=None)})
    assert actions == [policy.pad('a')]
    assert state['policy']['soldiers_seen'] == 40
    _, state = decide(month_canvas(55, month=8), state)
    assert state['policy']['soldiers_seen'] == 64
    assert state['policy']['shop']['soldier_receipt']['status'] == 'paid'

"""Owner: aim at fifty; about a hundred only with money beyond necessities."""
from copy import deepcopy

import pytest
from docich import hanjuku_policy as p
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import month_canvas
from test_hanjuku_egg_priority import quantity


def memory(count=17, gold=130, uses=4):
    mem = {'chapter': 1, 'month': '1-7', 'tick': 100,
           'soldiers_seen': count, 'soldiers_seen_key': '1-7',
           'egg_uses': {'ココット': uses}}
    header = {'year': 1, 'month': 7, 'gold': gold}
    return mem, header


@pytest.mark.parametrize('count,expected', [(0, 50), (17, 33), (49, 1), (50, 0), (60, 0)])
def test_normal_cash_adds_only_the_shortfall_to_fifty(count, expected):
    mem, header = memory(count=count, gold=100)
    shop = p._plan(mem, header)
    assert shop['soldiers'] == expected
    assert shop['soldier_total_target'] == 50


@pytest.mark.parametrize('count,expected', [(17, 83), (50, 50), (60, 40), (99, 1), (100, 0)])
def test_spare_cash_can_prepare_one_hundred_on_hand(count, expected):
    mem, header = memory(count=count, gold=500)
    shop = p._plan(mem, header)
    assert shop['soldiers'] == expected
    if expected: assert shop['soldier_total_target'] == 100


def test_depleted_egg_is_checked_before_troops_even_with_a_small_army():
    mem, header = memory(uses=1, gold=130)
    shop = p._plan(mem, header)
    assert shop['reserve'] == 50 and shop['egg_priority']
    assert shop['soldiers'] == 33
    assert p.month_step(parse(month_canvas(130, on='たまごのかいふく')), mem) == [p.pad('a')]
    assert mem['month_sub']['kind'] == 'egg'
    assert not shop.get('soldier_receipt')


def test_unaffordable_recovery_never_consumes_its_money_on_troops():
    mem, header = memory(uses=1, gold=45)
    shop = p._plan(mem, header)
    assert shop['reserve'] == 15 and shop['soldiers'] == 0
    for _ in range(4): p.month_step(parse(month_canvas(45, on='へいしほじゅう')), mem)
    assert shop['soldiers_done'] and not shop.get('soldier_receipt')


def test_higher_target_keeps_recovery_repair_wages_and_spare_before_expansion():
    mem, header = memory(count=17, gold=250, uses=1)
    shop = {'key': '1-7', 'items': [], 'soldiers': 99, 'egg': 'pending',
            'reserve': 50, 'general_repair_reserve': 50, 'recruit_reserve': 50}
    p._limit_soldier_refill(mem, shop, header)
    assert shop['soldiers'] == 33 and shop['soldier_total_target'] == 50


def test_hotloaded_quantity_cannot_confirm_the_old_ninety_nine_plan():
    mem, header = memory(count=17)
    mem['shop'] = {'key': '1-7', 'items': [], 'merchant_done': True, 'soldiers': 99,
                   'soldiers_done': False, 'egg': 'not_needed', 'soldier_tens': 9}
    screen = quantity(); screen.header = header
    actions = p.quantity_step(screen, mem, soldiers=True)
    assert mem['shop']['soldiers'] == 33
    assert actions != [p.pad('a')]
    assert not mem['shop'].get('soldier_receipt')


def test_existing_dispatched_purchase_receipt_is_never_rewritten():
    mem, header = memory(count=17)
    shop = {'key': '1-7', 'items': [], 'soldiers': 99,
            'soldier_receipt': {'status': 'pending', 'qty': 99}}
    before = deepcopy(shop)
    p._limit_soldier_refill(mem, shop, header)
    assert shop == before

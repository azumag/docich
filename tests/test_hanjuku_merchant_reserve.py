"""Merchant money is subject to payroll and unpaid necessity reservations.

Quotation screens here are synthetic. The stock and returned month-menu PNGs
are original g719 frames; the intervening quotation PNGs were not saved.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku_screen import parse
from docich.hanjuku_pixels import read_png
from test_hanjuku_chart_bot import Canvas

ID = {'game': 'hanjuku-hero', 'runtime_id': 'g719-a6227941', 'generation': 719,
      'lease_id': '6231516c-c54f-4cc4-b39a-343a281de2db'}


def memory(*, reserve=0, complete=False):
    wages = {'どうし': 0, 'ゼウス': 4, 'ヴィーナス': 3, 'ココット': 1}
    return {'chapter': 1, 'month': '1-5', 'tick': 100, '_run_identity': dict(ID),
            'recruit_roster': {'identity': dict(ID), 'chapter': 1, 'month': '1-5',
                'tick': 90, 'complete': complete, 'names': list(wages), 'wages': wages},
            'egg_uses': {'ココット': 0, 'ゼウス': 1}, 'egg_zero_owners': ['ココット'],
            'shop': {'key': '1-5', 'items': [['ノリウツール', 1], ['イッテツーン', 1]],
                     'want': ['ノリウツール', 1, 18], 'reserve': reserve,
                     'merchant_done': False, 'variant': 'budget_boss_kit_first'}}


def quote(gold=60, *, count=1, cost=18, month=5):
    c = Canvas()
    if gold is not None:
        c.text(48, 15, f'1ねん {month}のつき {gold}G')
    c.text(24, 183, f'{count}こで{cost}Gになりまんな')
    c.text(184, 183, 'うむッ!'); c.text(184, 199, 'いかんッ!'); c.hand(162, 177)
    screen = parse(c.frame()); screen.kind = 'shop_quantity'
    return screen


def stock(gold=60, *, month=5):
    c = Canvas(); c.text(48, 15, f'1ねん {month}のつき {gold}G')
    for y, name, cost in ((175, 'ノリウツール', 18), (191, 'イッテツーン', 1)):
        c.text(120, y, name); c.text(216, y, f'{cost}G')
    c.hand(98, 169)
    screen = parse(c.frame()); screen.kind = 'shop_list'
    return screen


def test_g719_43g_cannot_spend_the_unknown_30g_and_unpaid_13g_hold():
    mem = memory(reserve=13)
    assert p.shop_step(stock(43), mem) == [p.pad('b')]
    assert not mem['shop']['items'] and not mem['shop'].get('bought')
    mem = memory(reserve=13); before = deepcopy(mem['egg_uses'])
    assert p.quantity_step(quote(43), mem) == [p.pad('b')]
    assert not mem['shop'].get('bought') and mem['egg_uses'] == before
    assert mem['shop']['reserve'] == 13 and mem['egg_zero_owners'] == ['ココット']


def test_original_merchant_and_zero_cash_return_do_not_prove_a_quote_receipt():
    root = Path(__file__).parent / 'fixtures/hanjuku'
    f1, f2 = read_png(root / 'g719-merchant-819.png'), read_png(root / 'g719-merchant-843.png')
    assert f1.digest() == '1ad249dd69eddf41012ce5bac2af952a15332642cfbb3255a916c979c26f07de'
    assert f2.digest() == '4219ee2eedab31fd4b79a86d10c3f63134ac532ab3e23581b7f90cb2e427e68c'
    before, after = parse(f1), parse(f2)
    assert before.kind == 'shop_list' and before.header['gold'] == 43
    assert after.kind == 'month_menu' and after.header['gold'] == 0
    mem = memory(reserve=13)
    mem['shop']['items'] = [['クースカン', 1], ['ノリウツール', 1], ['イッテツーン', 1]]
    assert p.shop_step(before, mem) == [p.pad('b')]
    assert not mem['shop'].get('bought')


@pytest.mark.parametrize('gold,allowed', [(47, False), (48, True), (60, True)])
def test_real_confirmation_keeps_unknown_payroll_at_exact_boundary(gold, allowed):
    mem = memory()
    assert p.quantity_step(quote(gold), mem) == [p.pad('a' if allowed else 'b')]
    assert bool(mem['shop'].get('bought')) is allowed


def test_actual_complete_8g_payroll_can_fund_18g_without_a_blind_30g_floor():
    mem = memory(complete=True)
    assert p._merchant_spendable(mem, mem['shop'], quote(26).header) == 18
    assert p.quantity_step(quote(26), mem) == [p.pad('a')]


def test_known_62g_payroll_is_fully_protected_in_stock_and_final_confirmation():
    mem = memory(complete=True)
    wages = {'どうし': 0, 'ゼウス': 15, 'ヴィーナス': 15, 'ココット': 15,
             'ヘルメス': 15, 'コリアンダー': 2}  # synthetic observed-payroll boundary
    mem['recruit_roster'].update(names=list(wages), wages=wages)
    assert p._egg_recovery_wage_floor(mem) == 62
    mem['shop']['items'] = [['ノリウツール', 1]]
    assert p.shop_step(stock(79), mem) == [p.pad('b')]
    mem['shop']['items'] = [['ノリウツール', 1], ['イッテツーン', 1]]
    assert p.quantity_step(quote(79), mem) == [p.pad('b')]
    mem = memory(complete=True); mem['recruit_roster'].update(names=list(wages), wages=wages)
    assert p.quantity_step(quote(80), mem) == [p.pad('a')]


def test_stock_quantity_cap_retains_card_priority_within_available_money():
    mem = memory(); mem['shop']['items'] = [['イッテツーン', 20]]
    screen = stock(35); screen.hand = (98, 185, 120, 200)
    assert p.shop_step(screen, mem) == [p.pad('a')]
    assert mem['shop']['want'] == ['イッテツーン', 5, 1]
    assert mem['shop']['items'] == [['イッテツーン', 5]]


def test_cash_or_new_repair_hold_after_selection_cannot_use_cached_quantity():
    mem = memory(reserve=50); mem['shop']['want'] = ['ノリウツール', 2, 18]
    mem['shop']['items'][0][1] = 2
    assert p.quantity_step(quote(100, count=2, cost=36), mem) == [p.pad('b')]
    mem = memory(reserve=50); mem['shop']['hero_repair_reserve'] = 50
    assert p.quantity_step(quote(130), mem) == [p.pad('b')]
    assert mem['shop']['hero_repair_reserve'] == 50


def test_all_unpaid_holds_and_other_owners_survive_a_card_purchase():
    mem = memory(complete=True, reserve=50)
    mem['shop'].update(hero_repair_reserve=50, general_repair_reserve=50, recruit_reserve=50)
    before = deepcopy(mem['egg_uses'])
    assert p.quantity_step(quote(226), mem) == [p.pad('a')]
    assert mem['egg_uses'] == before and mem['egg_zero_owners'] == ['ココット']
    assert p._merchant_spendable(mem, mem['shop'], quote(208).header) == 0
    assert mem['shop']['reserve'] == 50 and mem['shop']['recruit_reserve'] == 50


@pytest.mark.parametrize('change', ['month', 'memory_month', 'header', 'wrong_price', 'duplicate_quote',
                                     'bool_reserve', 'bad_repair', 'bad_recruit'])
def test_unknown_stale_malformed_or_changed_quote_never_confirms(change):
    mem = memory(); screen = quote()
    if change == 'month': screen = quote(month=6)
    elif change == 'memory_month': mem['month'] = '1-6'
    elif change == 'header': screen = quote(gold=None)
    elif change == 'wrong_price': screen = quote(cost=19)
    elif change == 'duplicate_quote': screen.text += '1こで18Gになりまんな'
    elif change == 'bool_reserve': mem['shop']['reserve'] = True
    elif change == 'bad_repair': mem['shop']['hero_repair_reserve'] = '50'
    else: mem['shop']['recruit_reserve'] = -1
    assert p.quantity_step(screen, mem) == [p.pad('b')]
    assert not mem['shop'].get('bought')


def test_rejected_cached_quote_does_not_reopen_after_saved_editor_returns():
    mem = memory(reserve=50)
    assert p.quantity_step(quote(60), mem) == [p.pad('b')]
    mem = json.loads(json.dumps(mem))
    assert mem['shop']['want'] is None and mem['shop']['items'] == [['イッテツーン', 1]]
    assert p.quantity_step(quote(60), mem) == [p.pad('b')]
    assert not mem['shop'].get('bought')


def test_fading_header_does_not_discard_the_requested_stock_as_absent():
    mem = memory(); before = deepcopy(mem['shop']['items']); screen = stock()
    screen.header = {'year': 1, 'month': 5, 'gold': None}
    assert p.shop_step(screen, mem) == [p.pad('b')]
    assert mem['shop']['items'] == before


@pytest.mark.parametrize('qty', [True, False, 0, -1, '1'])
def test_bad_cached_quantity_cannot_open_a_purchase(qty):
    mem = memory(); mem['shop']['items'] = [['ノリウツール', qty]]
    assert p.shop_step(stock(), mem) == [p.pad('b')]
    assert not mem['shop'].get('bought')


def test_bool_cached_quote_target_does_not_alias_one_actual_item():
    mem = memory(); mem['shop']['want'][1] = True
    assert p.quantity_step(quote(), mem) == [p.pad('b')]
    assert not mem['shop'].get('bought')

"""Synthetic native-font merchant screens; no live purchase is claimed."""
from copy import deepcopy
import json

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku.strategy import bargains as b
from test_hanjuku_merchant_reserve import memory, quote, stock
from test_hanjuku_chart_bot import Canvas
from docich.hanjuku_screen import parse


@pytest.fixture
def ready(monkeypatch):
    # These unit cases are after all charted purchases. The future-chart
    # guard and the actual monthly _plan integration are tested separately.
    monkeypatch.setattr(b, '_charted_purchase_ahead', lambda mem, header: False)
    mem = memory(complete=True)
    mem['egg_uses'] = {}
    mem['egg_zero_owners'] = []
    mem['shop'].update(items=[], want=None, soldiers=50, soldiers_done=False,
                       reserve=0, recruit_reserve=0)
    return mem


def listing(gold=300, rows=(('ノリウツール', 18),)):
    c = Canvas(); c.text(48, 15, f'1ねん 5のつき {gold}G')
    for i, (name, price) in enumerate(rows):
        c.text(120, 175 + i * 16, name); c.text(216, 175 + i * 16, f'{price}G')
    c.hand(98, 169)
    screen = parse(c.frame()); screen.kind = 'shop_list'
    return screen


def test_empty_plan_buys_a_visible_strong_card_with_spare_cash(ready):
    assert p.shop_step(listing(), ready) == [p.pad('a')]
    assert ready['shop']['want'] == ['ノリウツール', 3, 18]
    assert ready['shop']['bargain_listing_planned'] is True
    assert not ready['shop'].get('bought')  # selecting is not payment


def test_monthly_plan_opens_merchant_without_a_chart_card_order():
    mem = {'chapter': 1, 'month': '99-5', 'tick': 1, 'egg_uses': {},
           'soldiers_seen': 100, 'soldiers_seen_key': '99-5'}
    shop = p._plan(mem, {'year': 99, 'month': 5, 'gold': 500})
    assert shop['items'] and shop['merchant_done'] is False
    assert shop['bargain_planned'] is True
    assert all(name in b.BARGAIN_TARGETS for name, _ in shop['items'])


def test_actual_cart_stock_replaces_unavailable_provisional_candidates(ready):
    b.plan_bargains(ready, ready['shop'], listing().header)
    assert ready['shop']['items'][0][0] == 'ノリウツール'
    # This vendor has only ミックミー: do not walk away just because the
    # first two provisional candidates consumed the preliminary budget.
    assert p.shop_step(listing(rows=(('ミックミー', 40),)), ready) == [p.pad('a')]
    assert ready['shop']['want'] == ['ミックミー', 2, 40]


@pytest.mark.parametrize('gold,qty', [(87, 0), (88, 0), (105, 0), (106, 1), (124, 2)])
def test_full_payroll_soldier_funds_and_extra_buffer_boundary(ready, gold, qty):
    # 8G payroll + 50G pending soldiers + 30G optional buffer remain.
    actions = p.shop_step(listing(gold), ready)
    assert actions == [p.pad('a' if qty else 'b')]
    assert (ready['shop'].get('want') or [None, 0])[1] == qty


def test_all_necessities_and_original_card_priority_survive(ready):
    shop = ready['shop']
    shop.update(items=[['イッテツーン', 2]], reserve=50,
                hero_repair_reserve=50, general_repair_reserve=50, recruit_reserve=50)
    original = deepcopy(shop)
    b.plan_bargains(ready, shop, listing(400).header)
    assert shop['items'][0] == ['イッテツーン', 2]
    for key in ('reserve', 'hero_repair_reserve', 'general_repair_reserve',
                'recruit_reserve', 'soldiers', 'soldiers_done'):
        assert shop[key] == original[key]
    assert sum(b.KNOWN_PRICES[n] * q for n, q in shop['items'][1:]) <= 100


def test_existing_same_card_is_not_duplicated_or_reordered(ready):
    ready['shop']['items'] = [['ノリウツール', 1], ['イッテツーン', 2]]
    b.plan_bargains(ready, ready['shop'], listing().header)
    assert ready['shop']['items'][:2] == [['ノリウツール', 1], ['イッテツーン', 2]]
    assert 'ノリウツール' not in ready['shop']['bargain_cards']


def test_sufficient_observed_stock_is_not_bought_again(ready):
    ready['card_stock'] = dict(b.BARGAIN_TARGETS)
    before = deepcopy(ready['card_stock'])
    assert p.shop_step(listing(999), ready) == [p.pad('b')]
    assert ready['shop']['items'] == [] and ready['card_stock'] == before


def test_gold_and_copy_caps_also_apply_to_deeply_discounted_stock(ready):
    prices = {name: 1 for name in b.BARGAIN_TARGETS}
    b.plan_bargains(ready, ready['shop'], listing(999).header, listed=prices)
    assert sum(qty for _, qty in ready['shop']['items']) == b.BARGAIN_MONTHLY_COPIES
    assert sum(prices[n] * q for n, q in ready['shop']['items']) <= b.BARGAIN_MONTHLY_GOLD


@pytest.mark.parametrize('rows', [(('ノリウツール', 19),), (('イッテツーン', 1),)])
def test_expensive_or_weak_only_stock_is_not_an_optional_purchase(ready, rows):
    assert p.shop_step(listing(999, rows), ready) == [p.pad('b')]
    assert not ready['shop'].get('bought') and ready['shop']['items'] == []


def test_exact_quote_rechecks_cash_and_does_not_repeat_after_hotload(ready):
    assert p.shop_step(listing(142), ready) == [p.pad('a')]
    assert ready['shop']['want'] == ['ノリウツール', 3, 18]
    assert p.quantity_step(quote(142, count=3, cost=54), ready) == [p.pad('a')]
    assert ready['shop']['bought'] == [['ノリウツール', 3, 18]]
    ready = json.loads(json.dumps(ready))
    assert p.quantity_step(quote(142, count=3, cost=54), ready) == [p.pad('b')]
    assert p.shop_step(listing(88), ready) == [p.pad('b')]
    assert ready['shop']['bought'] == [['ノリウツール', 3, 18]]
    assert not ready.get('card_stock')  # never invent an observed inventory receipt


@pytest.mark.parametrize('change', ['cash', 'repair', 'soldiers', 'recruit', 'month', 'price'])
def test_changed_quote_or_necessities_declines_extra_without_rearming(ready, change):
    p.shop_step(listing(142), ready)
    screen = quote(142, count=3, cost=54)
    if change == 'cash': screen = quote(141, count=3, cost=54)
    elif change == 'repair': ready['shop']['hero_repair_reserve'] = 1
    elif change == 'soldiers': ready['shop']['soldiers'] = 51
    elif change == 'recruit': ready['shop']['recruit_reserve'] = 1
    elif change == 'month': screen = quote(142, count=3, cost=54, month=6)
    else:
        ready['shop']['want'][2] = 19
        screen = quote(200, count=3, cost=57)
    assert p.quantity_step(screen, ready) == [p.pad('b')]
    assert not ready['shop'].get('bought')
    restored = json.loads(json.dumps(ready))
    assert p.shop_step(listing(999), restored) == [p.pad('b')]
    assert restored['shop']['items'] == []


def test_future_chart_budget_is_not_spent_on_extras(ready, monkeypatch):
    monkeypatch.setattr(b, '_charted_purchase_ahead', lambda mem, header: True)
    assert p.shop_step(listing(999), ready) == [p.pad('b')]
    assert ready['shop']['items'] == []


@pytest.mark.parametrize('bad', [None, True, 'x', [[]], [{}], ['ノリウツール', 'ノリウツール']])
def test_malformed_saved_extra_metadata_cannot_authorize_payment(ready, bad):
    ready['shop']['bargain_cards'] = bad
    assert b.bargain_spendable(ready, ready['shop'], listing().header) is None


@pytest.mark.parametrize('field,value', [('soldiers', True), ('reserve', -1),
                                          ('recruit_reserve', '50')])
def test_malformed_reservations_leave_plan_unchanged(ready, field, value):
    ready['shop'][field] = value
    before = deepcopy(ready['shop'])
    b.plan_bargains(ready, ready['shop'], listing().header)
    assert ready['shop'] == before


def test_unread_header_does_not_discard_or_expand_optional_plan(ready):
    b.plan_bargains(ready, ready['shop'], listing().header)
    before = deepcopy(ready['shop'])
    screen = listing(); screen.header['gold'] = None
    assert p.shop_step(screen, ready) == [p.pad('b')]
    assert ready['shop'] == before


def test_saved_before_recruit_soldier_budget_is_retained(ready):
    ready['shop'].update(soldiers=0, soldiers_before_recruit=50,
                        soldiers_before_recruit_done=False)
    assert b.bargain_spendable(ready, ready['shop'], listing(106).header) == 18


def test_spent_optional_gold_is_not_reissued_on_another_card(ready):
    ready['shop'].update(bargain_cards=['ノリウツール', 'クースカン'],
                        bought=[['ノリウツール', 3, 18]], items=[['クースカン', 3]])
    assert b.bargain_spendable(ready, ready['shop'], listing(999).header) == 46

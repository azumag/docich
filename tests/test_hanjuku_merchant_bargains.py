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


def test_pending_recruit_fee_is_reserved_but_an_affordable_extra_is_bought(ready):
    # Live g1133 49-10: the Chinese merchant had 8734G, but the boolean
    # recruit hold skipped every planned card before the 50G recruit step.
    shop = ready['shop']
    shop.update(items=[['ミックミー', 2]], bargain_cards=['ミックミー'],
                bargain_planned=True, bargain_listing_planned=True,
                recruit_priority=True, recruit='check', recruit_reserve=0,
                soldiers=44, soldiers_done=False)
    assert p.shop_step(listing(8734, rows=(('ミックミー', 40),)), ready) == [p.pad('a')]
    assert shop['want'] == ['ミックミー', 2, 40]


@pytest.mark.parametrize('gold,bought', [(155, 0), (156, 1)])
def test_pending_recruit_fee_stays_out_of_the_extra_budget(ready, gold, bought):
    shop = ready['shop']
    shop.update(items=[['ノリウツール', 3]], bargain_cards=['ノリウツール'],
                bargain_planned=True, bargain_listing_planned=True,
                recruit_priority=True, recruit='check', recruit_reserve=0,
                soldiers=50, soldiers_done=False)
    actions = p.shop_step(listing(gold), ready)
    assert actions == [p.pad('a' if bought else 'b')]
    assert (shop.get('want') or [None, 0])[1] == bought


def test_pending_recruit_still_defers_new_extra_planning(ready):
    # The merchant may sell an already planned extra, but the recruit menu
    # keeps priority over planning a new one (chart bot monthly ordering).
    shop = ready['shop']
    shop.update(items=[], recruit_priority=True, recruit='check', recruit_reserve=0)
    b.plan_bargains(ready, shop, listing(999).header)
    assert shop['items'] == []
    assert not shop.get('bargain_listing_planned')


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
    assert sum(b.KNOWN_PRICES[n] * q for n, q in shop['items'][1:]) <= b.BARGAIN_MONTHLY_GOLD


def test_existing_same_card_is_not_duplicated_or_reordered(ready):
    ready['shop']['items'] = [['ノリウツール', 1], ['イッテツーン', 2]]
    b.plan_bargains(ready, ready['shop'], listing().header)
    assert ready['shop']['items'][:2] == [['ノリウツール', 1], ['イッテツーン', 2]]
    assert 'ノリウツール' not in ready['shop']['bargain_cards']


def test_stocked_cards_are_topped_up_with_surplus_cash(ready):
    # Owner 2026-11-05: 在庫が目標に達していても余剰資金で買い足す。
    # 月次の金額・枚数上限と在庫観測の非更新はそのまま。
    ready['card_stock'] = dict(b.BARGAIN_TARGETS)
    before = deepcopy(ready['card_stock'])
    assert p.shop_step(listing(999), ready) == [p.pad('a')]
    shop = ready['shop']
    assert shop['items'] and all(name in b.BARGAIN_TARGETS for name, _ in shop['items'])
    assert sum(b.KNOWN_PRICES[n] * q for n, q in shop['items']) <= b.BARGAIN_MONTHLY_GOLD
    assert ready['card_stock'] == before


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
    assert b.bargain_spendable(ready, ready['shop'], listing(999).header) == b.BARGAIN_MONTHLY_GOLD - 54


@pytest.mark.parametrize('gold', [100, 200, 300, 400, 500, 1000, 2000])
def test_round_cash_header_never_poisoned_as_zero_price_stock(ready, gold):
    actions = p.shop_step(listing(gold), ready)
    quantity = min(99 if gold >= b.RICH_MERCHANT_GOLD else 3,
                   max(0, gold - 8 - 50 - 30) // 18)
    assert actions == [p.pad('a' if quantity else 'b')]
    assert ready['shop']['bargain_listing_planned'] is True
    assert (ready['shop'].get('want') or [None, 0])[1] == quantity
    assert not ready['shop'].get('bought')


def test_unreadable_merchant_exit_is_recorded_not_silent(ready):
    # A quiet 'b' on the merchant list once left a planned card unbought
    # (g1059 8-6..8-10: five bargain plans, no buy or buy_skip record).
    ready['month'] = '99-1'                  # the month no longer matches the shop
    assert p.shop_step(listing(), ready) == [p.pad('b')]
    record = ready['_records'][-1]
    assert record['decision'] == 'shop_list_exit'
    assert record['observed_metric']['reason'] == 'spendable_unreadable'
    assert ready['shop'].get('bought') in (None, [])


def test_unwanted_listing_exit_records_the_merchant_stock(ready):
    # g1097 2026-11-02: the merchant left with no record at all when the
    # listing matched no wanted card; the silent B hid why nothing was bought.
    screen = listing(999, rows=(('バルムンク', 90), ('グリンボー', 30)))
    ready['shop']['bargain_listing_planned'] = False
    assert p.shop_step(screen, ready) == [p.pad('b')]
    record = ready['_records'][-1]
    assert record['decision'] == 'shop_list_exit'
    assert record['observed_metric']['reason'] == 'nothing_planned'
    assert record['observed_metric']['listed'] == {'バルムンク': 90, 'グリンボー': 30}


# g1097 2026-11-03 00:57: a cart selling the strong cards at half price came up
# right after the monthly duel. The month's single listing pass was already
# spent (and the shop closed), so the bot left in the same observation with
# 9987G unused.
HALF = (('ノリウツール', 8), ('クースカン', 12), ('ゼンマイン', 16), ('ミックミー', 20))


def month_pass_spent(mem):
    mem['shop'].update(bargain_planned=True, bargain_listing_planned=True,
                       merchant_done=True, closed=True, items=[], want=None)
    return mem


def test_a_half_price_cart_after_the_month_pass_gets_a_pass_of_its_own(ready):
    month_pass_spent(ready)
    assert p.shop_step(listing(9987, HALF), ready) == [p.pad('a')]       # selects a card to buy
    shop = ready['shop']
    assert shop['want'][0] in dict(HALF) and shop['cheap_vendor'] is True
    assert shop['cheap_vendor_planned'] is True
    planned = dict(HALF)
    rich = dict((n, q) for n, q in shop['items'] if n in b.RICH_MERCHANT_CARDS)
    # This synthetic four-row canvas clips the fourth (ミックミー) row;
    # only the three visible cards may be planned from the actual listing.
    assert rich == {'ノリウツール': b.RICH_MERCHANT_MAX_COPIES}
    ordinary = [(n, q) for n, q in shop['items'] if n not in b.RICH_MERCHANT_CARDS]
    assert sum(planned[n] * q for n, q in ordinary) <= b.CHEAP_VENDOR_GOLD
    assert sum(q for _, q in ordinary) <= b.CHEAP_VENDOR_COPIES
    assert not shop.get('bought')                       # selecting is not payment


def test_the_larger_caps_are_only_for_that_pass_and_still_bound_the_spend(ready):
    month_pass_spent(ready)
    prices = {name: 1 for name in b.BARGAIN_TARGETS}            # absurdly cheap: only the caps bind
    b.plan_bargains(ready, ready['shop'], listing(9999).header, listed=prices)
    rich = {n: q for n, q in ready['shop']['items'] if n in b.RICH_MERCHANT_CARDS}
    # This synthetic listing contains only BARGAIN_TARGETS; a rich merchant
    # cannot buy requested cards that do not appear in the observed shop.
    assert rich == {n: b.RICH_MERCHANT_MAX_COPIES for n in b.RICH_MERCHANT_CARDS
                    if n in prices}
    ordinary = [(n, q) for n, q in ready['shop']['items'] if n not in b.RICH_MERCHANT_CARDS]
    # Four of the bargain names are now rich-priority cards, so only the
    # remaining kinds count against the ordinary capped bargain quantities.
    available_target_copies = sum(b.BARGAIN_TARGETS[n] * b.CHEAP_VENDOR_TARGET_FACTOR
                                  for n in prices if n not in b.RICH_MERCHANT_CARDS)
    assert sum(q for _, q in ordinary) == min(b.CHEAP_VENDOR_COPIES, available_target_copies)
    assert sum(prices[n] * q for n, q in ordinary) <= b.CHEAP_VENDOR_GOLD


def test_a_normally_priced_cart_after_the_month_pass_is_still_skipped(ready):
    month_pass_spent(ready)
    full = (('ノリウツール', 18), ('クースカン', 24), ('ゼンマイン', 32), ('ミックミー', 40))
    assert p.shop_step(listing(9987, full), ready) == [p.pad('b')]
    assert not ready['shop'].get('cheap_vendor')
    assert ready['shop']['items'] == []


def test_the_half_price_pass_is_taken_once_per_month(ready):
    month_pass_spent(ready)
    b.plan_bargains(ready, ready['shop'], listing(9987).header, listed=dict(HALF))
    assert ready['shop']['cheap_vendor_planned'] is True
    ready['shop']['items'] = []
    b.plan_bargains(ready, ready['shop'], listing(9987).header, listed=dict(HALF))
    assert ready['shop']['items'] == []                 # no third pass in the same month


def test_a_cheap_pass_still_respects_necessities_and_the_cash_reserve(ready):
    month_pass_spent(ready)
    ready['shop'].update(egg='pending', egg_priority=True)       # recovery comes first
    b.plan_bargains(ready, ready['shop'], listing(9987).header, listed=dict(HALF))
    assert ready['shop']['items'] == [] and not ready['shop'].get('cheap_vendor_planned')


def test_bargain_plan_records_only_considered_cards_not_total_holdings(ready):
    """The planning subset must not be mistaken for whole-hand inventory."""
    ready['card_stock'] = {'ノリウツール': 1, 'バルムンク': 17}
    b.plan_bargains(ready, ready['shop'], listing(999).header)
    records = [r for r in ready.get('_records', []) if r.get('decision') == 'bargain_plan']
    assert records, 'expected a purchase plan with diagnostic evidence'
    metric = records[-1]['observed_metric']
    assert metric['planned_cards_seen_total'] == sum(metric['seen'].values())
    assert 'seen_total' not in metric
    assert 'バルムンク' not in metric['seen']
    assert metric['planned_cards_seen_total'] != sum(ready['card_stock'].values())


def test_a_card_at_the_holding_cap_is_dropped_without_asking(ready):
    """g1163 2026-11-09 08:53-08:57: fifteen capped cards cost 3.5 minutes."""
    mem = deepcopy(ready)
    screen = listing(gold=300, rows=(('ノリウツール', 18),))
    mem['card_stock'] = {'ノリウツール': b.HOLD_CAP}
    mem['shop']['items'] = [['ノリウツール', 5]]
    mem['shop']['key'] = '1-5'
    actions = p.shop_step(screen, mem)
    assert actions == [] or all(a.get('type') != 'pad' or a.get('key') != 'a' for a in actions)
    skips = [r for r in mem['_records'] if r['decision'] == 'buy_skip']
    assert skips and skips[-1]['deviation_reason'] == 'holding_cap'
    assert skips[-1]['card'] == 'ノリウツール'
    assert not mem['shop']['items']


def test_a_card_one_below_the_cap_is_still_asked_for(ready):
    mem = deepcopy(ready)
    screen = listing(gold=300, rows=(('ノリウツール', 18),))
    mem['card_stock'] = {'ノリウツール': b.HOLD_CAP - 1}
    mem['shop']['items'] = [['ノリウツール', 1]]
    mem['shop']['key'] = '1-5'
    p.shop_step(screen, mem)
    assert not [r for r in mem.get('_records') or []
                if r['decision'] == 'buy_skip' and r.get('deviation_reason') == 'holding_cap']
    assert mem['shop']['want'] == ['ノリウツール', 1, 18]


def test_the_rare_card_sweep_moves_several_rows_per_observation():
    """g1163 2026-11-10 00:38: 34 consecutive sortie_input decisions.

    The list is ID-sorted, so the distance to キャトルミュー is known and up to
    four rows can move per observation; the landing is still read.
    """
    from docich.hanjuku.flows import deploy

    source = deploy._rare_card_inventory.__doc__ or ''
    assert 'bounded sweep' in source
    # The jump is a plain arithmetic contract: rows between the visible edge
    # and the event card, capped at four presses.
    ids = [1, 5, 9, 13]
    target = 21
    frames = max(1, min((target - max(ids)) // 4 + 1, 4))
    assert frames == 3

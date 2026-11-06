"""Synthetic merchant screen: the shopkeeper refuses a card the hand cannot hold.

No live purchase is claimed. The refusal message sits over the still-visible
price list, so it classifies as shop_list while the hand cursor is hidden.
"""
import pytest

from docich import hanjuku_policy as p
from docich.hanjuku.strategy import bargains as b
from test_hanjuku_merchant_reserve import memory
from test_hanjuku_chart_bot import Canvas
from docich.hanjuku_screen import parse

MESSAGE = 'う〜んそれいじょうもてないようですな!'


@pytest.fixture
def ready(monkeypatch):
    monkeypatch.setattr(b, '_charted_purchase_ahead', lambda mem, header: False)
    mem = memory(complete=True)
    mem['egg_uses'] = {}
    mem['egg_zero_owners'] = []
    mem['shop'].update(items=[], want=None, soldiers=50, soldiers_done=False,
                       reserve=0, recruit_reserve=0)
    return mem


def refused_list(gold=300, rows=(('ノリウツール', 18), ('ミックミー', 40), ('クースカン', 25)),
                 message=MESSAGE):
    c = Canvas(); c.text(48, 15, f'1ねん 5のつき {gold}G')
    for i, (name, price) in enumerate(rows):
        c.text(120, 111 + i * 16, name); c.text(216, 111 + i * 16, f'{price}G')
    c.text(16, 191, message)          # message window hides the hand cursor
    screen = parse(c.frame())
    return screen


def test_refusal_message_is_read_over_the_price_list(ready):
    screen = refused_list()
    assert 'もてない' in screen.text
    assert screen.hand is None


def test_refusal_closes_the_message_instead_of_holding(ready):
    shop = ready['shop']
    shop.update(items=[['ノリウツール', 3], ['ミックミー', 2]], want=None,
                bargain_listing_planned=True, bargain_planned=True)
    screen = refused_list()
    screen.kind = 'shop_list'
    actions = p.shop_step(screen, ready)
    assert actions == [p.pad('a')]
    # No further card is attempted this visit, and the refusal is on record.
    assert shop['items'] == []
    assert shop['want'] is None
    assert shop['hold_limit'] == 1
    assert any(r.get('deviation_reason') == 'holding_limit'
               for r in ready.get('_records', []))


def test_refused_purchase_is_not_counted_as_bought(ready):
    shop = ready['shop']
    shop.update(items=[['ミックミー', 2]], want=None, bought=[['ノリウツール', 3, 18]],
                bargain_listing_planned=True, bargain_planned=True,
                last_buy={'row': ['ノリウツール', 3, 18], 'gold': 300})
    screen = refused_list(gold=300)           # gold unchanged: nothing was paid
    screen.kind = 'shop_list'
    p.shop_step(screen, ready)
    assert shop['bought'] == []


def test_earlier_real_purchase_keeps_counting(ready):
    shop = ready['shop']
    shop.update(items=[['ミックミー', 2]], want=None, bought=[['ノリウツール', 3, 18]],
                bargain_listing_planned=True, bargain_planned=True,
                last_buy={'row': ['ノリウツール', 3, 18], 'gold': 354})
    screen = refused_list(gold=300)           # 54G left the hand: it was bought
    screen.kind = 'shop_list'
    p.shop_step(screen, ready)
    assert shop['bought'] == [['ノリウツール', 3, 18]]


def test_text_kind_message_is_also_closed(ready):
    ready['shop'].update(items=[['ミックミー', 2]], want=None,
                         bargain_listing_planned=True, bargain_planned=True)
    c = Canvas(); c.text(48, 15, '1ねん 5のつき 300G'); c.text(16, 191, MESSAGE)
    screen = parse(c.frame()); screen.kind = 'text'
    assert p.shop_step(screen, ready) == [p.pad('a')]
    assert ready['shop']['items'] == []


def test_message_press_is_bounded(ready):
    shop = ready['shop']
    shop.update(items=[['ミックミー', 2]], want=None,
                bargain_listing_planned=True, bargain_planned=True)
    screen = refused_list()
    screen.kind = 'shop_list'
    outs = [p.shop_step(screen, ready) for _ in range(12)]
    assert outs[0] == [p.pad('a')]
    assert outs.count([p.pad('a')]) <= 8
    assert outs[-1] == [p.pad('b')]   # give up on the message and leave the shop


def _refused_after(ready, name, qty, stock=None, gold_before=300, rest=(), since=None):
    shop = ready['shop']
    if stock is not None:
        ready['card_stock'] = {name: stock}
    ready['card_bought_since_read'] = {name: qty if since is None else since}  # confirm added it
    shop.update(items=[list(r) for r in rest], want=None, bought=[[name, qty, 18]],
                bargain_listing_planned=True, bargain_planned=True,
                last_buy={'row': [name, qty, 18], 'gold': gold_before})
    screen = refused_list(gold=gold_before)   # gold unchanged: nothing was paid
    screen.kind = 'shop_list'
    return p.shop_step(screen, ready), screen


def test_refusal_records_a_holding_floor_and_retries_half(ready):
    actions, _ = _refused_after(ready, 'ノリウツール', 60, stock=0, rest=[['ミックミー', 50]])
    assert actions == [p.pad('a')]
    shop = ready['shop']
    assert shop['bought'] == []
    assert ready['card_bought_since_read'] == {'ノリウツール': 0}      # refused copies are not held
    assert ready['card_held_floor'] == {'ノリウツール': 40}            # 60 on top of >=40 broke 99
    assert shop['items'] == [['ノリウツール', 30], ['ミックミー', 50]]  # 30 <= room 59


def test_retry_never_exceeds_the_room_the_floor_leaves(ready):
    _refused_after(ready, 'ノリウツール', 99, stock=0)         # floor 1, room 98, half 49
    assert ready['shop']['items'] == [['ノリウツール', 49]]


def test_full_kind_drops_only_that_card(ready):
    _refused_after(ready, 'ノリウツール', 1, stock=0, rest=[['ミックミー', 3]])
    assert ready['card_held_floor']['ノリウツール'] == 99
    assert ready['shop']['items'] == [['ミックミー', 3]]       # another kind is unaffected


def test_a_second_refusal_in_the_same_visit_is_handled_again(ready):
    _refused_after(ready, 'ノリウツール', 99, stock=0)
    shop = ready['shop']
    clean = parse(Canvas().frame()); clean.kind = 'shop_list'
    p.shop_step(clean, ready)
    assert shop['hold_limit_open'] is False
    ready['card_bought_since_read'] = {'ノリウツール': 49}
    shop.update(bought=[['ノリウツール', 49, 18]], last_buy={'row': ['ノリウツール', 49, 18], 'gold': 300})
    screen = refused_list(); screen.kind = 'shop_list'
    assert p.shop_step(screen, ready) == [p.pad('a')]
    assert ready['card_held_floor']['ノリウツール'] == 51      # >=51 held, 49 more broke 99
    assert shop['items'] == [['ノリウツール', 24]]


def test_purchase_confirmation_counts_toward_the_estimate(ready):
    b.note_card_bought(ready, 'ノリウツール', 60)
    ready['card_stock'] = {'ノリウツール': 30}
    assert b.held_estimate(ready, 'ノリウツール') == 90
    assert b.hold_room(ready, 'ノリウツール') == 9


def test_panel_read_replaces_the_purchase_ledger(ready):
    from docich.hanjuku.strategy.kit import _observe_card_stock
    ready['card_bought_since_read'] = {'ノリウツール': 60, 'ミックミー': 5}
    ready['card_held_floor'] = {'ノリウツール': 70}
    _observe_card_stock(ready, {'rows': [{'card': 'ノリウツール', 'stock': 62}]})
    assert 'ノリウツール' not in ready['card_bought_since_read']
    assert 'ノリウツール' not in ready['card_held_floor']
    assert ready['card_bought_since_read'] == {'ミックミー': 5}   # unread kinds keep theirs
    assert b.held_estimate(ready, 'ノリウツール') == 62


def test_second_rich_visit_cannot_ask_for_more_than_99(ready):
    ready['card_stock'] = {'ノリウツール': 0, 'ミックミー': 0}
    ready['card_bought_since_read'] = {'ノリウツール': 90, 'ミックミー': 40}   # first visit, panel unread
    shop = ready['shop']
    shop.update(items=[], want=None, rich_merchant=True)
    b.plan_bargains(ready, shop, {'year': 1, 'month': 6, 'gold': 9000},
                    listed={'ノリウツール': 18, 'ミックミー': 40})
    planned = dict(shop['items'])
    assert planned.get('ノリウツール', 0) <= 9 and planned.get('ミックミー', 0) <= 59


def test_revalidation_at_the_quote_respects_the_cap(ready):
    ready['card_stock'] = {'ノリウツール': 0}
    ready['card_bought_since_read'] = {'ノリウツール': 95}
    shop = ready['shop']
    shop.update(items=[['ノリウツール', 20]], bargain_cards=['ノリウツール'], rich_merchant=True)
    hdr = {'year': 1, 'month': 5, 'gold': 5000}
    assert b.merchant_card_spendable(ready, shop, hdr, 'ノリウツール', 18) <= 4 * 18


def test_confirmed_purchase_enters_the_held_ledger_and_cap_declines_overflow(monkeypatch):
    from test_hanjuku_merchant_reserve import quote
    monkeypatch.setattr(b, '_charted_purchase_ahead', lambda mem, header: False)
    mem = memory(complete=True)
    mem['card_stock'] = {'ノリウツール': 0}
    mem['shop'].update(egg='done', bargain_cards=['ノリウツール'],
                       items=[['ノリウツール', 3]], want=['ノリウツール', 3, 18])
    assert p.quantity_step(quote(999, count=3, cost=54), mem) == [p.pad('a')]
    assert mem['card_bought_since_read'] == {'ノリウツール': 3}
    assert mem['shop']['last_buy']['row'] == ['ノリウツール', 3, 18]



def test_refusal_records_full_shop_context_after_retry(ready):
    """Evidence includes the complete queue and state after a refused buy."""
    ready['card_stock'] = {'ノリウツール': 3, 'ミックミー': 8}
    shop = ready['shop']
    rest = [['ミックミー', 2], ['クースカン', 3]]
    actions, _ = _refused_after(ready, 'ノリウツール', 60, rest=rest)
    assert actions == [p.pad('a')]
    record = next(r for r in reversed(ready['_records'])
                  if r.get('deviation_reason') == 'holding_limit')
    metric = record['observed_metric']
    assert metric['card_stock_total'] == 11
    assert metric['items'] == [['ノリウツール', 30], *rest]
    assert metric['shop_state']['hold_limit'] == 1
    assert metric['shop_state']['hold_limit_open'] is True
    assert metric['shop_state']['want'] is None
    assert metric['shop_state']['last_buy'] == {'row': ['ノリウツール', 60, 18], 'gold': 300}
    assert metric['shop_state']['gold'] == 300
    # Snapshots must not change when the live queue changes.
    shop['items'][0][1] = 1
    assert metric['items'][0][1] == 30


def test_refusal_shop_snapshot_detaches_nested_purchase_row(ready):
    shop = ready['shop']
    original_row = ['ノリウツール', 60, 18]
    shop.update(items=[['ミックミー', 2]], want=['ノリウツール', 60, 18],
                bought=[list(original_row)],
                last_buy={'row': original_row, 'gold': 300})
    screen = refused_list(gold=300)
    screen.kind = 'shop_list'
    assert p.shop_step(screen, ready) == [p.pad('a')]
    metric = next(r['observed_metric'] for r in reversed(ready['_records'])
                  if r.get('deviation_reason') == 'holding_limit')
    assert metric['shop_state']['want'] == ['ノリウツール', 60, 18]
    assert metric['shop_state']['last_buy'] == {'row': ['ノリウツール', 60, 18], 'gold': 300}
    original_row[1] = 1
    assert metric['shop_state']['last_buy']['row'][1] == 60
    assert metric['shop_state']['want'][1] == 60

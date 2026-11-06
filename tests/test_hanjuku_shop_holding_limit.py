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


def _refused_after(ready, name, qty, stock=None, gold_before=300, rest=()):
    shop = ready['shop']
    if stock is not None:
        ready['card_stock'] = {name: stock}
    shop.update(items=[list(r) for r in rest], want=None, bought=[[name, qty, 18]],
                bargain_listing_planned=True, bargain_planned=True,
                last_buy={'row': [name, qty, 18], 'gold': gold_before})
    screen = refused_list(gold=gold_before)   # gold unchanged: nothing was paid
    screen.kind = 'shop_list'
    return p.shop_step(screen, ready), screen


def test_refusal_learns_the_ceiling_and_retries_half(ready):
    actions, _ = _refused_after(ready, 'ノリウツール', 99, stock=4, rest=[['ミックミー', 50]])
    assert actions == [p.pad('a')]
    shop = ready['shop']
    assert shop['bought'] == []
    assert ready['hold_ceiling'] == {'ノリウツール': 103}      # 4 held + 99 asked
    assert shop['items'] == [['ノリウツール', 49], ['ミックミー', 50]]


def test_a_second_refusal_in_the_same_visit_is_handled_again(ready):
    _refused_after(ready, 'ノリウツール', 99, stock=0)
    shop = ready['shop']
    ok = refused_list(); ok.text = ''                       # message closed
    clean = parse(Canvas().frame()); clean.kind = 'shop_list'
    p.shop_step(clean, ready)
    assert shop['hold_limit_open'] is False
    shop.update(bought=[['ノリウツール', 49, 18]], last_buy={'row': ['ノリウツール', 49, 18], 'gold': 300})
    screen = refused_list(); screen.kind = 'shop_list'
    assert p.shop_step(screen, ready) == [p.pad('a')]
    assert ready['hold_ceiling']['ノリウツール'] == 49          # tightened, never loosened
    assert shop['items'] == [['ノリウツール', 24]]


def test_single_copy_refusal_means_full_hand(ready):
    actions, _ = _refused_after(ready, 'ノリウツール', 1, stock=9,
                                rest=[['ミックミー', 3], ['クースカン', 2]])
    assert actions == [p.pad('a')]
    assert ready['shop']['items'] == []


def test_learned_ceiling_limits_later_rich_plan(ready):
    ready['hold_ceiling'] = {'ノリウツール': 11, 'ミックミー': 6}
    ready['card_stock'] = {'ノリウツール': 4, 'ミックミー': 1}
    shop = ready['shop']
    shop.update(items=[], want=None, rich_merchant=True)
    hdr = {'year': 1, 'month': 5, 'gold': 5000}
    b.plan_bargains(ready, shop, hdr, listed={'ノリウツール': 18, 'ミックミー': 40})
    planned = dict(shop['items'])
    assert planned['ノリウツール'] <= 6 and planned['ミックミー'] <= 4   # ceiling-1-stock


def test_ceiling_is_ignored_once_stock_is_used_up(ready):
    ready['hold_ceiling'] = {'ノリウツール': 11}
    assert b.hold_room(ready, 'ノリウツール', 0) == 10
    assert b.hold_room(ready, 'ノリウツール', 10) == 0
    assert b.hold_room(ready, 'ミックミー', 0) is None


def test_revalidation_at_the_quote_respects_the_ceiling(ready):
    ready['hold_ceiling'] = {'ノリウツール': 6}
    ready['card_stock'] = {'ノリウツール': 0}
    shop = ready['shop']
    shop.update(items=[['ノリウツール', 20]], bargain_cards=['ノリウツール'], rich_merchant=True)
    hdr = {'year': 1, 'month': 5, 'gold': 5000}
    assert b.merchant_card_spendable(ready, shop, hdr, 'ノリウツール', 18) <= 5 * 18

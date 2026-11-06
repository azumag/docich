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
    assert shop['hold_limit'] is True
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

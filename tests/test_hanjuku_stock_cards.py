"""docich#4685: a sortie must carry cards the country holds.

ch2 2026-11-19: two waves at アウスパジア opened at ally 37/38 against enemy 85
and retreated, while ノリウツール 21 and バグストーム 25 sat in stock and the
planned イッテツーン was at 0 (dropped at the card list, empty kit).
"""
from docich.hanjuku.strategy import replan


def test_wanted_cards_that_are_in_stock_are_kept():
    mem = {'card_stock': {'イッテツーン': 2, 'ノリウツール': 1}}
    assert replan._stock_cards(mem, ['イッテツーン', 'イッテツーン']) == ['イッテツーン', 'イッテツーン']


def test_an_empty_wanted_kit_falls_back_to_the_best_stocked_cards():
    mem = {'card_stock': {'イッテツーン': 0, 'ノリウツール': 21, 'バグストーム': 25}}
    assert replan._stock_cards(mem, ['イッテツーン', 'イッテツーン']) == ['バグストーム', 'ノリウツール']


def test_an_unread_stock_keeps_the_plan():
    assert replan._stock_cards({}, ['イッテツーン']) == ['イッテツーン']
    assert replan._stock_cards({'card_stock': {}}, ['イッテツーン']) == ['イッテツーン']


def test_a_fully_empty_stock_carries_nothing():
    assert replan._stock_cards({'card_stock': {'イッテツーン': 0}}, ['イッテツーン']) == []

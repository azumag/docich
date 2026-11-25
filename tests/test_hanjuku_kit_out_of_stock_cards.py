"""owner 2026-11-26 (#5510): 在庫が実測 0 の札は計画にあっても携行しない。"""
from docich.hanjuku.strategy import kit


def _mem(stock, cards):
    return {
        'card_stock': stock,
        'plan': {'chart_plan': {'orders': [{
            'step': 'A:1:M1', 'purpose': 'attack', 'source': 'アルマムーン',
            'target': 'タタール', 'general': 'カシュー', 'cards': cards,
        }]}},
        "chapter": 2,
    }


def _order():
    return {'step': 'A:1:M1', 'purpose': 'attack', 'source': 'アルマムーン',
            'target': 'タタール', 'general': 'カシュー',
            'cards': ['ミックミー']}


def test_a_planned_card_measured_zero_is_not_carried():
    mem = _mem({'ミックミー': 0}, ['ミックミー'])
    assert kit._deploy_cards(_order(), mem) == []


def test_a_planned_card_with_stock_is_still_carried():
    mem = _mem({'ミックミー': 2}, ['ミックミー'])
    assert kit._deploy_cards(_order(), mem) == ['ミックミー']


def test_an_unread_stock_does_not_drop_the_planned_card():
    mem = _mem(None, ['ミックミー'])
    assert kit._deploy_cards(_order(), mem) == ['ミックミー']

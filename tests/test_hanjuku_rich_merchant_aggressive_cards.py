"""2026-11-06 owner policy: rich merchants stock Noriutsuru/Mickmee; Balmung is boss-only."""

from docich import hanjuku_policy as p
from docich.hanjuku.strategy import bargains as b
from docich.hanjuku.flows import battle as flow
from docich.hanjuku.judgment import battle as rules
from test_hanjuku_merchant_bargains import ready, listing
from test_hanjuku_merchant_reserve import quote
from test_hanjuku_card_wear_down import memory as card_memory


def test_1000g_merchant_buys_both_without_monthly_caps(ready):
    rows = (('ノリウツール', 18), ('ミックミー', 40))
    assert p.shop_step(listing(1000, rows), ready) == [p.pad('a')]
    shop = ready['shop']
    planned = dict(shop['items'])
    assert shop['rich_merchant'] is True
    assert planned['ノリウツール'] > b.BARGAIN_TARGETS['ノリウツール']
    assert planned['ミックミー'] > b.BARGAIN_TARGETS['ミックミー']
    assert sum(dict(rows)[n] * qty for n, qty in planned.items()) > b.BARGAIN_MONTHLY_GOLD
    assert sum(dict(rows)[n] * qty for n, qty in planned.items()) <= b.bargain_spendable(
        ready, shop, listing(1000, rows).header, rich=True)
    assert max(planned.values()) <= 99


def test_real_quotes_remain_affordable_after_first_large_purchase(ready):
    rows = (('ノリウツール', 18), ('ミックミー', 40))
    p.shop_step(listing(1000, rows), ready)
    shop = ready['shop']
    first_name, first_qty = shop['items'][0]
    first_cost = first_qty * dict(rows)[first_name]
    assert p.quantity_step(quote(1000, count=first_qty, cost=first_cost), ready) == [p.pad('a')]
    assert shop['bought'] == [[first_name, first_qty, dict(rows)[first_name]]]
    after_first = 1000 - first_cost
    # The cursor remains on the first row after payment: moving to the next
    # merchant item is a legitimate down press, not an immediate selection.
    assert p.shop_step(listing(after_first, rows), ready) == [p.pad('down')]
    second_name, second_qty = shop['items'][0]
    second_cost = second_qty * dict(rows)[second_name]
    assert p.quantity_step(quote(after_first, count=second_qty, cost=second_cost), ready) == [p.pad('a')]
    # This particular merchant lists only the two names in rows, not the full
    # nine-card owner priority set.
    assert {row[0] for row in shop['bought']} == {name for name, _ in rows}
    assert after_first - second_cost >= 8 + 50 + b.BARGAIN_CASH_BUFFER


def test_balmung_is_for_bosses_only_even_when_listed():
    ordinary = card_memory('バルムンク', enemy='ヴィーナス', enemy_hp=82,
                           ally_hp=65, soldiers=(3, 6))
    gate = rules._card_damage_gate(ordinary, ordinary['battle'], 'バルムンク',
                                   listed_cards=['バルムンク'])
    assert (gate['allowed'], gate['role']) == (False, 'boss_only_balmung')
    boss = card_memory('バルムンク', enemy='クイーン', enemy_hp=82,
                       ally_hp=65, soldiers=(3, 6))
    gate = rules._card_damage_gate(boss, boss['battle'], 'バルムンク',
                                   listed_cards=['バルムンク'])
    assert gate['role'] != 'boss_only_balmung'


def test_confirmed_unused_cards_open_immediately_without_balmung(monkeypatch):
    order = {'step': 'test-step', 'general': 'ココット', 'target': 'normal'}
    monkeypatch.setattr(flow, '_order_for_step', lambda mem, step: order)
    monkeypatch.setattr(flow, 'bound_fields', lambda order, mem: {
        'general': 'ココット', 'cards': ['ノリウツール', 'ミックミー', 'バルムンク']})
    monkeypatch.setattr(flow, '_is_boss_order', lambda order, mem: False)
    monkeypatch.setattr(flow, '_boss_tactics_allowed', lambda mem, cur: False)
    cur = {'step': 'test-step', 'ally': 'ココット', 'enemy': 'クミン'}
    tactics = flow._carried_opening_tactics({}, cur, [])
    assert [t['card'] for t in tactics] == ['ノリウツール', 'ミックミー']
    assert all(t['open'] for t in tactics)
    # If the chart already owns a card's timing, do not fire that copy twice.
    specified = [{'card': 'ミックミー'}]
    assert [t['card'] for t in flow._carried_opening_tactics({}, cur, specified)] == ['ノリウツール']

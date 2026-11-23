"""2026-11-06 owner policy: rich merchants stock Noriutsuru/Mickmee; Balmung is boss-only."""

from docich import hanjuku_policy as p
from docich.hanjuku.strategy import bargains as b
from docich.hanjuku.flows import battle as flow
from docich.hanjuku.judgment import battle as rules
from test_hanjuku_merchant_bargains import ready, listing
from test_hanjuku_merchant_reserve import quote
from test_hanjuku_card_wear_down import memory as card_memory


def test_2000g_merchant_puts_the_strong_card_first_and_keeps_the_1000g_floor(ready):
    # Owner 2026-11-23: strong cards first, not expensive cards first.  The
    # strongest listed card is bought up to the 1000G floor.
    rows = (('ノリウツール', 18), ('ミックミー', 40))
    assert p.shop_step(listing(2000, rows), ready) == [p.pad('down')]
    assert p.shop_step(listing(2000, rows, selected=1), ready) == [p.pad('a')]
    shop = ready['shop']
    planned = dict(shop['items'])
    assert shop['rich_merchant'] is True
    assert [name for name, _ in shop['items']] == ['ミックミー', 'ノリウツール']
    assert planned['ノリウツール'] > b.BARGAIN_TARGETS['ノリウツール']
    assert planned['ミックミー'] > b.BARGAIN_TARGETS['ミックミー']
    want = shop.get('want') or [None, 0, 0]
    assert want[0] == 'ミックミー'
    assert want[1] * want[2] <= 1000      # the actual purchase keeps the floor
    assert max(planned.values()) <= 99


def test_after_buying_the_strong_card_to_the_floor_the_merchant_closes(ready):
    rows = (('ノリウツール', 18), ('ミックミー', 40))
    p.shop_step(listing(2000, rows), ready)
    p.shop_step(listing(2000, rows, selected=1), ready)
    shop = ready['shop']
    first_name, first_qty = shop['items'][0]
    first_cost = first_qty * dict(rows)[first_name]
    assert first_name == 'ミックミー'
    assert p.quantity_step(quote(2000, count=first_qty, cost=first_cost), ready) == [p.pad('a')]
    assert shop['bought'] == [[first_name, first_qty, dict(rows)[first_name]]]
    after_first = 2000 - first_cost
    assert after_first == 1000           # exactly the 1000G floor remains
    # Buying anything else would cross the floor, so the merchant closes
    # instead of selecting another card.
    assert p.shop_step(listing(after_first, rows), ready) == [p.pad('b')]


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

def test_rich_merchant_want_keeps_1000g_floor(ready):
    shop_state = ready['shop']
    shop_state['rich_merchant'] = True
    shop_state['want'] = ['ノリウツール', 11, 18]
    shop_state['items'] = []
    rows = (('ノリウツール', 18),)
    p.shop_step(listing(1093, rows), ready)
    want = ready['shop']['want']
    assert want[1] == 5          # (1093 - 1000) // 18
    assert ready['shop']['items'] == []

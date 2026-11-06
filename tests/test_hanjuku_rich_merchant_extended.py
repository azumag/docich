"""Rich merchant owner policy: all nine requested cards, then optional random stock.

All stock and quotes in these tests are synthetic, not observed live prices.
"""
from copy import deepcopy

from docich.hanjuku.strategy import bargains as b
from test_hanjuku_merchant_bargains import ready, listing


RICH_ROWS = {
    'ノリウツール': 18,
    'ミックミー': 40,
    'グリンボー': 6,
    'イッテツーン': 1,
    'ころぼぐんだん': 5,
    'フットバース': 15,  # No reference gold price; only an observed quote may authorize it.
    'ピッグローラー': 12,
    'ファバード': 40,
    'エンジェリン': 32,
}


def test_all_nine_requested_cards_are_considered_before_random(ready):
    shop = ready['shop']
    header = listing(9000).header
    b.plan_bargains(ready, shop, header, listed=RICH_ROWS)
    chosen = dict(shop['items'])
    assert set(b.RICH_MERCHANT_CARDS) == set(RICH_ROWS)
    assert all(chosen.get(name, 0) > 0 for name in RICH_ROWS)
    assert all(qty <= 99 for qty in chosen.values())
    assert sum(RICH_ROWS[name] * qty for name, qty in chosen.items()) <= 9000 - 50 - 8 - 30
    assert not shop.get('rich_random_cards')


def test_unknown_reference_price_is_not_assumed_without_a_listing(ready):
    shop = ready['shop']
    header = listing(1000).header
    b.plan_bargains(ready, shop, header)  # only preliminary, no invented stock
    assert all(name not in dict(shop['items']) for name in ('フットバース', 'ピッグローラー'))
    b.plan_bargains(ready, shop, header, listed={'フットバース': 15, 'ピッグローラー': 12})
    assert dict(shop['items']).get('フットバース', 0) > 0
    assert dict(shop['items']).get('ピッグローラー', 0) > 0


def test_requested_special_cards_only_use_rich_budget(ready):
    shop = ready['shop']
    b.plan_bargains(ready, shop, listing(999).header, listed={'フットバース': 15})
    assert shop['items'] == []


def test_spare_cash_may_buy_random_observed_cards_reproducibly(ready):
    header = listing(1000).header
    rows = {'ダンスライン': 5, 'カルゲンジー': 8, 'ラピニアール': 12, 'ビッグウェイブ': 88}
    initial = deepcopy(ready)
    b.plan_bargains(ready, ready['shop'], header, listed=rows)
    b.plan_bargains(initial, initial['shop'], header, listed=rows)
    chosen = dict(ready['shop']['items'])
    assert chosen == dict(initial['shop']['items'])
    selected = ready['shop']['rich_random_cards']
    assert selected and len(selected) <= b.RICH_RANDOM_MAX_KINDS
    assert set(chosen) == set(selected)
    assert all(0 < chosen[name] <= b.RICH_RANDOM_MAX_COPIES for name in selected)
    assert all(name not in b.RICH_MERCHANT_CARDS for name in selected)
    assert sum(rows[name] * qty for name, qty in chosen.items()) <= 1000 - 50 - 8 - 30
    for name in selected:
        assert b.merchant_card_spendable(ready, ready['shop'], header, name, rows[name]) >= rows[name]


def test_random_names_are_never_selected_below_rich_threshold(ready):
    b.plan_bargains(ready, ready['shop'], listing(999).header,
                    listed={'ダンスライン': 5, 'ラピニアール': 12})
    assert ready['shop']['items'] == []


def test_holding_room_limits_newly_requested_cards(ready):
    ready['card_stock'] = {'グリンボー': 96, 'ピッグローラー': 99}
    b.plan_bargains(ready, ready['shop'], listing(9000).header,
                    listed={'グリンボー': 6, 'ピッグローラー': 12})
    assert dict(ready['shop']['items']) == {'グリンボー': 3}

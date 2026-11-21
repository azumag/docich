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


def test_the_generic_opener_upgrades_to_the_recommended_strong_kit_when_abundant():
    # docich#4895: クースカン・ミックミーが潤沢なら暫定開幕札は推奨組
    # (クースカン+ミックミーx2、ID合計47 < 48) へ組み替える。
    mem = {'card_stock': {'クースカン': 4, 'ミックミー': 5, 'イッテツーン': 9}}
    assert replan._stock_cards(mem, None) == ['クースカン', 'ミックミー', 'ミックミー']


def test_the_upgrade_never_plans_a_card_the_stock_does_not_hold():
    # #4685 の退行防止: 在庫にないイッテツーンを混ぜてカード一覧で落とさない。
    mem = {'card_stock': {'ミックミー': 5}}
    assert replan._stock_cards(mem, None) == ['ミックミー', 'ミックミー']


def test_the_generic_opener_keeps_the_weak_kit_when_strong_stock_is_thin():
    # Every weak copy must be replaceable: a lone strong card still joins at the
    # card list via strong_card_kit, so the opener stays until 2+ spares exist.
    mem = {'card_stock': {'クースカン': 1, 'イッテツーン': 9}}
    assert replan._stock_cards(mem, None) == ['イッテツーン', 'イッテツーン']


def test_a_pinned_ittetsuun_kit_is_not_upgraded():
    # A chart order whose pinned cards happen to equal the interim opener
    # (2-S1 / 2-V2 in hanjuku_chart) keeps exactly those cards.
    mem = {'card_stock': {'クースカン': 9, 'ミックミー': 9, 'イッテツーン': 9}}
    assert replan._stock_cards(mem, ['イッテツーン', 'イッテツーン']) == ['イッテツーン', 'イッテツーン']


def test_an_unread_stock_keeps_the_generic_opener():
    assert replan._stock_cards({}, None) == list(replan.INTERIM_CARDS)
    assert replan._stock_cards({'card_stock': {}}, None) == list(replan.INTERIM_CARDS)


def test_pinned_copies_do_not_exceed_the_measured_stock():
    # Planning イッテツーンx2 on a stock of 1 stalls the list on the second copy.
    mem = {'card_stock': {'イッテツーン': 1, 'ノリウツール': 1}}
    assert replan._stock_cards(mem, ['イッテツーン', 'イッテツーン']) == ['イッテツーン']


def test_the_upgrade_leaves_the_pending_boss_kit_its_copies(monkeypatch):
    # g825: the strict boss kit's strong copies stay reserved for the boss order.
    from collections import Counter
    from docich.hanjuku.strategy import boss_cards
    monkeypatch.setattr(boss_cards, 'boss_kit',
                        lambda mem: ('x-B1', Counter({'クースカン': 1})))
    mem = {'card_stock': {'クースカン': 2, 'ミックミー': 5}}
    assert replan._stock_cards(mem, None) == ['クースカン', 'ミックミー', 'ミックミー']
    mem = {'card_stock': {'クースカン': 1, 'ミックミー': 5}}
    assert replan._stock_cards(mem, None) == ['ミックミー', 'ミックミー']


def test_generic_fallback_does_not_spend_boss_reserved_stock(monkeypatch):
    from collections import Counter
    from docich.hanjuku.strategy import boss_cards
    monkeypatch.setattr(boss_cards, 'boss_kit',
                        lambda mem: ('x-B1', Counter({'クースカン': 2})))
    mem = {'card_stock': {'イッテツーン': 0, 'クースカン': 2,
                          'ノリウツール': 1}}
    assert replan._stock_cards(mem, None) == ['ノリウツール']
    # Pinned chart orders retain the pre-existing fallback semantics.
    assert replan._stock_cards(mem, ['イッテツーン']) == ['クースカン', 'ノリウツール']

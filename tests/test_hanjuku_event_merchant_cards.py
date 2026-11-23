"""Random-event merchants (中華商人/屋台) buy 切り札 above the 1000G floor."""

from docich.hanjuku.flows import shop


def test_event_merchant_buys_cards_keeps_1000g():
    mem = {}
    state = shop._plan_event_merchant_cards(
        mem, None, '5-1', 1500,
        {'イッテツーン': 100, 'ノリウツール': 300, '兵士': 1}, event=True)
    assert state['event_merchant'] is True
    # Owner 2026-11-23: strong cards first, not expensive cards first.
    assert [r[0] for r in state['items']] == ['ノリウツール', 'イッテツーン']
    assert state[shop.EVENT_CARD_RESERVE] == 1000
    assert any(r['decision'] == 'event_merchant_cards' for r in mem.get('_records') or [])


def test_event_merchant_does_not_spend_into_the_floor():
    assert shop._plan_event_merchant_cards({}, None, '5-1', 1050, {'イッテツーン': 100}, event=True) is None
    assert shop._plan_event_merchant_cards({}, None, '5-1', 1000, {'イッテツーン': 100}, event=True) is None


def test_active_monthly_shop_plan_is_untouched():
    monthly = {'key': '5-1', 'items': [['イッテツーン', 1]], 'merchant_done': False}
    mem = {'shop': monthly}
    state = shop._plan_event_merchant_cards(
        mem, monthly, '5-1', 1500, {'ノリウツール': 300}, event=True)
    assert state['items'] == [['イッテツーン', 1]]
    assert not state.get('event_merchant')


def test_plain_monthly_merchant_is_not_reopened():
    assert shop._plan_event_merchant_cards({}, None, '5-1', 9999, {'ノリウツール': 300}) is None

def test_image_difference_detects_the_event_merchant():
    from types import SimpleNamespace
    screen = SimpleNamespace(text='')
    mem = {'month_shop_image': tuple([100] * 48),
           'screen_image': tuple([100] * 48)}
    assert shop._is_event_merchant(screen, mem) is False
    mem['screen_image'] = tuple([200] * 48)
    assert shop._is_event_merchant(screen, mem) is True
    assert shop._is_event_merchant(SimpleNamespace(text='中華商人'), mem) is True


def test_monthly_shop_image_is_remembered_once():
    mem = {'screen_image': (10, 20, 30)}
    shop_state = {'key': '5-1', 'items': [['イッテツーン', 1]]}
    shop._remember_month_shop_image(mem, shop_state, None)
    assert mem['month_shop_image'] == (10, 20, 30)
    mem['screen_image'] = (99, 99, 99)
    shop._remember_month_shop_image(mem, shop_state, None)
    assert mem['month_shop_image'] == (10, 20, 30)

def test_chinese_merchant_line_detects_the_event():
    from types import SimpleNamespace
    screen = SimpleNamespace(text='どれ　かてくれる　ちゅーか')
    assert shop._is_event_merchant(screen, {}) is True
    mem = {'event_merchant_pending': {'tick': 100}, 'tick': 120}
    assert shop._is_event_merchant(SimpleNamespace(text=''), mem) is True
    mem = {'event_merchant_pending': {'tick': 100}, 'tick': 700}
    assert shop._is_event_merchant(SimpleNamespace(text=''), mem) is False

def test_event_merchant_is_planned_only_once_per_visit():
    mem = {}
    first = shop._plan_event_merchant_cards(
        mem, None, '5-1', 1500, {'イッテツーン': 100}, event=True)
    assert first['event_planned'] is True
    assert [r['decision'] for r in mem['_records']].count('event_merchant_cards') == 1
    second = shop._plan_event_merchant_cards(
        mem, first, '5-1', 1400, {'イッテツーン': 100}, event=True)
    assert second['items'] == first['items']
    assert [r['decision'] for r in mem['_records']].count('event_merchant_cards') == 1

def test_card_strength_orders_strong_cards_first():
    from docich.hanjuku.strategy import bargains
    assert bargains.card_strength('ファバード') > bargains.card_strength('ノリウツール')
    assert bargains.card_strength('ミックミー') > bargains.card_strength('ころぼぐんだん')
    assert (bargains.card_strength('エンジェリン')
            > bargains.card_strength('グリンボー'))

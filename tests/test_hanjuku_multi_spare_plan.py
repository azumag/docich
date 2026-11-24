"""Regression coverage for #3266 plan-local sortie reservations."""

from docich.hanjuku.strategy import replan
from docich.hanjuku.state.regions import world as _world, plan as _plan_region, sorties as _sorties


def crowded_home():
    mem = {
        'chapter': 5, 'month': '42-6', 'tick': 100774,
        'captured': ['テラビル', 'ヒュペルボレア', 'ボッサール', 'アイアイエー',
                     'バスカビル', 'プトレマイス', 'オトラント'],
        'lost': ['ジュメルズ', 'クロチェット', 'カルーン'],
        'orders': {},
        'garrison': {
            'アイアイエー': [],
            'アルマムーン': ['どうし', 'ペコリーノ', 'クランベリー', 'マスカット',
                         'デーメーテール', 'ミルフィーユ'],
            'オトラント': ['マンゴスチン'], 'テラビル': ['リースリング'],
            'バスカビル': ['マカデミア'], 'ヒュペルボレア': ['ピスタチオ'],
            'プトレマイス': ['キャンディー'], 'ボッサール': ['ポセイドン'],
        },
        'route_probe_read_tick': {'アイアイエー': 74177},
        'sorties': {}, 'launched_orders': {}, '_records': [],
        'house_eggs': {}, 'general_location_unknown': [],
    }
    for step, general, tick in (
        ('A:0ca8ce6a:M1', 'ジェラート', 88300),
        ('A:d0871b56:M2', 'ピスタチオ', 93200),
        ('A:86477f11:M3', 'ラビオリ', 99900),
    ):
        _world(mem)['sorties'][step] = {
            'source': 'アルマムーン', 'target': 'アイアイエー',
            'general': general, 'status': 'overdue', 'tick': tick, 'purpose': 'move',
        }
        _plan_region(mem)['orders'][step] = 'failed'
    return mem


def retakes(mem):
    lost = set(mem['lost'])
    return [order for order in replan.interim_candidates(mem).values()
            if order.get('purpose') == 'retake' and order.get('target') in lost]


def test_one_plan_assigns_different_measured_spares_to_three_retakes():
    mem = crowded_home()
    _world(mem)['garrison']['アルマムーン'].append('アポロン')  # one extra spare while the home retains its guards
    # クロチェット is reachable from オトラント, which must retain one guard.
    _world(mem)['garrison']['オトラント'].extend(('ゼウス', 'バジル', 'グレナデン'))
    mem['route_probe_read_tick']['アイアイエー'] = mem['tick']
    orders = retakes(mem)
    assert {order['target'] for order in orders} == set(mem['lost']), replan.interim_candidates(mem)
    # A march may be redirected to an occupied own castle that lies on the route;
    # source identity is therefore intentionally not fixed to アルマムーン.
    generals = [order['general'] for order in orders]
    assert len(generals) == 3 and len(set(generals)) == 3
    # The dense plan still preserves the existing hero-castle guard budget.
    assert len(set(_world(mem)['garrison']['アルマムーン']) - set(generals)) >= 3


def test_a_general_already_marching_is_not_reused_by_the_dense_plan():
    mem = crowded_home()
    mem['route_probe_read_tick']['アイアイエー'] = mem['tick']
    _world(mem)['sorties']['busy'] = {
        'source': 'アルマムーン', 'target': 'アイアイエー', 'general': 'ペコリーノ',
        'status': 'en_route', 'tick': mem['tick'] - 10, 'purpose': 'move',
    }
    orders = retakes(mem)
    assert orders
    assert 'ペコリーノ' not in {order['general'] for order in orders}
    assert len({order['general'] for order in orders}) == len(orders)


def test_an_unread_arrival_castle_gets_only_one_probe_per_plan():
    mem = crowded_home()
    candidates = list(replan.interim_candidates(mem).values())
    probes = [order for order in candidates
              if order.get('source_probe') is True and order.get('source') == 'アイアイエー']
    assert len(probes) == 1


def test_two_spares_cover_only_two_of_three_lost_castles():
    """DSH review on #3268: when sendable spares run out mid-plan, the remaining
    target is dropped rather than reusing an already-planned general."""
    mem = crowded_home()
    mem['route_probe_read_tick']['アイアイエー'] = mem['tick']
    _world(mem)['garrison']['アルマムーン'] = _world(mem)['garrison']['アルマムーン'][:5]
    # Keep the オトラント march blocker from supplying a third sortie general:
    # its list was read empty, so no spare may leave there either.
    _world(mem)['garrison']['オトラント'] = []
    orders = retakes(mem)
    assert len(orders) == 2
    generals = [order['general'] for order in orders]
    assert len(set(generals)) == 2

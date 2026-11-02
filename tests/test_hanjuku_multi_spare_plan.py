"""Regression coverage for #3266 plan-local sortie reservations."""

from docich.hanjuku.strategy import replan


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
        mem['sorties'][step] = {
            'source': 'アルマムーン', 'target': 'アイアイエー',
            'general': general, 'status': 'overdue', 'tick': tick, 'purpose': 'move',
        }
        mem['orders'][step] = 'failed'
    return mem


def retakes(mem):
    lost = set(mem['lost'])
    return [order for order in replan.interim_candidates(mem).values()
            if order.get('purpose') == 'retake' and order.get('target') in lost]


def test_one_plan_assigns_different_home_spares_to_three_retakes():
    mem = crowded_home()
    mem['route_probe_read_tick']['アイアイエー'] = mem['tick']
    orders = retakes(mem)
    assert {order['target'] for order in orders} == set(mem['lost'])
    assert all(order['source'] == 'アルマムーン' for order in orders)
    generals = [order['general'] for order in orders]
    assert len(generals) == 3 and len(set(generals)) == 3
    assert set(mem['garrison']['アルマムーン']) - set(generals)


def test_a_general_already_marching_is_not_reused_by_the_dense_plan():
    mem = crowded_home()
    mem['route_probe_read_tick']['アイアイエー'] = mem['tick']
    mem['sorties']['busy'] = {
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

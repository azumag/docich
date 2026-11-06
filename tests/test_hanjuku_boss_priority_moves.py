"""Owner 2026-11-05: no empty-castle garrison moves while the boss is open.

The mechanical plan filled every empty own castle with a spare general while
the chapter-6 boss was the only target left, so armies marched to ユージア /
トウトウ / バビラリー / イカリア instead of the boss. Those spares are the next
boss waves (owner 2026-11-02: 余剰兵力を連続投入), so the fill stops once the
boss is a candidate.
"""
from docich import hanjuku_chart as chart
from docich.hanjuku.strategy import replan
from docich.hanjuku.state.regions import world as _world


def memory(chapter=6, garrison_home=('どうし', 'ヘルメス', 'トレビス', 'ココット', 'マスカット')):
    castles = list(chart.castles(chapter))
    mem = {
        'chapter': chapter, 'month': '55-6', 'tick': 74600,
        'captured': list(castles), 'lost': [], 'orders': {},
        'garrison': {c: [] for c in castles},
        'sorties': {}, 'launched_orders': {}, '_records': [],
        'house_eggs': {}, 'general_location_unknown': [],
    }
    _world(mem)['garrison']['アルマムーン'] = list(garrison_home)
    return mem


def candidates(mem):
    return list(replan.interim_candidates(mem).values())


def empty_moves(orders):
    return [order for order in orders if '空の' in (order.get('note') or '')]


def test_no_empty_castle_moves_while_the_boss_is_the_only_target():
    orders = candidates(memory())
    assert any(order['target'] == 'ボス' for order in orders)
    assert not empty_moves(orders)


def test_chapter_seven_boss_seed_prioritizes_boss_over_empty_castle_moves():
    # Chapter 7 now has a seeded approximate boss cell. Once all normal
    # castles are captured, spare generals must be available for boss waves.
    orders = candidates(memory(chapter=7))
    assert any(order['target'] == 'ボス' for order in orders)
    assert not empty_moves(orders)

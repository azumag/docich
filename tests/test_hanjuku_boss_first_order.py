"""Owner 2026-11-21: そもそもボス攻撃を最優先に.

The boss used to sit last in the interim candidate list, so retakes/staffing
ran first and the wave waited for everything else.
"""
from docich import hanjuku_chart as chart
from docich.hanjuku.strategy import replan
from docich.hanjuku.state.regions import world as _world


def memory():
    castles = list(chart.castles(6))
    mem = {
        'chapter': 6, 'month': '55-6', 'tick': 74600,
        'captured': list(castles), 'lost': [], 'orders': {},
        'garrison': {c: [] for c in castles},
        'world': {'sorties': {}},
        'house_eggs': {}, 'general_location_unknown': [],
    }
    _world(mem)['garrison']['アルマムーン'] = ['どうし', 'ヘルメス', 'トレビス']
    return mem


def test_the_boss_is_the_first_interim_candidate():
    orders = list(replan.interim_candidates(memory()).values())
    assert orders
    assert orders[0]['target'] == chart.boss_castle(6)

"""The rotation reinforces the thinnest castle first (v591).

g1163 chapter 8 2026-11-09 21:33: ながはま held only アテナ when キッシュ
attacked, and the castle fell. The rotation used to order targets by enemy
distance alone, so a farther one-defender castle could wait behind a
better-staffed nearer one.
"""
from docich import hanjuku_chart as chart
from docich.hanjuku.strategy import replan, orders

CELLS = chart.castles(8)


def memory():
    owned = [c for c in CELLS if c != 'ボス'][:4]
    home = chart.home_castle(8)
    garrison = {c: ['甲', '乙', '丙'] for c in owned}
    garrison[owned[1]] = ['甲']           # thin castle, second in distance order
    return {'chapter': 8, 'tick': 1000, '_records': [], 'captured': list(owned),
            'lost': [], 'orders': {}, 'sorties': {}, 'month': '14-8',
            'world': {'garrison': garrison}, 'general_statuses': {'statuses': {}},
            'house_eggs': {}}


def test_the_thinnest_castle_is_reinforced_first():
    mem = memory()
    plan = orders._garrison_plan(mem)
    thin = [c for c in plan if len((mem['world']['garrison'].get(c) or [])) == 1]
    assert thin, plan
    moves = replan._garrison_rotation(mem, sorted(orders._owned(mem)), set(), set())
    assert moves, 'the rotation must emit at least one move'
    if len(plan) > 1:
        assert moves[0].get('target') == thin[0], (moves, plan)

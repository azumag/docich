"""Boss-phase gathering at the last corridor castle (owner 2026-11-15)."""
from docich import hanjuku_chart as chart
from docich.hanjuku.strategy import replan
from docich.hanjuku.state.regions import world as _world


def memory():
    castles = list(chart.castles(9))
    mem = {'chapter': 9, 'month': '9-8', 'tick': 349000,
           'captured': list(castles), 'lost': [], 'orders': {},
           'garrison': {c: [] for c in castles},
           'world': {'sorties': {}}, 'house_eggs': {},
           'general_location_unknown': []}
    return mem


def test_boss_phase_walks_the_rear_generals_toward_the_last_castle():
    # Only marches from the last corridor castle (イデ！) reach the moving boss.
    chart.set_boss_cell(9, (700, 400))
    try:
        mem = memory()
        _world(mem)['garrison']['カラ'] = ['将軍A', '将軍B']
        _world(mem)['garrison']['シマウ'] = ['将軍C', '将軍D', '将軍E']
        orders = list(replan.interim_candidates(mem).values())
        moves = [o for o in orders if o.get("purpose") == "move"]
        assert moves
        assert any("イデ" in (o.get("note") or "") for o in moves)
        assert all(o['target'] in chart.castles(9) for o in moves)
    finally:
        chart.set_boss_cell(9, None)
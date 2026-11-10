"""An unread garrison still receives a second defender (v583).

g1163 chapter 7 2026-11-09 19:49: フィロメラ fell while its defender fought and
the bookkeeping held no garrison row at all, so the rotation skipped the castle
it should have reinforced.
"""
from docich import hanjuku_chart as chart
from docich.hanjuku.strategy import replan, orders

CELLS = chart.castles(7)


def memory(unread_target=None):
    owned = [c for c in CELLS if c != 'ボス'][:4]
    home = chart.home_castle(7)
    garrison = {}
    for i, castle in enumerate(owned):
        garrison[castle] = [f'将軍{i}a', f'将軍{i}b', f'将軍{i}c']
    if unread_target:
        garrison.pop(unread_target, None)
    return {'chapter': 7, 'tick': 1000, '_records': [], 'captured': list(owned),
            'lost': [], 'orders': {}, 'sorties': {},
            'world': {'garrison': garrison}, 'general_statuses': {'statuses': {}},
            'house_eggs': {}, 'month': '14-6'}


def test_a_castle_with_no_garrison_row_is_reinforced():
    mem = memory()
    plan = orders._garrison_plan(mem)
    assert plan, 'the rotation needs a plan'
    # The front ring is what the rotation fills first.
    target = min(plan, key=lambda c: plan[c][0])
    mem = memory(unread_target=target)
    moves = replan._garrison_rotation(mem, sorted(orders._owned(mem)), set(), set())
    assert any(m.get('target') == target for m in moves), moves

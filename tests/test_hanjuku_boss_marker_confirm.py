"""The boss target may be confirmed by the marker on its measured cell (v410).

g1133 2026-11-05: three Y jumps to the boss ended on stationary cursor reads,
and the target could only be confirmed by a Y jump, so the base stayed
unassailable while the plan kept re-issuing the wave. The cell itself was
verified against a captured world-map frame in v402, so a marker standing on
it is the same evidence without the jump.
"""
from docich import hanjuku_chart as chart
from docich.hanjuku.flows import map as M
from docich.hanjuku.os_layer.screen import Frame, Screen

CELL = chart.castles(6)['ボス']
FRAME = Frame(256, 224, b'\x00' * (256 * 224 * 3))
ORDER = {'step': 'A:x:M1', 'target': 'ボス', 'source': 'アルマムーン'}


def screen(cursor):
    return Screen([], None, '', kind='map_target', cursor=cursor)


def memory(cursor, **extra):
    return {'chapter': 6, 'tick': 10, 'cursor': list(cursor), 'garrison': {}, **extra}


def test_a_marker_on_the_measured_cell_confirms_without_a_jump():
    mem = memory(CELL)
    assert M._target_roof_under_marker(screen(CELL), mem, FRAME, ORDER) is True


def test_a_marker_far_from_the_cell_does_not_confirm():
    far = (CELL[0] + 200, CELL[1] + 200)
    mem = memory(far)
    assert M._target_roof_under_marker(screen(far), mem, FRAME, ORDER) is False


def test_a_y_jump_confirmation_still_counts():
    far = (CELL[0] + 200, CELL[1] + 200)
    mem = memory(far, y_jumped={ORDER['step']: 'ボス'})
    assert M._target_roof_under_marker(screen(far), mem, FRAME, ORDER) is True


def test_a_normal_castle_still_needs_its_roof():
    order = {'step': 'A:x:M2', 'target': 'カバルッサ', 'source': 'アルマムーン'}
    mem = memory(CELL)
    assert M._target_roof_under_marker(screen(CELL), mem, FRAME, order) is False

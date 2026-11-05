"""A stuck position re-arms the Y-jump budget (v436).

g1133 2026-11-05: the boss order could neither walk (the position estimate was
frozen) nor jump (the per-order run budget from v426 was spent), so no sortie
left for hours. When the estimate has been stuck for CURSOR_STILL_LIMIT frames
the budget is re-armed: the Y view re-places the cursor absolutely.
"""
from docich.hanjuku.os_layer import navigation as N


def memory(still=0):
    mem = {'chapter': 1, '_records': [], 'cursor': [300, 300], 'uncertain': True}
    mem['tally'] = {f'yjump:X:map': N.Y_JUMP_TOTAL_LIMIT}
    mem['y_jumps_total'] = {'X:map': N.Y_JUMP_TOTAL_LIMIT}
    mem['cursor_still'] = {'cell': [300, 300], 'n': still}
    return mem


def order():
    return {'step': 'X'}


def test_a_spent_budget_still_blocks_a_moving_position():
    mem = memory(still=0)
    assert N._want_y_jump(mem, order(), 'アルマムーン', 'map') is False


def test_a_stuck_position_re_arms_the_budget():
    mem = memory(still=N.CURSOR_STILL_LIMIT)
    assert N._want_y_jump(mem, order(), 'アルマムーン', 'map') is True
    assert mem['y_jumps_total']['X:map'] == 0
    assert mem['tally']['yjump:X:map'] == 0

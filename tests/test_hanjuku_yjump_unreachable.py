"""A goal the Y view cannot align is not retried once a minute forever.

g1163 2026-11-12 08:29-08:31: every attempt for the same castle ended with the
identical residual [0.0, -11.5] after eight moves, once a minute, each costing a
Y open, eight moves and a close. A new order step resets the per-step budget, so
only the goal itself can carry the memory.
"""
from docich.hanjuku.os_layer import navigation as nav


def memory(tick=1000):
    return {'tick': tick, 'month': '25-5', 'chapter': 9}


def fail(mem, goal, residual, tick):
    mem['tick'] = tick
    nav._note_unreachable_goal(mem, goal, residual)


def test_one_failure_is_not_enough():
    mem = memory()
    fail(mem, 'オシロガ', (0.0, -11.5), 1000)
    assert nav.y_jump_unreachable(mem, 'オシロガ') is False


def test_the_same_residual_twice_marks_the_goal():
    mem = memory()
    fail(mem, 'オシロガ', (0.0, -11.5), 1000)
    fail(mem, 'オシロガ', (0.0, -11.5), 1060)
    assert nav.y_jump_unreachable(mem, 'オシロガ') is True


def test_a_different_residual_does_not_accumulate():
    mem = memory()
    fail(mem, 'オシロガ', (0.0, -11.5), 1000)
    fail(mem, 'オシロガ', (0.0, 2.0), 1060)
    assert nav.y_jump_unreachable(mem, 'オシロガ') is False


def test_a_large_residual_is_left_to_the_moves():
    mem = memory()
    fail(mem, 'オシロガ', (40.0, 30.0), 1000)
    fail(mem, 'オシロガ', (40.0, 30.0), 1060)
    assert nav.y_jump_unreachable(mem, 'オシロガ') is False


def test_the_mark_ages_out():
    mem = memory()
    fail(mem, 'オシロガ', (0.0, -11.5), 1000)
    fail(mem, 'オシロガ', (0.0, -11.5), 1060)
    mem['tick'] = 1060 + nav.Y_JUMP_UNREACHABLE_TICKS
    assert nav.y_jump_unreachable(mem, 'オシロガ') is False

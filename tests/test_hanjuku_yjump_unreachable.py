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


def test_a_repeated_large_residual_marks_the_goal():
    # #4416 (g1190 第10話): the detected point stayed put at a large residual
    # through all eight moves — the gate dropping readings where both axes
    # exceed PX discarded them, and the order retried the identical failure
    # until its budget ran out. A repeated large residual is the same stuck
    # signature, so it counts.
    mem = memory()
    fail(mem, 'オシロガ', (-40.5, 45.0), 1000)
    assert nav.y_jump_unreachable(mem, 'オシロガ') is False
    fail(mem, 'オシロガ', (-40.5, 45.0), 1060)
    assert nav.y_jump_unreachable(mem, 'オシロガ') is True


def test_jittered_repeats_within_the_px_window_count():
    # The incident readings drifted 3 px on one axis ([-40.5,45] then
    # [-43.5,45]): identical read points quantise and jitter, so "same spot"
    # is a window, not an exact match.
    mem = memory()
    fail(mem, 'オシロガ', (-40.5, 45.0), 1000)
    fail(mem, 'オシロガ', (-43.5, 45.0), 1060)
    assert nav.y_jump_unreachable(mem, 'オシロガ') is True


def test_landings_beyond_the_px_window_reset():
    mem = memory()
    fail(mem, 'オシロガ', (-40.5, 45.0), 1000)
    fail(mem, 'オシロガ', (-40.5, 38.0), 1060)
    assert nav.y_jump_unreachable(mem, 'オシロガ') is False


def test_a_single_large_residual_does_not_mark():
    # One large residual can still be the moves simply running out — it only
    # proves the goal is unreachable when the same landing repeats.
    mem = memory()
    fail(mem, 'オシロガ', (40.0, 30.0), 1000)
    fail(mem, 'オシロガ', (12.0, 30.0), 1060)
    assert nav.y_jump_unreachable(mem, 'オシロガ') is False


def test_the_mark_ages_out():
    mem = memory()
    fail(mem, 'オシロガ', (0.0, -11.5), 1000)
    fail(mem, 'オシロガ', (0.0, -11.5), 1060)
    mem['tick'] = 1060 + nav.Y_JUMP_UNREACHABLE_TICKS
    assert nav.y_jump_unreachable(mem, 'オシロガ') is False

"""The Y-jump budget survives a plan rebuild (v426).

g1133 2026-11-05: the same order re-opened the Y view 22 times a minute (128 in
11 minutes under v424) because the per-order jump counter was reset whenever the
plan was rebuilt, and the plan is rebuilt on every observation in this run
(issue #3417). A tally is kept beside the state and does survive, so the total
number of jumps per order and screen mode is now bounded for the whole run.
"""
from docich.hanjuku.os_layer import navigation as N


def memory():
    return {'chapter': 1, '_records': [], 'cursor': [300, 300], 'uncertain': True}


def order():
    return {'step': 'X'}


def test_the_total_budget_stops_a_reset_counter():
    mem = memory()
    for _ in range(N.Y_JUMP_TOTAL_LIMIT):
        mem['y_jumps'] = {}          # a plan rebuild forgets the per-order count
        assert N._want_y_jump(mem, order(), 'アルマムーン', 'map') is True
        N._start_y_jump(mem, order(), 'アルマムーン', 'map')
        mem.pop('y_jump', None)
    mem['y_jumps'] = {}
    assert N._want_y_jump(mem, order(), 'アルマムーン', 'map') is False


def test_another_mode_has_its_own_budget():
    mem = memory()
    for _ in range(N.Y_JUMP_TOTAL_LIMIT):
        mem['y_jumps'] = {}
        N._start_y_jump(mem, order(), 'アルマムーン', 'map')
        mem.pop('y_jump', None)
    assert N._want_y_jump(mem, order(), 'アルマムーン', 'target') is True

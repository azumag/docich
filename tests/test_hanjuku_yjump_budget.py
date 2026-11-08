"""The Y-jump budget survives a plan rebuild and a state reload (v427).

g1133 2026-11-05: the same order re-opened the Y view 22 times a minute because
the per-order jump counter was reset whenever the plan was rebuilt (issue
#3417). The budget is kept in two stores - the tally (which survived in this
run) and a plain dict (which survives a reload) - and either one reaching the
cap stops the jumps.
"""
from docich.hanjuku.os_layer import navigation as N


def memory():
    return {'chapter': 1, '_records': [], 'cursor': [300, 300], 'uncertain': True}


def order():
    return {'step': 'X'}


def test_the_plain_dict_alone_stops_the_jumps():
    mem = memory()
    mem['y_jumps_total'] = {'X:map': N.Y_JUMP_TOTAL_LIMIT}
    assert N._want_y_jump(mem, order(), 'アルマムーン', 'map') is False


def test_the_tally_alone_stops_the_jumps():
    mem = memory()
    mem['tally'] = {f'yjump:X:map': N.Y_JUMP_TOTAL_LIMIT}
    assert N._want_y_jump(mem, order(), 'アルマムーン', 'map') is False


def test_both_stores_are_counted_on_a_start():
    mem = memory()
    N._start_y_jump(mem, order(), 'アルマムーン', 'map')
    assert mem['y_jumps_total']['X:map'] == 1
    assert mem['tally']['yjump:X:map'] == 1

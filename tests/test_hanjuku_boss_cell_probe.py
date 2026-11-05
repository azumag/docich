"""Boss waves test different entry tiles in parallel (v404).

g1133 2026-11-05: the chapter-6 boss cell (818, 184) is the building centre
measured from the Y view (v402), yet armies that verifiably left their castle
never entered it (v402 record: marching 1634, source_still_has_general false).
Waiting one tile at a time costs a full march window per candidate, so each new
boss order now takes the next candidate in rotation and the waves sweep the
list in parallel.
"""
from docich.hanjuku.os_layer import navigation as N
from docich.hanjuku.strategy import replan


def plan(*steps):
    return {'source': 'mechanical',
            'orders': [{'step': s, 'general': 'ヘルメス', 'source': 'アルマムーン',
                        'target': 'ボス', 'cards': [], 'after': None} for s in steps]}


def test_the_first_boss_cell_is_the_measured_chart_cell():
    assert N.boss_probe_cell({'chapter': 6}, 'ボス', 'A:x:M1') == (818, 184)


def test_each_new_boss_order_takes_the_next_candidate():
    mem = {'chapter': 6, 'tick': 1, '_records': []}
    replan._adopt_plan(mem, plan('M1', 'M2', 'M3'), 'abcd1234')
    assigned = mem['boss_step_probe']
    assert len(assigned) == 3
    assert sorted(assigned.values()) == [0, 1, 2]


def test_a_step_keeps_its_candidate_when_the_plan_is_rebuilt():
    mem = {'chapter': 6, 'tick': 1, '_records': []}
    replan._adopt_plan(mem, plan('M1'), 'abcd1234')
    first = dict(mem['boss_step_probe'])
    replan._adopt_plan(mem, plan('M1'), 'abcd1234')
    assert mem['boss_step_probe'] == first


def test_the_aim_follows_the_order_not_the_chapter():
    mem = {'chapter': 6, 'boss_step_probe': {'A:x:M1': 1, 'A:x:M2': 2}}
    assert N.boss_probe_cell(mem, 'ボス', 'A:x:M1') == (818, 184 + 64)
    assert N.boss_probe_cell(mem, 'ボス', 'A:x:M2') == (818 + 64, 184)
    assert N.boss_probe_cell(mem, 'ボス', 'A:x:M9') == (818, 184)


def test_a_normal_castle_is_never_probed():
    mem = {'chapter': 6, 'boss_step_probe': {'A:x:M1': 2}}
    assert N.boss_probe_cell(mem, 'カバルッサ', 'A:x:M1') == (536, 288)


def test_the_assignment_stays_bounded():
    mem = {'chapter': 6, 'tick': 1, '_records': []}
    for i in range(50):   # over the 40-entry bound
        replan._adopt_plan(mem, plan(f'M{i}'), f'rid{i}')
    assert len(mem['boss_step_probe']) <= 40

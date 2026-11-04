"""A boss cell that never accepts an army is probed one tile at a time (v403).

g1133 2026-11-05: eight boss armies left アルマムーン and オケアナ and none
entered the 完熟秘密基地. The chart cell (818, 184) is the building centre
measured from the Y view (v402), so the next suspect is the tile an army may
enter from. Chapter 6 therefore carries a bounded probe list; the relaunch
budget advances one tile before the step is parked.
"""
from docich.hanjuku.os_layer import navigation as N
from docich.hanjuku.strategy import orders as O

LAUNCH = 40000


def memory(tick):
    return {'chapter': 6, 'tick': tick, '_records': [],
            'orders': {'6-B1': 'launched'},
            'sorties': {'6-B1': {'general': 'ヘルメス', 'source': 'アルマムーン',
                                 'target': 'ボス', 'status': 'en_route', 'tick': LAUNCH}},
            'launched_orders': {}}


def decisions(mem, name):
    return [r for r in mem.get('_records', []) if r['decision'] == name]


def test_the_first_probe_is_the_measured_chart_cell():
    assert N.boss_probe_cell({'chapter': 6}, 'ボス') == (818, 184)


def test_a_probe_index_moves_the_aim_one_tile():
    mem = {'chapter': 6, 'boss_cell_probe': {6: 1}}
    assert N.boss_probe_cell(mem, 'ボス') == (818, 184 + 64)


def test_a_normal_castle_is_never_probed():
    mem = {'chapter': 6, 'boss_cell_probe': {6: 2}}
    assert N.boss_probe_cell(mem, 'カバルッサ') == (536, 288)


def test_the_budget_exhaustion_moves_to_the_next_probe_tile():
    mem = memory(LAUNCH + O.MARCH_ARRIVAL_CAP)
    for n in range(1, O.BOSS_RELAUNCH_LIMIT + 1):
        O._relaunch_overdue_boss(mem)
        mem['orders']['6-B1'] = 'launched'
        mem['sorties']['6-B1'] = {'general': 'ヘルメス', 'source': 'アルマムーン',
                                   'target': 'ボス', 'status': 'en_route', 'tick': mem['tick']}
        mem['tick'] += O.MARCH_ARRIVAL_CAP
    O._relaunch_overdue_boss(mem)
    assert mem['boss_cell_probe'] == {6: 1}
    assert mem['boss_relaunch'] == {'6-B1': 1}
    assert mem['orders']['6-B1'] == 'pending'
    assert len(decisions(mem, 'boss_cell_probe_shift')) == 1
    assert not decisions(mem, 'boss_relaunch_exhausted')


def test_the_probe_list_is_bounded_and_then_parks():
    mem = memory(LAUNCH + O.MARCH_ARRIVAL_CAP)
    mem['boss_cell_probe'] = {6: len(N.BOSS_CELL_PROBES[6]) - 1}
    mem['boss_relaunch'] = {'6-B1': O.BOSS_RELAUNCH_LIMIT}
    O._relaunch_overdue_boss(mem)
    assert mem['boss_cell_probe'] == {6: len(N.BOSS_CELL_PROBES[6]) - 1}
    assert mem['orders']['6-B1'] == 'launched'
    assert len(decisions(mem, 'boss_relaunch_exhausted')) == 1


def test_chapter_three_keeps_its_single_cell():
    mem = {'chapter': 3, 'tick': LAUNCH + 9000, '_records': [],
           'orders': {'3-B1': 'launched'},
           'sorties': {'3-B1': {'general': 'アポロン', 'source': 'パタゴーヌ',
                                'target': 'ボス', 'status': 'en_route', 'tick': LAUNCH}},
           'launched_orders': {}}
    for _ in range(O.BOSS_RELAUNCH_LIMIT + 1):
        O._relaunch_overdue_boss(mem)
        mem['orders']['3-B1'] = 'launched'
        mem['sorties']['3-B1'] = {'general': 'アポロン', 'source': 'パタゴーヌ',
                                  'target': 'ボス', 'status': 'en_route', 'tick': mem['tick']}
        mem['tick'] += O.BOSS_ARRIVAL_LIMIT
    assert 'boss_cell_probe' not in mem

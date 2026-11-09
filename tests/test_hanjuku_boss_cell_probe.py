"""Boss waves test different entry tiles in parallel (v404).

g1133 2026-11-05: the chapter-6 boss cell (818, 120) is the building centre
measured from the Y view (v402), yet armies that verifiably left their castle
never entered it (v402 record: marching 1634, source_still_has_general false).
Waiting one tile at a time costs a full march window per candidate, so each new
boss order now takes the next candidate in rotation and the waves sweep the
list in parallel.
"""
from docich import hanjuku_chart as chart
from docich.hanjuku.os_layer import navigation as N
from docich.hanjuku.strategy import orders as O
from docich.hanjuku.strategy import replan


def plan(*steps):
    return {'source': 'mechanical',
            'orders': [{'step': s, 'general': 'ヘルメス', 'source': 'アルマムーン',
                        'target': 'ボス', 'cards': [], 'after': None} for s in steps]}


def test_the_first_boss_cell_is_the_measured_chart_cell():
    assert N.boss_probe_cell({'chapter': 6}, 'ボス', 'A:x:M1') == (818, 120)


def test_each_new_boss_order_takes_the_next_candidate():
    mem = {'chapter': 6, 'tick': 1, '_records': []}
    replan._adopt_plan(mem, plan('M1', 'M2', 'M3'), 'abcd1234')
    assigned = mem['boss_step_probe']
    assert len(assigned) == 3
    assert sorted(assigned.values()) == [0, 1, 2]


def test_the_rotation_survives_a_json_round_trip():
    # g1133 2026-11-05: the policy reloads between observations, so the stored
    # {"6": n} key comes back as a string while chapter is still int 6. Every
    # boss step then re-read index 0 and all waves aimed at the same cell.
    mem = {'chapter': 6, 'tick': 1, '_records': [],
           'boss_probe_rotation': {'6': 1}}
    replan._adopt_plan(mem, plan('M9'), 'abcd1234')
    assert mem['boss_step_probe']['A:abcd1234:M9'] == 1
    assert mem['boss_probe_rotation']['6'] == 2


def test_a_step_keeps_its_candidate_when_the_plan_is_rebuilt():
    mem = {'chapter': 6, 'tick': 1, '_records': []}
    replan._adopt_plan(mem, plan('M1'), 'abcd1234')
    first = dict(mem['boss_step_probe'])
    replan._adopt_plan(mem, plan('M1'), 'abcd1234')
    assert mem['boss_step_probe'] == first


def test_the_aim_follows_the_order_not_the_chapter():
    mem = {'chapter': 6, 'boss_step_probe': {'A:x:M1': 1, 'A:x:M2': 2}}
    assert N.boss_probe_cell(mem, 'ボス', 'A:x:M1') == (818 + 16, 120)
    assert N.boss_probe_cell(mem, 'ボス', 'A:x:M2') == (818 - 16, 120)
    assert N.boss_probe_cell(mem, 'ボス', 'A:x:M9') == (818, 120)


def test_a_normal_castle_is_never_probed():
    mem = {'chapter': 6, 'boss_step_probe': {'A:x:M1': 2}}
    assert N.boss_probe_cell(mem, 'カバルッサ', 'A:x:M1') == (536, 288)


def test_the_assignment_stays_bounded():
    mem = {'chapter': 6, 'tick': 1, '_records': []}
    for i in range(50):   # over the 40-entry bound
        replan._adopt_plan(mem, plan(f'M{i}'), f'rid{i}')
    assert len(mem['boss_step_probe']) <= 40


def overdue_boss(index=0, tick=100000):
    return {'chapter': 6, 'tick': tick, '_records': [],
            'orders': {'A:x:M1': 'launched'},
            'sorties': {'A:x:M1': {'general': 'ヘルメス', 'source': 'アルマムーン',
                                   'target': 'ボス', 'status': 'en_route',
                                   'tick': tick - O.MARCH_ARRIVAL_CAP - 1}},
            'launched_orders': {},
            'boss_step_probe': {'A:x:M1': index}}


def test_an_unentered_boss_relaunch_advances_the_step_candidate():
    # g1133 2026-11-05: the step kept re-sending cell (818, 120) for 2500
    # observations while the wave that entered used candidate 2.
    mem = overdue_boss(index=0)
    O._relaunch_overdue_boss(mem)
    assert mem['boss_step_probe']['A:x:M1'] == 1
    assert N.boss_probe_cell(mem, 'ボス', 'A:x:M1') == (818 + 16, 120)
    assert mem['orders']['A:x:M1'] == 'pending'
    assert mem['boss_relaunch']['A:x:M1'] == 1
    assert [r for r in mem['_records'] if r['decision'] == 'boss_cell_probe_shift']


def test_the_last_candidate_is_not_shifted_past_the_list():
    mem = overdue_boss(index=len(chart.BOSS_CELL_PROBES[6]) - 1)
    O._relaunch_overdue_boss(mem)
    assert mem['boss_step_probe']['A:x:M1'] == len(chart.BOSS_CELL_PROBES[6]) - 1
    assert mem['orders']['A:x:M1'] == 'pending'      # normal relaunch still runs
    assert not [r for r in mem['_records'] if r['decision'] == 'boss_cell_probe_shift']


def test_an_arrived_boss_relaunch_keeps_the_same_candidate():
    # A completed entry must not be treated as evidence that the tile is wrong.
    mem = overdue_boss(index=1)
    mem['sorties']['A:x:M1']['status'] = 'arrived'
    O._relaunch_overdue_boss(mem)
    assert mem['boss_step_probe']['A:x:M1'] == 1
    assert not [r for r in mem['_records'] if r['decision'] == 'boss_cell_probe_shift']


# Chapter 5's cell is WRAM-measured and chapter 9's boss moves, so neither may
# probe. Chapters 6, 7, 8, 10, 11 and 12 carry a source-map or Y-view estimate
# and a miss there would re-send the same tile until the chapter can never end.
import pytest


@pytest.mark.parametrize('chapter', [6, 7, 8, 10, 11, 12])
def test_every_estimated_boss_cell_can_be_probed(chapter):
    cell = chart.castles(chapter)['ボス']
    assert N.boss_probe_cell({'chapter': chapter, 'boss_step_probe': {}}, 'ボス', 'A:x:M1') == cell
    assert N.boss_probe_cell({'chapter': chapter, 'boss_step_probe': {'A:x:M1': 1}},
                             'ボス', 'A:x:M1') == (cell[0] + 16, cell[1])


@pytest.mark.parametrize('chapter', [5, 9])
def test_a_measured_or_moving_boss_never_probes(chapter):
    assert chapter not in chart.BOSS_CELL_PROBES


@pytest.mark.parametrize('chapter', [7, 10, 12])
def test_an_unentered_endgame_boss_relaunch_advances_its_candidate(chapter):
    cell = chart.castles(chapter)['ボス']
    mem = {'chapter': chapter, 'tick': 100000, '_records': [],
           'orders': {'A:x:M1': 'launched'},
           'sorties': {'A:x:M1': {'general': 'ヘルメス', 'source': 'アルマムーン',
                                  'target': 'ボス', 'status': 'en_route',
                                  'tick': 100000 - O.MARCH_ARRIVAL_CAP - 1}},
           'launched_orders': {}, 'boss_step_probe': {'A:x:M1': 0}}
    O._relaunch_overdue_boss(mem)
    assert mem['boss_step_probe']['A:x:M1'] == 1
    assert N.boss_probe_cell(mem, 'ボス', 'A:x:M1') == (cell[0] + 16, cell[1])
    assert [r for r in mem['_records'] if r['decision'] == 'boss_cell_probe_shift']


def test_the_first_ring_covers_a_two_tile_estimate_error():
    """All eight neighbouring tiles must be reachable before the 8-tile ring."""
    probes = chart.BOSS_CELL_PROBES[7]
    assert probes[0] == (0, 0)
    axis = {(16, 0), (-16, 0), (0, 16), (0, -16)}
    diagonal = {(16, 16), (-16, 16), (16, -16), (-16, -16)}
    assert axis | diagonal == set(probes[1:9])
    assert {(64, 0), (-64, 0), (0, 64), (0, -64)} == set(probes[9:])

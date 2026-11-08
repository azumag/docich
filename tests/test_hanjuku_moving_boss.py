"""Chapter 9's moving boss is located from the Y view's boss skull.

The boss has no static cell, so the bot never attacked it. The Y view draws it
with chapter 3's boss skull; the measured skull position maps to the chart
cell (chapter 3 measured: skull (101, 63) = cell (306, 141)). A fresh reading
is the only thing that unlocks the boss order.
"""
from pathlib import Path

import pytest

from docich import hanjuku_chart as chart
from docich.hanjuku.os_layer import world as W
from docich.hanjuku.os_layer.pixels import Frame, read_png
from docich.hanjuku.os_layer.readers import skull_cell_at, y_view_skulls
from docich.hanjuku.strategy import orders

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'hanjuku'
CH3 = read_png(FIXTURES / 'g1075-ch3-y-view-boss-skull.png')
CH4 = read_png(FIXTURES / 'g1075-ch4-y-view-season-skulls.png')


def test_the_reader_finds_chapter_three_boss_skull_and_its_cell():
    assert y_view_skulls(CH3) == [(101, 63)]
    assert skull_cell_at((101, 63), 3) == chart.CASTLES[3]['ボス']
    assert chart.CASTLES[3]['ボス'] == (306, 141)


def test_the_four_chapter_four_skulls_map_to_the_registered_sites():
    found = y_view_skulls(CH4)
    assert len(found) == 4
    assert {skull_cell_at(p, 4) for p in found} == set(chart.SEASON_SITE_CELLS.values())


def test_a_frame_without_a_skull_reads_nothing():
    assert y_view_skulls(Frame(256, 224, bytes(256 * 224 * 3))) == []


@pytest.fixture(autouse=True)
def clear_registry():
    for chapter in range(1, 13):
        chart.set_boss_cell(chapter, None)
    yield
    for chapter in range(1, 13):
        chart.set_boss_cell(chapter, None)


def test_a_measured_cell_overrides_a_chart_boss_cell():
    assert chart.castles(9).get('ボス') is None
    assert chart.set_boss_cell(9, (500, 600)) == (500, 600)
    assert chart.castles(9)['ボス'] == (500, 600)
    assert chart.moving_boss_cell(9) == (500, 600)
    # An approximate chart cell is replaced by the measured one.
    assert chart.castles(7)['ボス'] == (488, 320)
    chart.set_boss_cell(7, (306, 141))
    assert chart.castles(7)['ボス'] == (306, 141)
    assert chart.castles(5)['ボス'] == (744, 288)          # untouched
    chart.set_boss_cell(9, None)
    chart.set_boss_cell(7, None)
    assert chart.castles(9).get('ボス') is None
    assert chart.castles(7)['ボス'] == (488, 320)


@pytest.mark.parametrize('cell', [(None, 5), (500, None), (-1, 5), (99999, 5), 'x'])
def test_an_invalid_cell_is_never_registered(cell):
    assert chart.set_boss_cell(9, cell) is None
    assert chart.castles(9).get('ボス') is None


def boss_order():
    return next(o for o in chart.all_orders(9) if o['target'] == 'ボス')


def test_the_boss_order_is_locked_without_a_measured_cell():
    mem = {'chapter': 9, 'tick': 10}
    assert orders._boss_target_unlocked(boss_order(), mem) is False
    chart.set_boss_cell(9, (500, 600))
    assert orders._boss_target_unlocked(boss_order(), mem) is True
    chart.set_boss_cell(9, None)
    assert orders._boss_target_unlocked(boss_order(), mem) is False


def test_two_equal_y_view_readings_record_the_cell():
    mem = {'chapter': 9, 'tick': 100, '_records': []}
    W._apply_skull_boss(mem, CH3)
    assert not mem.get('boss_cell')              # one reading is not enough
    W._apply_skull_boss(mem, CH3)
    assert mem['boss_cell']['cell'] == [306, 141]
    assert mem['boss_cell']['chapter'] == 9
    assert mem['boss_cell']['tick'] == 100
    assert [r['decision'] for r in mem['_records']] == ['boss_cell_measured']


def test_a_different_position_restarts_the_confirmation():
    mem = {'chapter': 9, 'tick': 100, '_records': []}
    W._apply_skull_boss(mem, CH3)
    mem['boss_cell_streak'] = {'cell': [1, 2], 'count': 1}
    W._apply_skull_boss(mem, CH3)
    assert not mem.get('boss_cell')
    assert mem['boss_cell_streak']['cell'] == [306, 141]


def test_a_frame_without_a_skull_drops_the_partial_confirmation():
    mem = {'chapter': 9, 'tick': 100, '_records': [],
           'boss_cell_streak': {'cell': [306, 141], 'count': 1}}
    W._apply_skull_boss(mem, Frame(256, 224, bytes(256 * 224 * 3)))
    assert not mem.get('boss_cell_streak')


def test_a_static_chapter_also_measures_its_boss_cell():
    mem = {'chapter': 7, 'tick': 100, '_records': []}
    W._apply_skull_boss(mem, CH3)
    W._apply_skull_boss(mem, CH3)
    assert mem['boss_cell']['chapter'] == 7
    assert mem['boss_cell']['cell'] == [306, 141]


def test_chapter_four_sites_never_use_the_single_skull_rule():
    mem = {'chapter': 4, 'tick': 100, '_records': []}
    W._apply_skull_boss(mem, CH4)
    W._apply_skull_boss(mem, CH4)
    assert not mem.get('boss_cell')


def test_decide_registers_a_fresh_cell_and_drops_a_stale_moving_one():
    from docich.hanjuku_bot import decide
    blank = Frame(256, 224, bytes(256 * 224 * 3))
    fresh = {'policy': {'chapter': 9, 'tick': 200,
                        'boss_cell': {'chapter': 9, 'cell': [500, 600], 'tick': 100}}}
    decide(blank, fresh)
    assert chart.moving_boss_cell(9) == (500, 600)
    stale = {'policy': {'chapter': 9, 'tick': 100 + chart.MOVING_BOSS_TICKS + 1,
                        'boss_cell': {'chapter': 9, 'cell': [500, 600], 'tick': 100}}}
    decide(blank, stale)
    assert chart.moving_boss_cell(9) is None


def test_a_static_chapter_keeps_an_old_measurement():
    from docich.hanjuku_bot import decide
    blank = Frame(256, 224, bytes(256 * 224 * 3))
    state = {'policy': {'chapter': 7, 'tick': 100 + chart.MOVING_BOSS_TICKS * 3,
                        'boss_cell': {'chapter': 7, 'cell': [488, 300], 'tick': 100}}}
    decide(blank, state)
    assert chart.moving_boss_cell(7) == (488, 300)


def test_a_measurement_from_another_chapter_is_ignored():
    from docich.hanjuku_bot import decide
    blank = Frame(256, 224, bytes(256 * 224 * 3))
    state = {'policy': {'chapter': 6, 'tick': 200,
                        'boss_cell': {'chapter': 5, 'cell': [500, 600], 'tick': 100}}}
    decide(blank, state)
    assert chart.moving_boss_cell(6) is None

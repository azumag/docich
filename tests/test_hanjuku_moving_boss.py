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
    chart.set_moving_boss_cell(9, None)
    yield
    chart.set_moving_boss_cell(9, None)


def test_the_dynamic_cell_is_only_for_the_moving_boss_chapter():
    assert chart.castles(9).get('ボス') is None
    assert chart.set_moving_boss_cell(9, (500, 600)) == (500, 600)
    assert chart.castles(9)['ボス'] == (500, 600)
    assert chart.moving_boss_cell(9) == (500, 600)
    assert chart.castles(5)['ボス'] == (744, 288)          # untouched
    assert chart.set_moving_boss_cell(5, (1, 2)) is None   # only chapter 9
    chart.set_moving_boss_cell(9, None)
    assert chart.castles(9).get('ボス') is None


@pytest.mark.parametrize('cell', [(None, 5), (500, None), (-1, 5), (99999, 5), 'x'])
def test_an_invalid_cell_is_never_registered(cell):
    assert chart.set_moving_boss_cell(9, cell) is None
    assert chart.castles(9).get('ボス') is None


def boss_order():
    return next(o for o in chart.all_orders(9) if o['target'] == 'ボス')


def test_the_boss_order_is_locked_without_a_measured_cell():
    mem = {'chapter': 9, 'tick': 10}
    assert orders._boss_target_unlocked(boss_order(), mem) is False
    chart.set_moving_boss_cell(9, (500, 600))
    assert orders._boss_target_unlocked(boss_order(), mem) is True
    chart.set_moving_boss_cell(9, None)
    assert orders._boss_target_unlocked(boss_order(), mem) is False


def test_two_equal_y_view_readings_record_the_cell():
    mem = {'chapter': 9, 'tick': 100, '_records': []}
    W._apply_moving_boss(mem, CH3)
    assert not mem.get('moving_boss')            # one reading is not enough
    W._apply_moving_boss(mem, CH3)
    assert mem['moving_boss']['cell'] == [306, 141]
    assert mem['moving_boss']['tick'] == 100
    assert [r['decision'] for r in mem['_records']] == ['moving_boss_seen']


def test_a_different_position_restarts_the_confirmation():
    mem = {'chapter': 9, 'tick': 100, '_records': []}
    W._apply_moving_boss(mem, CH3)
    mem['moving_boss_streak'] = {'cell': [1, 2], 'count': 1}
    W._apply_moving_boss(mem, CH3)
    assert not mem.get('moving_boss')
    assert mem['moving_boss_streak']['cell'] == [306, 141]


def test_a_frame_without_a_skull_drops_the_partial_confirmation():
    mem = {'chapter': 9, 'tick': 100, '_records': [],
           'moving_boss_streak': {'cell': [306, 141], 'count': 1}}
    W._apply_moving_boss(mem, Frame(256, 224, bytes(256 * 224 * 3)))
    assert not mem.get('moving_boss_streak')


def test_other_chapters_never_scan_for_the_moving_boss():
    mem = {'chapter': 5, 'tick': 100, '_records': []}
    W._apply_moving_boss(mem, CH3)
    assert not mem.get('moving_boss')


def test_decide_registers_a_fresh_cell_and_drops_a_stale_one():
    from docich.hanjuku_bot import decide
    blank = Frame(256, 224, bytes(256 * 224 * 3))
    fresh = {'policy': {'chapter': 9, 'tick': 200,
                        'moving_boss': {'cell': [500, 600], 'tick': 100}}}
    decide(blank, fresh)
    assert chart.moving_boss_cell(9) == (500, 600)
    stale = {'policy': {'chapter': 9, 'tick': 100 + chart.MOVING_BOSS_TICKS + 1,
                        'moving_boss': {'cell': [500, 600], 'tick': 100}}}
    decide(blank, stale)
    assert chart.moving_boss_cell(9) is None

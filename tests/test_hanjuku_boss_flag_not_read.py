"""The boss castle's flag is never read from the Y view (g1075, 2026-10-29).

Chapter 3 draws the boss castle as a skull with no flag. After v293/v295 put
its cell on the skull, world_map_owners logged ボス: own at 15:10:55, 15:10:58
and 15:11:01 with no boss battle (14:36:15 had read it enemy).

- g1075-ch3-y-view-face-over-boss-skull.png  15:10:55 native, the face of
  アポロン (camped beside the boss building) overlaps the skull; three of its
  pixels are the own-flag colour (230,56,90) inside the boss flag box
"""
from pathlib import Path

import pytest

from docich import hanjuku_chart as chart
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.readers import (WORLD_FLAG_OWN, WORLD_MAP_OFFSET, _near_colour,
                                             world_cursor, world_flags)
from docich.hanjuku.os_layer.screen import is_world_map
from docich.hanjuku.os_layer.world import _apply_world_flags

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests' / 'fixtures' / 'hanjuku'
FACE = FIXTURES / 'g1075-ch3-y-view-face-over-boss-skull.png'


def boss_box(chapter):
    wx, wy = chart.castles(chapter)[chart.boss_castle(chapter)]
    ox, oy = WORLD_MAP_OFFSET[chapter]
    mx, my = round(wx / 8 + ox), round(wy / 8 + oy)
    return [(x, y) for x in range(mx - 2, mx + 6) for y in range(my - 2, my + 5)]


def test_the_face_puts_own_flag_pixels_in_the_boss_box():
    frame = read_png(FACE).resized()
    assert is_world_map(frame)
    own = [p for p in boss_box(3) if _near_colour(frame.pixel(*p), WORLD_FLAG_OWN)]
    assert own == [(106, 66), (107, 66), (108, 66)]


def test_the_face_over_the_skull_does_not_read_the_boss_castle_as_own():
    frame = read_png(FACE).resized()
    cursor = world_cursor(frame)
    assert cursor == (151.0, 64.5)
    flags = world_flags(frame, 3, cursor)
    assert 'ボス' not in flags
    # Every other castle off the cursor still reads (all eight were ours then).
    assert flags == {name: 'own' for name in ('グリン', 'コラダイン', 'イーピヨル', 'ドンダム',
                                              'メガパタ', 'パタゴーヌ', 'アバロン', 'リグア')}


def test_the_survey_records_no_boss_owner():
    frame = read_png(FACE).resized()
    mem = {'chapter': 3, 'tick': 100}
    _apply_world_flags(mem, world_flags(frame, 3, world_cursor(frame)))
    assert 'ボス' not in mem['world_flags']
    assert 'ボス' not in mem.get('castle_ownership', {})
    assert 'ボス' not in mem['captured']
    record = next(r for r in mem['_records'] if r['decision'] == 'world_map_owners')
    assert 'ボス' not in record['observed_metric']


@pytest.mark.parametrize('chapter', [1, 2, 3])
def test_no_saved_y_view_reads_a_boss_flag_in_any_measured_chapter(chapter):
    boss = chart.boss_castle(chapter)
    pngs = sorted(ROOT.glob('tests/fixtures/**/*.png'))
    views = 0
    for path in pngs:
        try:
            frame = read_png(path).resized()
        except ValueError:
            continue
        if not is_world_map(frame):
            continue
        views += 1
        assert boss not in world_flags(frame, chapter), path.name
    assert views >= 8

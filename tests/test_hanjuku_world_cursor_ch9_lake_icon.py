"""g1163 2026-11-11: chapter 9's lake icon must not stand in for the Y cursor.

The Y view of chapter 9 has a blinking blue icon in a black-outlined lake at
(169, 116). The chapter-3 blue-dot ring test accepted it before the gold G test
ran, so while it blinked on the real G was never returned. The ハヤク target
jump bounced between left 933 ms / down 717 ms (aimed from the lake icon) and
right 550 ms / up 933 ms (aimed from the real G) until its 8-move limit, at
07:59 and again at 08:22.
"""
from pathlib import Path

import pytest

from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.readers import world_cursor

ROOT = Path(__file__).parent / 'fixtures' / 'hanjuku'


def test_the_real_g_wins_over_the_lake_icon():
    assert world_cursor(read_png(ROOT / 'g1163-ch9-y-view-g-at-west-edge-lake-icon.png')) == (65.5, 142.5)


def test_the_g_near_the_target_still_reads():
    assert world_cursor(read_png(ROOT / 'g1163-ch9-y-view-g-near-hayaku.png')) == (83.5, 125.5)


@pytest.mark.parametrize('name,expected', [
    ('g1075-world-map-cursor.png', (137.0, 128.0)),        # blue-dot ring only
    ('g1075-ch3-y-view-g-on-boss-aim.png', (101.5, 65.5)),
    ('g1075-world-map-ring-west-edge.png', (65.0, 81.5)),  # white ring only
    ('g1075-world-map-cursor-off.png', None),
])
def test_earlier_y_views_read_as_before(name, expected):
    assert world_cursor(read_png(ROOT / name)) == expected

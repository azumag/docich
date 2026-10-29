"""#2962: the Y ring at the sea's edge, and halved roof alignment.

Live frames from g1075-1d5f65e9 (2026-10-29 JST):
- g1075-world-map-ring-west-edge.png   12:19:57, ring west of リグア at (65, 81.5)
- g1075-world-map-ring-top-edge.png    ring at the sea's top edge (153, 50.5)
- g1075-world-map-ring-off.png         the blinking ring is off: no cursor
- g1075-target-marker-beside-rigua.png 12:25:30, target marker at (128, 118),
  リグア's roof target at (121, 125): offset (-7, +7)
"""
from pathlib import Path

from docich.hanjuku.os_layer.navigation import ALIGN_LIMIT, _align_on_roof
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.readers import world_cursor
from docich.hanjuku.os_layer.screen import parse

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'hanjuku'


def frame(name):
    return read_png(FIXTURES / name)


def test_a_ring_at_the_west_edge_of_the_sea_is_read():
    assert world_cursor(frame('g1075-world-map-ring-west-edge.png')) == (65.0, 81.5)


def test_a_ring_at_the_top_edge_of_the_sea_is_read():
    assert world_cursor(frame('g1075-world-map-ring-top-edge.png')) == (153.0, 50.5)


def test_a_view_with_the_ring_off_still_has_no_cursor():
    assert world_cursor(frame('g1075-world-map-ring-off.png')) is None


def test_existing_cursor_reads_are_unchanged():
    assert world_cursor(frame('chapter-2-boss-y-focus.png')) == (128.0, 72.5)
    assert world_cursor(frame('g749-cinnamon-recall-picker-29564.png')) == (127.0, 150.5)
    assert world_cursor(frame('g1075-world-map-sea-cursor.png')) == (128.5, 81.5)


def marker_screen():
    shot = frame('g1075-target-marker-beside-rigua.png')
    screen = parse(shot, phase='field')
    assert screen.kind == 'map_target' and screen.marker == (128, 118)
    return screen, shot


def test_alignment_holds_half_the_offset_so_a_fast_marker_does_not_overshoot():
    screen, shot = marker_screen()
    actions = _align_on_roof(screen, {}, shot, ('enemy',), 'I:b59ab6:3:target')
    assert [(a['buttons'], a['hold_ms']) for a in actions] == [(['left'], 67), (['down'], 67)]


def test_at_the_step_limit_a_marker_within_the_confirm_distance_is_on():
    screen, shot = marker_screen()
    mem = {'align_steps': {'k': ALIGN_LIMIT}}
    assert _align_on_roof(screen, mem, shot, ('enemy',), 'k') == 'on'


def test_no_roof_of_the_expected_owner_nearby_still_reopens_the_jump():
    screen, shot = marker_screen()
    mem = {'align_steps': {'k': ALIGN_LIMIT}}
    assert _align_on_roof(screen, mem, shot, ('own',), 'k') is None

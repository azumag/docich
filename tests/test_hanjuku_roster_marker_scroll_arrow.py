"""The roster's scroll arrow is not the selected general's map marker.

g1163 2026-11-11 00:26 (chapter 8): the monthly walk read 29 markers as
out_of_tolerance. The real triangle over あるまむーん had one gray edge pixel
blended with grass, (86,96,83) = chroma 13, one level past the neutral guard,
so the scan fell through to the identical white/gray 「more names」 arrow
under the name column at (192,161). Every general then cost a status panel,
about three minutes a month.
"""
from pathlib import Path

from docich.hanjuku.os_layer import readers
from docich.hanjuku_pixels import read_png

ROOT = Path(__file__).parent / 'fixtures/hanjuku'


def test_blended_marker_over_grass_wins_over_the_scroll_arrow():
    frame = read_png(ROOT / 'g1163-roster-scroll-arrow-and-blended-marker.png')
    assert readers.selection_marker(frame)['box'] == (29, 133, 35, 139)
    evidence = {}
    mapped = readers.marker_castle(frame, 8, evidence=evidence)
    assert evidence['reason'] == 'mapped'
    assert mapped['castle'] == 'あるまむーん'


def test_clean_marker_frame_still_maps_its_castle():
    frame = read_png(ROOT / 'g1163-roster-marker-shirakawa.png')
    assert readers.selection_marker(frame)['box'] == (77, 133, 83, 139)
    assert readers.marker_castle(frame, 8)['castle'] == 'しらかわ'


def test_white_pixels_stay_strict():
    assert not readers._marker_shade_matches((230, 245, 230), readers.GENERAL_MARKER_WHITE)
    assert readers._marker_shade_matches((86, 96, 83), readers.GENERAL_MARKER_GRAY)
    # A coloured flag/army shade still fails the gray class.
    assert not readers._marker_shade_matches((120, 80, 80), readers.GENERAL_MARKER_GRAY)


class _Canvas:
    def __init__(self, width=256, height=224):
        self.width, self.height = width, height
        self.px = {}

    def pixel(self, x, y):
        return self.px.get((x, y), (16, 72, 57))


def _stamp(canvas, x0, y0):
    shade = {readers.GENERAL_MARKER_WHITE: (255, 254, 255),
             readers.GENERAL_MARKER_GRAY: (90, 88, 90),
             readers.GENERAL_MARKER_BLACK: (32, 32, 32)}
    for dx, dy, colour in readers._GENERAL_MARKER_PATTERN:
        canvas.px[(x0 + dx, y0 + dy)] = shade[colour]


def test_map_match_below_the_arrow_row_still_wins():
    canvas = _Canvas()
    _stamp(canvas, 192, 100)   # an arrow-like sprite higher up but outside the map
    _stamp(canvas, 60, 140)    # the general's marker inside the map panel
    assert readers.selection_marker(canvas)['box'] == (60, 140, 66, 146)


def test_outside_match_is_kept_when_the_map_has_none():
    canvas = _Canvas()
    _stamp(canvas, 192, 161)
    assert readers.selection_marker(canvas)['box'] == (192, 161, 198, 167)

"""Chapter 9's sand map and pale island coasts are outdoor maps, not events.

g1190 2026-11-15 03:0x (real production frame): the chapter-9 desert field had
0.00 grass and 0.00 water under the old predicates, so the bot read the map as
'event' and fell through to the blind confirm fallback while the field policy
(map cursor, world-map survey, unsteerable-map nudge) never ran.
"""
from pathlib import Path

from docich.hanjuku.os_layer.phase import classify
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import parse

FIXTURES = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' / 'hanjuku'


def test_chapter_nine_desert_field_reads_as_the_map():
    frame = read_png(FIXTURES / 'g1190-ch9-desert-field.png')
    phase = classify(frame)
    assert phase == 'field'
    assert parse(frame, phase=phase).kind in {'map', 'map_target'}


def test_pale_island_map_reads_as_the_map():
    frame = read_png(FIXTURES / 'g1097-probe-dome-under-cursor.png')
    phase = classify(frame)
    assert phase == 'field'
    screen = parse(frame, phase=phase)
    assert screen.kind == 'map'
    assert screen.cursor == (132, 109)


def test_terrain_predicates_do_not_reclassify_other_screens():
    cases = {
        'g692-venus-artemis-unbalanced-panel.png': 'battle',
        'g478-merchant.png': 'concert',
        'g514-frame-077-retainer-offer.png': 'concert',
        'g1075-ch4-snow-field.png': 'event',
    }
    for name, expected in cases.items():
        assert classify(read_png(FIXTURES / name)) == expected, name

"""A snow field is not a battle: its paper-bright bottom has no panel edge.

g1075 2026-10-29 17:29-17:31: the chapter 4 map read phase 'battle', so no
map cursor was parsed and the field sat 'unknown' without a sortie.
"""
from pathlib import Path

from docich.hanjuku.os_layer.phase import classify
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import parse

FIXTURES = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' / 'hanjuku'


def test_chapter_four_snow_field_reads_as_the_map_with_its_cursor():
    frame = read_png(FIXTURES / 'g1075-ch4-snow-field.png')
    phase = classify(frame)
    assert phase != 'battle'
    screen = parse(frame, phase=phase)
    assert screen.kind == 'map'
    assert screen.cursor == (140, 120)


def test_battle_panel_keeps_the_battle_phase():
    assert classify(read_png(FIXTURES / 'g692-venus-artemis-unbalanced-panel.png')) == 'battle'


def test_chapter_three_winter_cave_ring_is_not_a_battle_either():
    assert classify(read_png(FIXTURES / 'g1075-ch3-dome-camp-cave-ring.png')) != 'battle'

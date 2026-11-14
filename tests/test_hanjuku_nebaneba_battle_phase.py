"""A right-side command list with both general panels stacked on the left.

g1190 chapter 9 2026-11-15: ``ねばねば!! こうげき/たいきゃく`` read as a
non-battle screen, the bot held, and the run ended with input_stalled.
"""
from pathlib import Path

from docich.hanjuku_pixels import read_png
from docich.hanjuku.os_layer.phase import classify
from docich.hanjuku.os_layer.screen import parse

ROOT = Path(__file__).resolve().parents[1]
FRAMES = ROOT / 'tests' / 'fixtures' / 'hanjuku'
NEBA_NEBA = FRAMES / 'g1190-ch9-nebaneba-battle-menu.png'
FIELD = FRAMES / 'g1190-ch9-desert-field.png'


def test_the_stacked_battle_panels_are_a_battle_not_an_event():
    frame = read_png(NEBA_NEBA)
    assert classify(frame) == 'battle'
    screen = parse(frame, phase=classify(frame))
    assert screen.kind == 'battle_menu'
    assert 'こうげき' in screen.text and 'たいきゃく' in screen.text


def test_the_chapter9_field_frame_stays_out_of_the_battle_branch():
    assert classify(read_png(FIELD)) != 'battle'

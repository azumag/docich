"""Cursor ambiguity must never become a guessed confirmation input."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich.hanjuku_pixels import Frame
from docich.hanjuku_screen import find_hand, Screen
from docich.hanjuku_policy import yes_no_step


def orange_boxes(*boxes):
    rgb = bytearray(256 * 224 * 3)
    for x0, y0, x1, y1 in boxes:
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                i = (y * 256 + x) * 3
                rgb[i:i + 3] = bytes((255, 174, 82))
    return Frame(256, 224, bytes(rgb))


def test_unique_hand_survives_separate_sprite_and_tiny_fragments():
    assert find_hand(orange_boxes((163, 177, 180, 190), (100, 85, 105, 88),
                                 (30, 50, 60, 80))) == (163, 177, 180, 190)


def test_two_hand_sized_objects_remain_ambiguous():
    assert find_hand(orange_boxes((163, 177, 180, 190), (30, 50, 47, 63))) is None


def test_missing_confirmation_hand_records_hold_without_pressing_a():
    mem = {}
    screen = Screen([], None, 'なにかかってかねーかい?うむッ!いかんッ!', kind='yes_no')
    assert yes_no_step(screen, mem) == []
    assert mem['_records'][-1]['decision'] == 'situation_held'
    assert mem['_records'][-1]['observed_metric']['hand_visible'] is False

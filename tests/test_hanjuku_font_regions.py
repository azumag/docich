"""Region-limited OCR must match full-frame masks; no gameplay timing changes."""
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich.hanjuku_font import dark, light, read_lines, row_masks
from docich.hanjuku_glyphs import GLYPHS, MARKS
from docich.hanjuku_pixels import Frame


def old_masks(frame, predicate):
    out = []
    for y in range(frame.height):
        value = 0
        for x in range(frame.width):
            value = value << 1 | bool(predicate(*frame.pixel(x, y)))
        out.append(value)
    return out


def frame_with_marks(y):
    rgb = bytearray(bytes((16, 72, 57)) * (256 * 224))
    code = {v: k for k, v in GLYPHS.items()}
    marks = {v: k for k, v in MARKS.items()}
    def tile(x, row, bits):
        for dy in range(8):
            for dx in range(8):
                if bits >> (63 - dy * 8 - dx) & 1:
                    i = ((row + dy) * 256 + x + dx) * 3
                    rgb[i:i + 3] = b'\xff\xff\xff'
    tile(176, y, code['は'])
    tile(176, y - 8, marks['゛'])
    tile(184, y, code['は'])
    tile(184, y - 8, marks['゜'])
    tile(200, y, 0x0123456789ABCDEF)  # Keep unknowns, never substitute glyphs.
    return Frame(256, 224, bytes(rgb))


@pytest.mark.parametrize('dy', range(8))
def test_marks_outside_rect_and_non_aligned_columns_match_full_masks(dy):
    y = 176 + dy
    frame = frame_with_marks(y)
    rect = (179, y, 215, 216)
    full = row_masks(frame, light)
    expected = read_lines(frame, rect=rect, masks=full)
    assert read_lines(frame, rect=rect) == expected
    assert 'ばぱ' in ''.join(line.text for line in expected)


@pytest.mark.parametrize('predicate', [light, dark, lambda r,g,b: 95 <= min(r,g,b) <= max(r,g,b) <= 120])
@pytest.mark.parametrize('rect', [(0,0,256,224), (0,160,256,200), (176,176,256,216),
                                  (13,7,99,43), (248,216,280,240), (0,0,7,7)])
def test_region_results_match_legacy_on_deterministic_noise(predicate, rect):
    rng = random.Random(1024)
    frame = Frame(256, 224, bytes(rng.randrange(256) for _ in range(256 * 224 * 3)))
    legacy = old_masks(frame, predicate)
    assert row_masks(frame, predicate) == legacy
    assert read_lines(frame, rect=rect, predicate=predicate) == read_lines(frame, rect=rect, masks=legacy)


def test_narrow_menu_samples_only_region_plus_kana_context():
    frame = frame_with_marks(176)
    calls = 0
    def counted(r, g, b):
        nonlocal calls
        calls += 1
        return light(r, g, b)
    read_lines(frame, predicate=counted, rect=(176,176,256,216))
    assert calls == 80 * 48
    assert calls < frame.width * frame.height / 10


def test_supplied_masks_do_not_invoke_predicate():
    frame = frame_with_marks(176)
    masks = row_masks(frame, light)
    def forbidden(*_):
        raise AssertionError('supplied masks must be reused')
    assert read_lines(frame, rect=(176,176,256,216), predicate=forbidden, masks=masks)

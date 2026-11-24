"""The C-speed pixel kernels must decide exactly what the per-pixel predicates decide.

``band_bits`` replaced Python loops over every pixel in ``row_masks``, ``find_hand``,
``_target_marker``, ``own_camps`` and the world-marker scan. Screen parsing depends on
every bit of those results, so each kernel is compared with a plain per-pixel reference
on a sample of saved game frames and on seeded random frames built around the measured
colours. The sample stays small on purpose: the reference is the slow scan the kernels
replaced, and this file must not hand that CPU time back to CI.
"""
from __future__ import annotations

from pathlib import Path
import random

import pytest

from docich.hanjuku.os_layer.font import dark, light, row_masks, white
from docich.hanjuku.os_layer.pixels import (
    Frame, band_bits, bit_points, near_bits, read_png, union_bits,
)
from docich.hanjuku.os_layer.readers import WORLD_COLOUR_TOL, WORLD_MARKER
from docich.hanjuku.os_layer.screen import (
    CAMP_BAND, CAMP_ORANGE, CAMP_YELLOW, DOME_DOOR, DOME_DOOR_TOLERANCE, HAND_COLORS,
    find_hand, _near,
)

FIXTURES = sorted((Path(__file__).parent / 'fixtures/hanjuku').rglob('*.png'))[::24]
PREDICATES = {'light': light, 'dark': dark, 'white': white}
RECTS = [None, (0, 0, 256, 224), (176, 176, 256, 216), (24, 135, 232, 176),
         (-8, -8, 40, 40), (250, 218, 400, 400), (100, 100, 100, 150), (300, 0, 400, 50)]


def _random_frame(seed: int, width: int = 256, height: int = 224) -> Frame:
    rng = random.Random(seed)
    palette = [rng.randrange(256) for _ in range(30)]
    colours = [*HAND_COLORS, (0, 64, 189), CAMP_YELLOW, CAMP_ORANGE, CAMP_BAND, DOME_DOOR,
               WORLD_MARKER, (255, 255, 255), (0, 0, 0)]
    data = bytearray()
    for _ in range(width * height):
        if rng.random() < 0.7:
            base = rng.choice(colours)
            data.extend(min(255, max(0, value + rng.randint(-16, 16))) for value in base)
        else:
            data.extend(rng.choice(palette) for _ in range(3))
    return Frame(width, height, bytes(data))


def _frames():
    for path in FIXTURES:
        yield path.name, read_png(path)
    yield 'random', _random_frame(1)
    yield 'small', _random_frame(99, 17, 9)


def _reference_masks(frame, predicate, rect):
    width = frame.width
    x0, y0, x1, y1 = rect or (0, 0, width, frame.height)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(width, x1), min(frame.height, y1)
    masks = [0] * frame.height
    for y in range(y0, y1):
        value = 0
        for x in range(x0, x1):
            value = (value << 1) | bool(predicate(*frame.pixel(x, y)))
        masks[y] = value << max(0, width - x1)
    return masks


@pytest.mark.parametrize('name,frame', list(_frames()), ids=lambda item: item if isinstance(item, str) else '')
def test_row_masks_match_the_per_pixel_predicate(name, frame):
    for predicate in PREDICATES.values():
        for rect in RECTS:
            assert row_masks(frame, predicate, rect=rect) == _reference_masks(frame, predicate, rect)


@pytest.mark.parametrize('name,frame', list(_frames()), ids=lambda item: item if isinstance(item, str) else '')
def test_near_bits_points_match_the_per_pixel_scan(name, frame):
    for colour, tolerance in ((WORLD_MARKER, WORLD_COLOUR_TOL), (DOME_DOOR, DOME_DOOR_TOLERANCE),
                              ((0, 64, 189), 14), (CAMP_YELLOW, 0)):
        expected = [(x, y) for y in range(frame.height) for x in range(frame.width)
                    if _near(frame.pixel(x, y), colour, tolerance)]
        assert bit_points(frame, near_bits(frame, colour, tolerance)) == expected


@pytest.mark.parametrize('name,frame', list(_frames()), ids=lambda item: item if isinstance(item, str) else '')
def test_union_of_bands_matches_the_hand_colour_scan(name, frame):
    expected = [(x, y) for y in range(frame.height) for x in range(frame.width)
                if frame.pixel(x, y)[0] >= 195
                and any(_near(frame.pixel(x, y), colour) for colour in HAND_COLORS)]
    bits = union_bits(*(band_bits(frame, (max(195, r - 10), r + 10), (g - 10, g + 10), (b - 10, b + 10))
                        for r, g, b in HAND_COLORS))
    assert bit_points(frame, bits) == expected


def test_a_frame_shorter_than_its_dimensions_still_fails_loudly():
    frame = Frame(4, 4, b'\0' * 12)
    with pytest.raises(IndexError):
        row_masks(frame, light)
    with pytest.raises(IndexError):
        find_hand(frame)


def test_an_unregistered_predicate_keeps_the_generic_path():
    frame = _random_frame(5, 32, 16)
    grey = lambda r, g, b: 95 <= min(r, g, b) and max(r, g, b) <= 120  # noqa: E731
    assert row_masks(frame, grey, rect=(4, 2, 30, 14)) == _reference_masks(frame, grey, (4, 2, 30, 14))
    assert row_masks(frame, grey) == _reference_masks(frame, grey, None)

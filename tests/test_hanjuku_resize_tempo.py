"""Byte-exact normalization, including two-cluster ties and non-native sizes."""
import random
from pathlib import Path

import pytest

from docich.hanjuku.os_layer.pixels import Frame, read_png


def reference_resize(frame, width, height):
    # Deliberately slow area-overlap specification, independent of the fast
    # path's geometry helper and two-sample algebra.
    out = bytearray()
    for y in range(height):
        for x in range(width):
            samples = []
            for sy in range(y * frame.height // height,
                            min(frame.height, ((y + 1) * frame.height + height - 1) // height)):
                wy = min((y + 1) * frame.height, (sy + 1) * height) - max(y * frame.height, sy * height)
                for sx in range(x * frame.width // width,
                                min(frame.width, ((x + 1) * frame.width + width - 1) // width)):
                    wx = min((x + 1) * frame.width, (sx + 1) * width) - max(x * frame.width, sx * width)
                    samples.append((frame.pixel(sx, sy), wx * wy))
            total = sum(w for rgb, w in samples)
            mean = sum(sum(rgb) * w for rgb, w in samples)
            high = [(rgb, w) for rgb, w in samples if sum(rgb) * total >= mean]
            low = [(rgb, w) for rgb, w in samples if sum(rgb) * total < mean]
            chosen = high if sum(w for rgb, w in high) >= sum(w for rgb, w in low) else low
            weight = sum(w for rgb, w in chosen)
            out.extend((sum(rgb[c] * w for rgb, w in chosen) + weight // 2) // weight for c in range(3))
    return bytes(out)


@pytest.mark.parametrize('src,dst', [((19, 8), (16, 8)), ((19, 8), (16, 7)),
    ((16, 8), (32, 8)), ((16, 8), (32, 16)), ((2, 2), (1, 1)),
    ((19, 17), (5, 4)), ((1, 9), (3, 4)), ((9, 1), (4, 3))])
def test_all_resize_paths_match_area_cluster_reference(src, dst):
    rng = random.Random(83)
    frame = Frame(*src, rng.randbytes(src[0] * src[1] * 3))
    assert frame.resized(*dst).rgb == reference_resize(frame, *dst)


@pytest.mark.parametrize('rgb,expected', [
    (bytes([0, 0, 0, 255, 255, 255]), bytes([255, 255, 255])),
    (bytes([255, 255, 255, 0, 0, 0]), bytes([255, 255, 255])),
    (bytes([255, 0, 0, 0, 255, 0]), bytes([128, 128, 0])),
])
def test_two_sample_equal_weight_brightness_and_chroma_ties(rgb, expected):
    assert Frame(2, 1, rgb).resized(1, 1).rgb == expected


@pytest.mark.parametrize('name', [
    'g1097-main-menu-intro.png', 'g1097-main-menu-status-help.png',
    'g698-resume-starter.png', 'g1075-akematsu-first-box.png',
])
def test_saved_native_captures_keep_every_normalized_byte(name):
    frame = read_png(Path(__file__).parent / 'fixtures/hanjuku' / name)
    assert frame.resized().rgb == reference_resize(frame, 256, 224)


def test_same_geometry_does_not_reuse_pixels_from_a_previous_observation():
    black = Frame(299, 224, bytes(299 * 224 * 3))
    white = Frame(299, 224, bytes([255]) * (299 * 224 * 3))
    assert black.resized().rgb == bytes(256 * 224 * 3)
    assert white.resized().rgb == bytes([255]) * (256 * 224 * 3)

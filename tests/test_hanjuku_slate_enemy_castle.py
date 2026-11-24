"""Chapter 6 draws the enemy castle with a slate roof (g1133 2026-11-04).

Live: every sortie to オケアナ read ``roofs: 0`` at the target marker, so
``arrival_unverified`` / ``target_not_under_marker`` cancelled it and the cursor
wandered. The roof is (74,88,139) lit and (49,64,90) shaded, not the blue of
chapters 1-5, and it is only the top of the sprite.

- g1133-ch6-enemy-castle-slate-roof.png: the bot's own 256 px frame (23:20:52,
  カバルッサ, cursor not yet centred).
- g1133-ch6-oceana-target-marker.png: a stream capture (299 px) resampled to
  256 px, the cursor G on オケアナ (23:40:20).
"""
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich.hanjuku.flows.map import _target_roof_under_marker
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.readers import _cursor
from docich.hanjuku.os_layer.screen import (SLATE_LIT, SLATE_SHADE, castle_roofs, parse)

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'hanjuku'
BOT_FRAME = FIXTURES / 'g1133-ch6-enemy-castle-slate-roof.png'
OCEANA = FIXTURES / 'g1133-ch6-oceana-target-marker.png'


def test_the_slate_castle_is_one_enemy_roof():
    roofs = castle_roofs(read_png(BOT_FRAME))
    assert [r['kind'] for r in roofs] == ['enemy']
    x0, y0, x1, y1 = roofs[0]['box']
    assert (x0, y0, x1, y1) == (116, 103, 139, 121)


def test_the_tower_and_the_body_merge_into_one_roof_with_the_cursor_cell_as_target():
    frame = read_png(OCEANA)
    roofs = castle_roofs(frame)
    assert len(roofs) == 1 and roofs[0]['kind'] == 'enemy'
    assert roofs[0]['box'] == (132, 103, 151, 121)
    # The marker is the cursor cell of オケアナ: the roof target is that cell.
    screen = parse(frame)
    assert screen.kind == 'map_target'
    assert roofs[0]['target'] == (141, 122)


def _screen_with_marker(frame, marker):
    """The live bot read the marker at (141, 122) on this view (decision log
    23:38:39 ``screen_cursor``); a resampled stream capture is not the bot's
    frame, so the marker it read is pinned here instead of re-detected."""
    screen = parse(frame)
    screen.marker = marker
    return screen


def test_the_marker_on_the_slate_castle_confirms_an_attack_target():
    frame = read_png(OCEANA)
    mem = {'chapter': 6, 'captured': [], 'tick': 1}
    order = {'target': 'オケアナ', 'step': 'A:test:M1'}
    for marker in ((141, 122), (141, 127), (137, 118)):     # the live reads varied 122-127
        assert _target_roof_under_marker(_screen_with_marker(frame, marker), mem, frame, order) is True


def test_a_marker_off_the_slate_castle_or_an_own_order_is_not_confirmed():
    frame = read_png(OCEANA)
    order = {'target': 'オケアナ', 'step': 'A:test:M1'}
    mem = {'chapter': 6, 'captured': [], 'tick': 1}
    assert _target_roof_under_marker(_screen_with_marker(frame, (141, 150)), mem, frame, order) is False
    assert _target_roof_under_marker(_screen_with_marker(frame, (60, 122)), mem, frame, order) is False
    # An own-castle order never accepts an enemy roof.
    mem['captured'] = ['オケアナ']
    assert _target_roof_under_marker(_screen_with_marker(frame, (141, 122)), mem, frame, order) is False


def _flat_frame(tmp_path, colours):
    """A 256x224 frame of grass with one 12x12 block per colour."""
    image = Image.new('RGB', (256, 224), (8, 113, 16))
    for k, colour in enumerate(colours):
        for x in range(40 + 30 * k, 52 + 30 * k):
            for y in range(60, 72):
                image.putpixel((x, y), colour)
    path = tmp_path / 'flat.png'
    image.save(path)
    return read_png(path)


@pytest.mark.parametrize('colours', [[SLATE_LIT], [SLATE_SHADE], [SLATE_LIT, SLATE_SHADE]])
def test_one_slate_tone_alone_is_not_a_roof(tmp_path, colours):
    # Separate 12x12 blocks of a single tone are rock/shadow, never a roof.
    assert castle_roofs(_flat_frame(tmp_path, colours)) == []


def test_both_slate_tones_in_one_piece_make_a_roof(tmp_path):
    image = Image.new('RGB', (256, 224), (8, 113, 16))
    for x in range(100, 116):
        for y in range(80, 96):
            image.putpixel((x, y), SLATE_LIT if (x + y) % 3 else SLATE_SHADE)
    path = tmp_path / 'both.png'
    image.save(path)
    roofs = castle_roofs(read_png(path))
    assert [r['kind'] for r in roofs] == ['enemy']

"""v346: our camp's flag in its thin phase is still a flag.

g1075 2026-10-30, chapter 4: the camp beside the frozen north island
stayed on screen while the recall never re-armed (owner: 画面にキャンプが
見えてるのに帰還が発動してない). The tent's flag was drawn one red pixel
per row (16:20 frame: y130 x149, y131 x148), and ``own_camps`` required
two flagish pixels in a single row, so the tent was rejected in nine of
the ten saved map frames that showed it. Two adjacent flagish pixels
anywhere in the flag window now count; a lone noise pixel still does not.
- g1075-ch4-camp-thin-flag.png   16:20:04, the thin flag above the tent
"""
from pathlib import Path

from docich.hanjuku.os_layer.pixels import Frame, read_png
from docich.hanjuku.os_layer.screen import own_camps

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'hanjuku'
THIN_FLAG = 'g1075-ch4-camp-thin-flag.png'

GREEN = (16, 72, 57)
YELLOW = (238, 198, 65)
ORANGE = (238, 113, 57)
BAND = (131, 198, 222)
RED = (255, 0, 0)


def _tent_frame(flag):
    """A map frame holding the measured tent body; ``flag`` paints pixels."""
    rgb = bytearray(bytes(GREEN) * (256 * 224))

    def put(x, y, color):
        i = (y * 256 + x) * 3
        rgb[i:i + 3] = bytes(color)

    for yy in range(72, 81):
        for xx in range(30, 39):
            put(xx, yy, YELLOW if yy < 76 else ORANGE)
    for yy in (77, 78):
        for xx in range(31, 39):
            put(xx, yy, BAND)
    for x, y in flag:
        put(x, y, RED)
    return Frame(256, 224, bytes(rgb))


def test_the_live_thin_flag_tent_is_our_camp():
    assert own_camps(read_png(FIXTURES / THIN_FLAG)) == [
        {'target': (141, 128), 'clipped': False}]


def test_a_one_pixel_per_row_flag_is_found():
    camps = own_camps(_tent_frame([(35, 68), (34, 69)]))
    assert camps == [{'target': (27, 66), 'clipped': False}]


def test_a_lone_red_pixel_still_is_not_a_flag():
    assert own_camps(_tent_frame([(35, 68)])) == []
    # Scattered red pixels are noise, not our flag.
    assert own_camps(_tent_frame([(30, 68), (35, 70)])) == []

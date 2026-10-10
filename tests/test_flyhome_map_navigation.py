from docich.flyhome.map_navigation import DIGITS, level_number
from docich.flyhome.image import Image

def map_image(level, color=(255, 255, 255)):
    im = Image(320, 180, bytes([160, 90, 40]) * (320 * 180))
    # ImageのRGB配列へ、実機で検証した字形を描く。
    data = bytearray(im.data)
    for x, value in ((27, level // 10), (32, level % 10)):
        for i, bit in enumerate(DIGITS[str(value)]):
            if bit == '1':
                offset = ((14 + i // 4) * 320 + x + i % 4) * 3
                data[offset:offset+3] = bytes(color)
    return Image(320, 180, bytes(data))

def test_map_number_reads_every_available_level_and_selection_flash():
    for lv in range(1, 51):
        assert level_number(map_image(lv)) == lv
        assert level_number(map_image(lv, (255, 128, 0))) == lv

def test_unknown_or_invalid_map_number_is_not_selected():
    assert level_number(Image(320, 180, bytes(320 * 180 * 3))) is None
    assert level_number(map_image(0)) is None
    assert level_number(map_image(99)) is None



def test_sequential_navigation_waits_for_moving_placard_and_advances_once():
    from docich.flyhome.map_navigation import select_sequential_level
    blank = Image(320, 180, bytes(320 * 180 * 3))
    frames = iter([blank, blank, map_image(15), map_image(15), blank, blank, map_image(16), map_image(16)])
    taps = []
    select_sequential_level(lambda: next(frames), taps.append, 16)
    assert taps == ['right']


def test_sequential_navigation_never_searches_past_wrong_level():
    import pytest
    from docich.flyhome.map_navigation import select_sequential_level
    taps = []
    with pytest.raises(RuntimeError, match='no skip permitted'):
        select_sequential_level(lambda: map_image(41), taps.append, 16)
    assert taps == []


def test_sequential_navigation_restarts_same_level_without_arrow():
    from docich.flyhome.map_navigation import select_sequential_level
    taps = []
    select_sequential_level(lambda: map_image(15), taps.append, 15)
    assert taps == []

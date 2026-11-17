"""A map frame always seeds the continuity record (v435).

g1133 2026-11-05: `cursor` stayed [392,144] with `nav_last` null for hours, so no
frame could integrate and every order was stuck at its source - the boss sortie
never left オケアナ. `nav_last` was only written by `nav_step` after it computed
a direction, so a single early return left it None for good.
"""
from docich.hanjuku.os_layer.screen import Frame, Screen
from docich.hanjuku.os_layer import world as W

FRAME = Frame(256, 224, b'\x00' * (256 * 224 * 3))


def screen(cursor):
    return Screen([], None, '', kind='map', cursor=cursor)


def test_a_frame_without_continuity_seeds_it():
    mem = {'chapter': 6, 'tick': 20, 'cursor': [100, 100]}
    W.update_world(screen((50, 50)), mem, FRAME)
    assert isinstance(mem['nav_last'], dict)
    assert mem['nav_last']['screen'] == [50, 50]
    assert mem['nav_last']['tick'] == 20
    assert mem['nav_last']['kind'] == 'map'


def test_the_seeded_record_lets_the_next_frame_integrate():
    mem = {'chapter': 6, 'tick': 20, 'cursor': [100, 100]}
    W.update_world(screen((50, 50)), mem, FRAME)
    mem['tick'] = 21
    world = W.update_world(screen((54, 50)), mem, FRAME)
    assert world == [104, 100]


def test_a_short_gap_between_frames_still_integrates():
    mem = {'chapter': 6, 'tick': 20, 'cursor': [100, 100],
           'nav_last': {'tick': 20 - W.NAV_CONTINUITY_TICKS, 'kind': 'map', 'screen': [50, 50]}}
    world = W.update_world(screen((54, 50)), mem, FRAME)
    assert world == [104, 100]


def test_a_long_gap_does_not_integrate():
    mem = {'chapter': 6, 'tick': 200, 'cursor': [100, 100],
           'nav_last': {'tick': 200 - W.NAV_CONTINUITY_TICKS - 1, 'kind': 'map', 'screen': [50, 50]}}
    world = W.update_world(screen((54, 50)), mem, FRAME)
    assert world != [104, 100]


def test_the_jump_hold_slows_down_near_the_goal():
    """Owner 2026-11-18: the cursor reached the castle, then overshot past it.

    The old hold (two frames per view pixel) carried the cursor beyond the goal
    and the next read steered it back the other way, so the sortie never
    confirmed. Close range now uses about one frame per pixel.
    """
    from docich.hanjuku.os_layer.navigation import _jump_hold_frames
    assert _jump_hold_frames(2) < 2 * 2
    assert _jump_hold_frames(8) == 8
    assert _jump_hold_frames(32) <= 20
    assert _jump_hold_frames(60) == 56


def _frame_with_blue(box, fill=0):
    """A black frame with ``box`` filled blue."""
    from docich.hanjuku.os_layer.pixels import Frame
    rgb = bytearray(256 * 224 * 3)
    x0, y0, x1, y1 = box
    for x in range(x0, x1):
        for y in range(y0, y1):
            i = (y * 256 + x) * 3
            rgb[i:i + 3] = bytes((0, 40, 220))
    return Frame(256, 224, bytes(rgb))


def test_the_blue_g_cursor_is_read_in_chapter_11():
    """g1190 2026-11-18 03:13: the Y view showed the blue G at (85,95).

    world_cursor returned None there (the blue-G test was chapter-9 only), so
    every クラボニア jump aborted as unreadable.
    """
    from docich.hanjuku.os_layer.readers import world_cursor
    frame = _frame_with_blue((80, 88, 92, 100))
    got = world_cursor(frame, 11)
    assert got is not None
    assert abs(got[0] - 85.5) <= 1 and abs(got[1] - 93.5) <= 1, got


def test_a_small_blue_lake_is_not_a_cursor():
    from docich.hanjuku.os_layer.readers import world_cursor
    # A 2x2 blue dot is below both the G box's density floor and the ring
    # detector's 8-pixel minimum.
    frame = _frame_with_blue((169, 114, 171, 116))
    assert world_cursor(frame, 11) is None

"""A short gap between map frames still integrates the position (v432).

g1133 2026-11-05: the chapter throws battles, so map frames are sparse (two in
8.4 minutes). Requiring the very next tick froze the estimate for hours; a gap
up to NAV_CONTINUITY_TICKS now integrates, a longer one does not.
"""
from docich.hanjuku.os_layer.screen import Frame, Screen
from docich.hanjuku.os_layer import world as W

FRAME = Frame(256, 224, b'\x00' * (256 * 224 * 3))


def screen(cursor):
    return Screen([], None, '', kind='map', cursor=cursor)


def memory(tick, last_tick):
    return {'chapter': 6, 'tick': tick, 'cursor': [100, 100],
            'nav_last': {'tick': last_tick, 'kind': 'map', 'screen': [50, 50]}}


def test_a_short_gap_integrates_the_measured_motion():
    mem = memory(20, 20 - W.NAV_CONTINUITY_TICKS)
    world = W.update_world(screen((54, 50)), mem, FRAME)
    assert world == [104, 100]


def test_a_long_gap_does_not_integrate():
    mem = memory(200, 200 - W.NAV_CONTINUITY_TICKS - 1)
    world = W.update_world(screen((54, 50)), mem, FRAME)
    assert world != [104, 100]

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

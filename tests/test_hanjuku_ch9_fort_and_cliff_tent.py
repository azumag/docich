"""Chapter 9 camps beside cliffs and merged into a 砦 (g1190 2026-11-16).

Both frames are the live local run (libretro core, RGB565 expanded to 8 bit):
タオ's view with a tent tucked under the end of a cliff wall and, further
left, two generals merged into a 砦 dome. The dome was never detected (its
door/flag colours sit 3-7 per channel off the exact production values), and
the tent's cliff was not seen in the winter palette (cliff (40, 48, 32)),
so the recall marched it straight into the wall every month.
"""
from pathlib import Path

from docich.hanjuku.flows import recall
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import own_camps

FIX = Path(__file__).parent / 'fixtures' / 'hanjuku'
GRASS = FIX / 'g1190-ch9-tao-fort-and-cliff-tent-565.png'
WINTER = FIX / 'g1190-ch9-tao-cliff-tent-winter-565.png'
TENT = (61, 146)


def test_the_565_fort_dome_is_detected_beside_the_tent():
    camps = own_camps(read_png(GRASS))
    domes = [c for c in camps if c.get('kind') == 'dome']
    assert domes, camps
    x, y = domes[0]['target']
    assert 5 <= x <= 20 and 145 <= y <= 160, domes
    assert any(tuple(c['target']) == TENT for c in camps)


def test_the_cliff_tent_has_an_open_side_in_both_palettes():
    for path in (GRASS, WINTER):
        side = recall._cliff_open_side(read_png(path), list(TENT))
        # The wall runs up-right from the tent and along its left; below is
        # the lake. The only open perpendicular step is to the right.
        assert side == 'right', (path.name, side)


def _visible_state(origin=(201, 554)):
    return {'stage': 'dest', 'steps': 0, 'target': list(TENT),
            'origin': list(origin), 'move_visible': {'castle': 'タオ', 'world': [280, 528]}}


def test_a_cliff_bound_camp_steps_off_the_wall_before_the_visible_move(monkeypatch):
    """Owner 2026-11-16: 崖際のキャンプは、いったん崖から垂直方向に離れる必要がある."""
    from docich.hanjuku.os_layer import world, navigation
    from docich.hanjuku_screen import Screen
    from docich.hanjuku.state.regions import flow as _flow
    frame = read_png(WINTER)
    mem = {'chapter': 9, 'tick': 1000, '_records': []}
    _flow(mem)['recall'] = _visible_state()
    here = {'cell': [201, 554]}
    monkeypatch.setattr(world, 'observed_cursor_cell', lambda *a, **k: here['cell'])
    monkeypatch.setattr(navigation, 'nav_step', lambda s, m, f, cell: 'right')
    screen = Screen([], None, '', kind='map', cursor=TENT)
    state = _flow(mem)['recall']
    assert recall._visible_move_dest_step(screen, mem, frame, state) == [recall.pad('right')]
    assert state['escape_world'] == [201 + 24, 554]
    assert any(r['decision'] == 'camp_cliff_escape' for r in mem['_records'])
    here['cell'] = [225, 554]
    actions = recall._visible_move_dest_step(screen, mem, frame, state)
    assert actions == [recall.pad('a')]
    assert state.get('escape_move') is True and state['goal'] is None
    # The same camp area is not escaped again right away: the next recall
    # goes straight to the castle.
    _flow(mem)['recall'] = _visible_state(origin=(225, 554))
    state = _flow(mem)['recall']
    here['cell'] = [225, 554]
    recall._visible_move_dest_step(screen, mem, frame, state)
    assert 'escape_world' not in state

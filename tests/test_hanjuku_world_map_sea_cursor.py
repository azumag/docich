"""#2958: chapter 3's Y view with the cursor over the sea, and blind A during a jump.

Live frames from g1075-1d5f65e9 (2026-10-29 JST):
- g1075-world-map-land-cursor.png  12:11:36, G on グリン, 1519 sea samples
- g1075-world-map-sea-cursor.png   12:11:41, G over the sea, 1479 sea samples
  (read 'unknown' before v282; the scene-confirm A decided the jump there)
- g1075-world-map-fade.png         12:11:40, the view fading in after Y
"""
from pathlib import Path

import pytest

from docich.hanjuku.orchestrator import Y_JUMP_UNKNOWN_HOLD_MAX, decide, pad
from docich.hanjuku.os_layer.pixels import Frame, read_png
from docich.hanjuku.os_layer.readers import world_cursor
from docich.hanjuku.os_layer.screen import (WORLD_BORDER, WORLD_SEA, is_world_map,
                                            parse)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests' / 'fixtures' / 'hanjuku'
LAND = FIXTURES / 'g1075-world-map-land-cursor.png'
SEA = FIXTURES / 'g1075-world-map-sea-cursor.png'
FADE = FIXTURES / 'g1075-world-map-fade.png'

# Every saved Y view in the repository (chapters 1-3). No other PNG is one.
WORLD_VIEWS = {
    'g1075-world-map-land-cursor.png', 'g1075-world-map-sea-cursor.png',
    'g1075-world-map-ring-west-edge.png', 'g1075-world-map-ring-top-edge.png',
    'g1075-world-map-ring-off.png',
    'chapter-2-boss-y-focus.png', 'g749-cinnamon-recall-picker-29564.png',
    'hanjuku_recall_picker_20260929.png', 'g464-frame-022.png',
    'g1075-ch3-y-view-face-over-boss-skull.png',
}


def jump_state(goal='リグア'):
    # Y was pressed on the target map; the next frame is the fade or the view.
    return {'screen_kind': 'map_target',
            'policy': {'chapter': 3, 'tick': 100,
                       'y_jump': {'goal': goal, 'mode': 'target', 'step': 'I:b59ab6:2',
                                  'moves': 0, 'wait': 0, 'tick': 99}}}


def decisions(state):
    return [r.get('decision') for r in state.get('_records', [])]


def test_the_view_with_the_cursor_over_the_sea_is_the_world_map():
    frame = read_png(SEA)
    assert parse(frame).kind == 'world_map'
    assert world_cursor(frame) == (128.5, 81.5)


def test_the_view_with_the_cursor_on_land_is_still_the_world_map():
    frame = read_png(LAND)
    assert parse(frame).kind == 'world_map'
    assert world_cursor(frame) == (170.5, 87.5)


def test_the_fade_into_the_view_is_not_read_as_the_world_map():
    assert parse(read_png(FADE)).kind == 'unknown'


def test_no_other_saved_frame_is_read_as_the_world_map():
    pngs = sorted({*ROOT.glob('tests/fixtures/**/*.png'),
                   *ROOT.glob('tests/*.png'),
                   *ROOT.glob('ops/vm_actions/tests/fixtures/**/*.png')})
    assert len(pngs) > 100
    found = set()
    for path in pngs:
        try:
            frame = read_png(path)
        except ValueError:
            continue
        if is_world_map(frame.resized()):
            found.add(path.name)
    assert found == WORLD_VIEWS


def test_border_and_sea_alone_below_the_old_bound_are_not_enough():
    """Without the backdrop beside the frame, the old 1500 bound still applies."""
    pixels = bytearray(bytes((16, 72, 57)) * (256 * 224))

    def put(x, y, rgb):
        i = (y * 256 + x) * 3
        pixels[i:i + 3] = bytes(rgb)

    put(128, 10, WORLD_BORDER)
    samples = [(x, y) for x in range(60, 200, 2) for y in range(60, 180, 2)]
    for x, y in samples[:1200]:
        put(x, y, WORLD_SEA)
    assert not is_world_map(Frame(256, 224, bytes(pixels)))


def test_an_open_jump_on_the_sea_cursor_view_steers_instead_of_confirming():
    actions, state = decide(read_png(SEA), jump_state())
    assert pad('a') not in actions
    assert actions and actions[0]['buttons'] == ['left']
    assert state['policy']['y_jump']['moves'] == 1
    assert 'y_jump_failed' not in decisions(state)


def test_an_open_jump_holds_the_default_a_on_an_unreadable_frame_boundedly():
    frame = read_png(FADE)
    state = jump_state()
    for held in range(1, Y_JUMP_UNKNOWN_HOLD_MAX + 1):
        actions, state = decide(frame, state)
        assert pad('a') not in actions
        assert decisions(state) == ['y_jump_unknown_hold']
        assert state['policy']['y_jump']['unknown_holds'] == held
    actions, state = decide(frame, state)
    assert actions == [pad('a')]
    assert decisions(state) == ['y_jump_failed']
    assert 'y_jump' not in state['policy']
    assert state['policy']['y_jump_last']['why'] == 'view_unreadable'


def test_without_an_open_jump_the_unreadable_frame_keeps_its_default():
    actions, state = decide(read_png(FADE), {'policy': {'chapter': 3, 'tick': 100}})
    assert actions == [pad('a')]
    assert 'y_jump_unknown_hold' not in decisions(state)


@pytest.mark.parametrize('kind_frame', [LAND, SEA])
def test_the_hold_never_applies_to_a_readable_world_map(kind_frame):
    _, state = decide(read_png(kind_frame), jump_state())
    assert 'y_jump_unknown_hold' not in decisions(state)


# #2971: g1075 13:12:29-13:13:08, an arrival battle opened mid-jump and holds
# 2-4 landed on its draws. Only a run that starts at the map or the view holds.
@pytest.mark.parametrize('previous', ['attack_started', 'battle', 'battle_menu', 'text'])
def test_an_unreadable_frame_after_an_interrupting_screen_keeps_its_default(previous):
    state = jump_state()
    state['screen_kind'] = previous
    actions, state = decide(read_png(FADE), state)
    assert actions == [pad('a')]
    assert 'y_jump_unknown_hold' not in decisions(state)
    assert 'unknown_holds' not in state['policy']['y_jump']


def test_an_unreadable_run_that_began_after_a_battle_is_not_held():
    state = jump_state()
    state['screen_kind'] = 'unknown'          # previous unknown was not held
    actions, state = decide(read_png(FADE), state)
    assert actions == [pad('a')]
    assert 'y_jump_unknown_hold' not in decisions(state)


def test_a_battle_between_two_view_runs_does_not_carry_the_episode():
    frame = read_png(FADE)
    _, state = decide(frame, jump_state())
    assert decisions(state) == ['y_jump_unknown_hold']
    state['screen_kind'] = 'battle'
    state['policy']['tick'] += 1               # one battle observation in between
    actions, state = decide(frame, state)
    assert actions == [pad('a')]
    assert 'y_jump_unknown_hold' not in decisions(state)
    assert state['policy']['y_jump']['unknown_holds'] == 1

"""Use the existing map-cursor speed without changing the fast target marker."""
from types import SimpleNamespace

import pytest

from docich.hanjuku.os_layer import navigation
from docich.hanjuku.os_layer.commands import pad
from docich.hanjuku.os_layer.screen import Screen

FRAME = SimpleNamespace(width=256, height=224)
GOAL = (100, 100)


@pytest.fixture
def roof(monkeypatch):
    monkeypatch.setattr(navigation, 'castle_roofs', lambda _frame: [
        {'kind': 'own', 'target': GOAL, 'clipped': False}])


def view(position, *, marker=False, kind=None):
    return Screen([], None, '', kind=kind or ('map_target' if marker else 'map'),
                  cursor=None if marker else position, marker=position if marker else None)


@pytest.mark.parametrize('offset,expected', [
    ((14, 0), [pad('right', 14)]),
    ((-14, 0), [pad('left', 14)]),
    ((0, 9), [pad('down', 9)]),
    ((0, -9), [pad('up', 9)]),
    ((9, -7), [pad('right', 9), pad('up', 7)]),
    ((28, 28), [pad('right', 28), pad('down', 28)]),
])
def test_normal_map_cursor_uses_one_frame_per_pixel(roof, offset, expected):
    position = (GOAL[0] - offset[0], GOAL[1] - offset[1])
    actions = navigation._align_on_roof(view(position), {}, FRAME, ('own',), 'k')
    assert actions == expected
    assert all(action['hold_ms'] <= pad('right', navigation.MAX_HOLD_FRAMES)['hold_ms']
               for action in actions)


@pytest.mark.parametrize('offset', [(14, 0), (-14, 0), (0, 9), (0, -9), (9, -7), (28, 28)])
def test_target_marker_keeps_half_offset_holds(roof, offset):
    position = (GOAL[0] - offset[0], GOAL[1] - offset[1])
    expected = []
    for d, neg, pos in ((offset[0], 'left', 'right'), (offset[1], 'up', 'down')):
        if abs(d) > navigation.ALIGN_TOL:
            expected.append(pad(pos if d > 0 else neg, (abs(d) + 1) // 2))
    assert navigation._align_on_roof(view(position, marker=True), {}, FRAME, ('own',), 'k') == expected


@pytest.mark.parametrize('kind', ['unknown', 'text', 'map_target'])
def test_a_cursor_on_another_screen_keeps_the_conservative_hold(roof, kind):
    assert navigation._align_on_roof(view((86, 100), kind=kind), {}, FRAME, ('own',), 'k') == [pad('right', 7)]


def test_marker_takes_priority_even_when_a_normal_cursor_is_also_present(roof):
    screen = view((86, 100))
    screen.marker = (90, 100)
    assert navigation._align_on_roof(screen, {}, FRAME, ('own',), 'k') == [pad('right', 5)]


@pytest.mark.parametrize('offset', [(14, 0), (0, -14), (22, -22)])
def test_normal_cursor_reaches_the_strict_tolerance_in_one_existing_model_step(roof, offset):
    # This is the same 1px/frame model nav_step uses, not a running emulator.
    position = [GOAL[0] - offset[0], GOAL[1] - offset[1]]
    mem = {}
    actions = navigation._align_on_roof(view(tuple(position)), mem, FRAME, ('own',), 'k')
    assert isinstance(actions, list)
    for action in actions:
        frames = round(action['hold_ms'] * 60 / 1000)
        axis, sign = {'left': (0, -1), 'right': (0, 1),
                      'up': (1, -1), 'down': (1, 1)}[action['buttons'][0]]
        position[axis] += sign * frames
    assert tuple(position) == GOAL
    assert navigation._align_on_roof(view(tuple(position)), mem, FRAME, ('own',), 'k') == 'on'
    for key in ('align_steps', 'align_offset', 'align_stall'):
        assert 'k' not in mem.get(key, {})


def test_arrival_still_requires_a_new_observation(roof):
    mem = {}
    first = view((86, 100))
    assert navigation._align_on_roof(first, mem, FRAME, ('own',), 'k') == [pad('right', 14)]
    # Planning the longer hold is not arrival: the unchanged next reading
    # still produces a correction, with the same finite stall/step budgets.
    assert navigation._align_on_roof(first, mem, FRAME, ('own',), 'k') == [pad('right', 14)]
    assert navigation._align_on_roof(first, mem, FRAME, ('own',), 'k') is None


def test_tolerance_owner_radius_and_missing_cursor_stay_unchanged(roof):
    assert navigation.ALIGN_TOL == 3
    assert navigation._align_on_roof(view((97, 100)), {}, FRAME, ('own',), 'k') == 'on'
    assert navigation._align_on_roof(view((96, 100)), {}, FRAME, ('own',), 'k') == [pad('right', 4)]
    assert navigation._align_on_roof(view((71, 100)), {}, FRAME, ('own',), 'k') is None
    assert navigation._align_on_roof(view((86, 100)), {}, FRAME, ('enemy',), 'k') is None
    assert navigation._align_on_roof(view(None), {}, FRAME, ('own',), 'k') is None

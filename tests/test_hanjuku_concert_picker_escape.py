"""Regression contracts for the 2026-10-10 concert B loop (#1996).

The concert's optional music picker is closed with B. A stale gold-cursor
match on a screen B cannot change kept the picker test true and sent B 128
times until ``screen_stalled`` ended the corner. The policy must notice the
unchanged repeats and press A itself instead of waiting for the watchdog.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich.hanjuku_bot import classify, decide
from docich.hanjuku_pixels import Frame


def concert_picker_frame(*, gold_row: int = 184) -> Frame:
    rgb = bytearray(Frame(256, 224, bytes((160, 110, 60)) * (256 * 224)).rgb)
    for y in range(150, 208):
        for x in range(18, 236):
            i = (y * 256 + x) * 3
            rgb[i:i + 3] = bytes((16, 72, 57))
    for y in range(128):
        for x in (*range(32), *range(224, 256)):
            i = (y * 256 + x) * 3
            rgb[i:i + 3] = bytes((200, 0, 0))
    for x in range(200, 240):
        i = (gold_row * 256 + x) * 3
        rgb[i:i + 3] = bytes((197, 141, 74))
    return Frame(256, 224, bytes(rgb))


def buttons(actions):
    return [button for action in actions for button in action['buttons']]


def test_unchanged_picker_screen_escapes_b_loop_with_a():
    frame = concert_picker_frame()
    assert classify(frame) == 'concert'
    state = {}
    pressed = []
    for _ in range(7):
        actions, state = decide(frame, state)
        pressed.append(buttons(actions))
    # A real picker closes on the first B; this frame never changes.
    assert pressed == [['b']] * 7
    assert state['policy']['concert_picker_watch']['repeat'] == 7
    actions, state = decide(frame, state)
    assert buttons(actions) == ['a']
    # The escape starts a fresh count instead of repeating A forever.
    assert 'concert_picker_watch' not in state['policy']
    actions, state = decide(frame, state)
    assert buttons(actions) == ['b']
    assert state['policy']['concert_picker_watch']['repeat'] == 1


def test_picker_screen_that_changes_keeps_using_b():
    state = {}
    for row in range(178, 178 + 10):
        actions, state = decide(concert_picker_frame(gold_row=row), state)
        assert buttons(actions) == ['b']
    # Ten different frames never accumulate the unchanged-repeat count.
    assert state['policy']['concert_picker_watch']['repeat'] == 1


def test_concert_without_picker_still_advances_with_a():
    frame = concert_picker_frame(gold_row=184)
    rgb = bytearray(frame.rgb)
    for x in range(200, 240):
        i = (184 * 256 + x) * 3
        rgb[i:i + 3] = bytes((16, 72, 57))
    actions, _ = decide(Frame(256, 224, bytes(rgb)), {})
    assert buttons(actions) == ['a']

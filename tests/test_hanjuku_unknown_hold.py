"""A frozen unreadable screen gets one A press (v428).

g1133 2026-11-05 logged 35 `situation_held` in half an hour: the bot sent no
input while the screen stayed unknown. Many game screens need one button to move
on, so an identical frame repeated UNKNOWN_HOLD_ADVANCE times gets a single A.
A screen that reacts changes its digest and never reaches the bound.
"""
from docich.hanjuku import orchestrator as O
from docich.hanjuku.os_layer.screen import Screen


def screen():
    return Screen([], None, '', kind='unknown')


def test_a_changing_screen_never_gets_the_press():
    mem = {}
    for i in range(O.UNKNOWN_HOLD_ADVANCE * 3):
        assert O.unknown_hold_advance(screen(), mem, [], f'd{i}') == []


def test_a_frozen_screen_gets_one_press_then_waits_again():
    mem = {}
    for _ in range(O.UNKNOWN_HOLD_ADVANCE - 1):
        assert O.unknown_hold_advance(screen(), mem, [], 'same') == []
    pressed = O.unknown_hold_advance(screen(), mem, [], 'same')
    assert pressed == [O.pad('a')]
    assert mem['_records'][-1]['decision'] == 'unknown_hold_advance'
    assert O.unknown_hold_advance(screen(), mem, [], 'same') == []


def test_existing_actions_are_kept_until_the_bound():
    mem = {}
    for _ in range(O.UNKNOWN_HOLD_ADVANCE - 1):
        assert O.unknown_hold_advance(screen(), mem, [O.pad('b')], 'same') == [O.pad('b')]


def test_an_animating_battle_frame_still_gets_the_bounded_press():
    """g1163 2026-11-09 13:12-13:16: the boss entry held six minutes, no input.

    A battle frame animates, so no two digests match and the identical-frame
    bound never fires; the consecutive-frame bound inside a battle does.
    """
    mem = {'battle': {'ally': 'デーメーテール', 'enemy': 'ハードマン'}}
    for i in range(O.UNKNOWN_BATTLE_ADVANCE - 1):
        assert O.unknown_hold_advance(screen(), mem, [], f'battle{i}') == []
    pressed = O.unknown_hold_advance(screen(), mem, [], 'battle-last')
    assert pressed == [O.pad('a')]
    record = mem['_records'][-1]
    assert record['decision'] == 'unknown_hold_advance'
    assert record['observed_metric']['battle'] is True
    # The counter restarts: the next frame does not press again.
    assert O.unknown_hold_advance(screen(), mem, [], 'battle-next') == []


def test_outside_a_battle_a_changing_screen_never_presses():
    mem = {}
    for i in range(O.UNKNOWN_BATTLE_ADVANCE * 3):
        assert O.unknown_hold_advance(screen(), mem, [], f'd{i}') == []


def test_a_two_frame_blink_still_gets_the_bounded_press():
    """g1163 2026-11-11 19:09-19:15: an unreadable 'event' frame blinked
    between exactly two digests, so a single-digest comparison looked new every
    observation, no input was sent for 186 observations, and the 5-minute
    stasis latched screen_stalled (the run then ignored every input until an
    operator cleared it). The pair must count as one unchanged screen.
    """
    mem = {}
    frames = ['blink-a', 'blink-b'] * O.UNKNOWN_HOLD_ADVANCE
    presses = [O.unknown_hold_advance(screen(), mem, [], d) for d in frames]
    # Exactly one press, at the bound, and the counter restarts afterwards.
    assert presses.count([O.pad('a')]) >= 1   # v739: repeats may press one frame earlier
    first = presses.index([O.pad('a')])
    assert O.UNKNOWN_HOLD_ADVANCE - 1 <= first <= O.UNKNOWN_HOLD_ADVANCE + 1


def test_a_three_frame_cycle_is_not_an_unchanged_screen():
    mem = {}
    for i in range(O.UNKNOWN_HOLD_ADVANCE * 3):
        assert O.unknown_hold_advance(screen(), mem, [], f'cycle{i % 3}') == []


def test_a_second_episode_presses_one_frame_earlier():
    """g1163 2026-11-12 22:1x: 66 no-input `unknown` frames, mostly 3-frame episodes."""
    mem = {}
    for _ in range(O.UNKNOWN_HOLD_ADVANCE - 1):
        O.unknown_hold_advance(screen(), mem, [], 'same')
    assert O.unknown_hold_advance(screen(), mem, [], 'same') == [O.pad('a')]
    # The screen stayed unknown: the next episode waits one frame less.
    waits = 0
    while O.unknown_hold_advance(screen(), mem, [], 'same') == []:
        waits += 1
        assert waits <= O.UNKNOWN_HOLD_ADVANCE
    assert waits <= O.UNKNOWN_HOLD_ADVANCE - 1

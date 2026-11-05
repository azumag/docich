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

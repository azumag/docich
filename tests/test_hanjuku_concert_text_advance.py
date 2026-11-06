"""Event narration lines must advance promptly and not be starved by others."""
from docich.hanjuku.orchestrator import (
    EVENT_TEXT_ADVANCE_MAX,
    event_text_advance,
)


def test_unchanged_event_narration_advances_promptly():
    mem = {}
    for _ in range(EVENT_TEXT_ADVANCE_MAX - 1):
        assert event_text_advance(mem, 'concert', 'text', 'text:hello', []) is False
    assert mem['event_text_watch']['count'] == EVENT_TEXT_ADVANCE_MAX - 1
    # The bounded wait sends A, and the counter restarts for the next line.
    assert event_text_advance(mem, 'concert', 'text', 'text:hello', []) is True
    assert 'event_text_watch' not in mem
    assert event_text_advance(mem, 'concert', 'text', 'text:hello', []) is False


def test_scripted_event_phases_all_advance():
    # The status scan / registration / house owners can hold the frame while a
    # scripted event is on screen; every scripted phase must still advance.
    for phase in ('concert', 'event', 'dialogue'):
        mem = {}
        results = [event_text_advance(mem, phase, 'text', 'text:x', [])
                   for _ in range(EVENT_TEXT_ADVANCE_MAX)]
        assert results[-1] is True


def test_any_action_resets_the_wait():
    mem = {}
    for _ in range(EVENT_TEXT_ADVANCE_MAX - 1):
        event_text_advance(mem, 'event', 'text', 'text:hello', [])
    assert event_text_advance(mem, 'event', 'text', 'text:hello', [{'type': 'pad'}]) is False
    assert 'event_text_watch' not in mem


def test_a_changed_line_restarts_the_wait():
    mem = {}
    for _ in range(EVENT_TEXT_ADVANCE_MAX - 1):
        event_text_advance(mem, 'event', 'text', 'text:first', [])
    assert event_text_advance(mem, 'event', 'text', 'text:second', []) is False
    assert mem['event_text_watch'] == {'key': 'text:second', 'count': 1}


def test_other_phases_and_kinds_never_advance_blindly():
    for phase, kind in (('field', 'text'), ('shop', 'text'), ('event', 'map'),
                        ('month_menu', 'text')):
        mem = {}
        for _ in range(EVENT_TEXT_ADVANCE_MAX + 2):
            assert event_text_advance(mem, phase, kind, 'k', []) is False
        assert 'event_text_watch' not in mem


def test_held_purchase_events_never_advance_blindly():
    # A held beauty/trade request is the same event text frame; a blind A
    # could accept a purchase. The guard must suppress the advance entirely.
    mem = {}
    for _ in range(EVENT_TEXT_ADVANCE_MAX + 2):
        assert event_text_advance(mem, 'event', 'text', 'text:hello', [], True) is False
    assert 'event_text_watch' not in mem


def test_malformed_watch_state_restarts_safely():
    for watch in ({'count': 'x'}, {'count': True}, {'key': 'text:x', 'count': -3},
                  {'key': 'text:x', 'count': 'x'}, {'key': 'text:x', 'count': True}, []):
        mem = {'event_text_watch': watch}
        assert event_text_advance(mem, 'event', 'text', 'text:x', []) is False

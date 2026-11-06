"""Concert narration lines must advance instead of waiting for the 300s stall."""
from docich.hanjuku.orchestrator import (
    CONCERT_TEXT_ADVANCE_MAX,
    concert_text_advance,
)


def test_unchanged_concert_narration_advances_after_the_bound():
    mem = {}
    results = [concert_text_advance(mem, 'concert', 'text', 'text:hello', [])
               for _ in range(CONCERT_TEXT_ADVANCE_MAX - 1)]
    assert results == [False] * (CONCERT_TEXT_ADVANCE_MAX - 1)
    assert mem['concert_text_watch']['count'] == CONCERT_TEXT_ADVANCE_MAX - 1
    # The bounded wait sends A, and the counter restarts for the next line.
    assert concert_text_advance(mem, 'concert', 'text', 'text:hello', []) is True
    assert 'concert_text_watch' not in mem
    assert concert_text_advance(mem, 'concert', 'text', 'text:hello', []) is False


def test_any_action_resets_the_wait():
    mem = {}
    for _ in range(CONCERT_TEXT_ADVANCE_MAX - 1):
        concert_text_advance(mem, 'concert', 'text', 'text:hello', [])
    assert concert_text_advance(mem, 'concert', 'text', 'text:hello', [{'type': 'pad'}]) is False
    assert 'concert_text_watch' not in mem


def test_a_changed_line_restarts_the_wait():
    mem = {}
    for _ in range(CONCERT_TEXT_ADVANCE_MAX - 1):
        concert_text_advance(mem, 'concert', 'text', 'text:first', [])
    assert concert_text_advance(mem, 'concert', 'text', 'text:second', []) is False
    assert mem['concert_text_watch'] == {'key': 'text:second', 'count': 1}


def test_other_phases_and_kinds_never_advance_blindly():
    for phase, kind in (('field', 'text'), ('concert', 'map'), ('shop', 'text')):
        mem = {}
        for _ in range(CONCERT_TEXT_ADVANCE_MAX + 2):
            assert concert_text_advance(mem, phase, kind, 'k', []) is False
        assert 'concert_text_watch' not in mem


def test_malformed_watch_state_restarts_safely():
    for watch in ({'count': 'x'}, {'count': True}, {'key': 'text:x', 'count': -3},
                  {'key': 'text:x', 'count': 'x'}, {'key': 'text:x', 'count': True}, []):
        mem = {'concert_text_watch': watch}
        assert concert_text_advance(mem, 'concert', 'text', 'text:x', []) is False

def test_held_purchase_events_never_advance_blindly():
    # A held beauty/trade request is the same concert text frame; a blind A
    # could accept a purchase. The guard must suppress the advance entirely.
    mem = {}
    for _ in range(CONCERT_TEXT_ADVANCE_MAX + 2):
        assert concert_text_advance(mem, 'concert', 'text', 'text:hello', [], True) is False
    assert 'concert_text_watch' not in mem

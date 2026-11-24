"""Event narration lines must advance promptly and not be starved by others."""
from docich.hanjuku.orchestrator import (
    EVENT_TEXT_ADVANCE_MAX,
    event_text_advance,
    event_text_advance_step,
    _dome_camp_menu_text_owned,
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


def test_typewriter_reveal_keeps_the_wait_counting():
    # g1270 48-7: the month dialogue typed char-by-char, each frame grew the
    # text, the key changed every observation and situation_held repeated.
    # A prefix extension is the same line still being revealed, so the count
    # must keep going and the A completes it.
    mem = {}
    for text in ('48ねん7のつき9899G「お',
                 '48ねん7のつき9899G「おめにかかれて'):
        assert event_text_advance(mem, 'event', 'text', f'text:{text}', []) is False
    assert event_text_advance(
        mem, 'event', 'text',
        'text:48ねん7のつき9899G「おめにかかれてこうえいです。このたび', []) is True


def test_a_shorter_or_replaced_line_restarts_the_wait():
    # A line that shrinks or diverges mid-string is a genuinely new line:
    # the wait must restart, not ride on the previous count.
    mem = {}
    for _ in range(EVENT_TEXT_ADVANCE_MAX - 1):
        event_text_advance(mem, 'event', 'text', 'text:こうえいです', [])
    # A shorter text is not a typewriter extension.
    assert event_text_advance(mem, 'event', 'text', 'text:こう', []) is False
    assert mem['event_text_watch'] == {'key': 'text:こう', 'count': 1}
    # A text that does not start with the previous one is a different line.
    assert event_text_advance(mem, 'event', 'text', 'text:さようなら', []) is False
    assert mem['event_text_watch'] == {'key': 'text:さようなら', 'count': 1}


def test_typewriter_reveal_only_counts_text_keys():
    # Digest-keyed frames (unreadable text) keep resetting on every change;
    # the prefix rule never applies to them.
    mem = {}
    event_text_advance(mem, 'event', 'text', 'aa96ff129027', [])
    assert event_text_advance(mem, 'event', 'text', 'aa96ff129027ff', []) is False
    assert mem['event_text_watch'] == {'key': 'aa96ff129027ff', 'count': 1}


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


def test_typewriter_reveal_counts_despite_planned_action():
    # g1270 48-7 follow-up: the alternating concert/event holds each planned
    # a pad (legacy default), so `not actions` never held and the count never
    # even started. A line still typing counts while a planned action exists.
    mem = {}
    pad_action = [{'type': 'pad', 'buttons': ['a'], 'hold_ms': 100}]
    assert event_text_advance(
        mem, 'event', 'text', 'text:「お', []) is False
    assert event_text_advance(
        mem, 'concert', 'text', 'text:「おめにかかれて', pad_action) is False
    assert event_text_advance(
        mem, 'event', 'text', 'text:「おめにかかれてこうえいです', pad_action) is True


def test_planned_action_on_stable_or_diverging_text_still_resets():
    # Only a typewriter extension overrides `not actions`: a stable or a
    # replaced line with a planned action resets as before.
    mem = {}
    pad_action = [{'type': 'pad', 'buttons': ['b'], 'hold_ms': 100}]
    for _ in range(EVENT_TEXT_ADVANCE_MAX - 1):
        event_text_advance(mem, 'event', 'text', 'text:おめにかかれて', [])
    assert event_text_advance(
        mem, 'event', 'text', 'text:おめにかかれて', pad_action) is False
    assert 'event_text_watch' not in mem
    assert event_text_advance(
        mem, 'event', 'text', 'text:さようなら', pad_action) is False
    assert 'event_text_watch' not in mem



def test_live_chikujou_text_is_never_advanced_as_generic_event_narration():
    mem = {'month_sub': {'kind': 'chikujou', 'build_wait': 7}}
    native_texts = (
        'これいじょうのぞうちくはできませんぞ!!どのしろをぞうちくなさいますか?',
        'どのしろをぞうちく',
        'どのしろをぞうちくなさいますか?',
        'イデ',  # partial castle-name typewriter prefix
        'イデ!',  # width-folded complete label before the suffix
        'イデ！じょう',  # partial quote before the cost line
        'カラじょうですな',  # partial typewriter quote
        'カラじょうですな5Gかかりますがよろしいですかなうむッ!',
        'カラじょうのレベルが2',
        'おかねがたりません',
        'しょうぐんがおりませなんだ',
        'カラじょうのレベルが2になりましたぞ',
        'おかねがたりませんぞ',
    )
    down = [{'type': 'pad', 'buttons': ['down'], 'hold_ms': 100}]
    for text in native_texts:
        for _ in range(EVENT_TEXT_ADVANCE_MAX + 2):
            assert event_text_advance_step(
                mem, 'event', 'text', f'text:{text}', down) is False
        assert mem['month_sub']['build_wait'] == 7
        assert 'event_text_watch' not in mem


def test_advancing_an_unrelated_event_restarts_the_chikujou_transition_wait():
    for key in ('text:イベント会話の本文', 'text:カ', 'text:じょうだんのイベント'):
        mem = {'chapter': 9, 'month_sub': {'kind': 'chikujou', 'build_wait': 19}}
        results = [
            event_text_advance_step(mem, 'event', 'text', key, [])
            for _ in range(EVENT_TEXT_ADVANCE_MAX)
        ]
        assert results[-1] is True
        assert mem['month_sub']['build_wait'] == 0



def test_active_dome_recall_owns_unclassified_text_but_not_other_screens():
    recall = {'stage': 'menu', 'camp_kind': 'dome'}
    assert _dome_camp_menu_text_owned(recall, True, 'text')
    assert _dome_camp_menu_text_owned(recall, True, 'unknown')
    assert not _dome_camp_menu_text_owned(recall, False, 'text')
    assert not _dome_camp_menu_text_owned(
        {'stage': 'menu', 'camp_kind': 'tent'}, True, 'text')
    assert not _dome_camp_menu_text_owned(
        {'stage': 'fort_menu', 'camp_kind': 'dome'}, True, 'text')
    assert not _dome_camp_menu_text_owned(recall, True, 'map')


def test_dome_recall_text_never_uses_the_generic_event_a():
    recall = {'stage': 'menu', 'camp_kind': 'dome'}
    guarded = _dome_camp_menu_text_owned(recall, True, 'text')
    mem = {}
    for _ in range(EVENT_TEXT_ADVANCE_MAX + 2):
        assert event_text_advance_step(
            mem, 'event', 'text', 'text:せめこまれました', [], guarded) is False
    assert 'event_text_watch' not in mem

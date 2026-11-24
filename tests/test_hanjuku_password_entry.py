"""The Ovarium password screen enters PHRASE in order from real measured frames.

The fixtures are raw 256x224 frames from the 2026-11-24 chapter-11 probe
(``tmp/hanjuku-local/probe-pw``). The left-bottom field holds the *entered*
words in entry order; the right list keeps every collected word and only
greys the used rows. v1456 read that field as "remaining words", so the hand
kept answering up/down in front of a grey row and never pressed A (deadlock).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse
from docich.hanjuku.flows import password

FIXTURES = Path(__file__).parent / 'fixtures' / 'hanjuku'
A = password.pad('a', password._A_FRAMES)
UP = password.pad('up', password._NAV_FRAMES)
DOWN = password.pad('down', password._NAV_FRAMES)


def screen(name):
    return parse(read_png(FIXTURES / f'hanjuku-password-{name}.png'))


def test_real_frames_are_classified_as_the_password_screen():
    for name in ('prompt-only', 'list-top', 'first-entered', 'all-ten'):
        assert screen(name).kind == 'password_entry'


def test_prompt_only_screen_sends_one_a_because_time_never_opens_the_list():
    # 4320 frames (72 s) with no input kept the prompt; one A opened the list.
    assert password.password_step(screen('prompt-only'), {}) == [A]


def test_list_with_the_hand_off_the_phrase_word_moves_the_hand():
    # Hand on かんじゅく (y23); PHRASE[0] われらが is the next row down.
    assert password.password_step(screen('list-top'), {}) == [DOWN]


def test_hand_on_the_phrase_word_presses_a():
    mem = {}
    assert password.password_step(screen('hand-on-target'), mem) == [A]
    assert mem['password_entry']['entered'] == 0


def test_entered_field_is_read_row_major_and_exactly():
    # 3 columns x 4 rows, filled in entry order: われらが ... みちびけ.
    entered, unreadable = password._entered(screen('all-ten'), strict=True)
    assert entered == list(password.PHRASE)
    assert not unreadable


def test_first_word_is_read_back_from_the_entered_field():
    mem = {}
    # One word (われらが) entered; かんじゅく is the visible row above the hand.
    assert password.password_step(screen('first-entered'), mem) == [UP]
    assert mem['password_entry']['entered'] == 1


def test_a_partial_sequence_resumes_from_the_screen_count():
    # No prior state: the next word comes from the measured entered field.
    assert password.password_step(screen('third-entered'), {}) == [DOWN]   # いだいなる
    assert password.password_step(screen('fourth-entered'), {}) == [UP]    # はは
    assert password.password_step(screen('eighth-entered'), {}) == [UP]    # われを (above)
    assert password.password_step(screen('ninth-entered'), {}) == [UP]     # みちびけ (off screen)


def test_hand_on_a_grey_row_never_presses_a():
    actions = password.password_step(screen('hand-on-grey'), {})
    assert [a['buttons'] for a in actions] == [['down']]


def test_a_visible_target_is_reached_by_moving_the_hand():
    # われらが at y23, hand at the bottom row: up, never a blind A.
    assert password.password_step(screen('list-target-visible'), {}) == [UP]


def test_the_closed_loop_tops_the_list_then_scans_down_to_the_word():
    # From the bottom window (われらが off screen) up to the list top, then
    # the word appears under the hand at the top row.
    mem = {}
    assert password.password_step(screen('list-bottom-edge'), mem) == [UP]
    assert password.password_step(screen('top-reset-1'), mem) == [UP]
    assert password.password_step(screen('top-reset-4'), mem) == [UP]
    assert password.password_step(screen('top-reset-6'), mem) == [UP]
    assert password.password_step(screen('top-reset-7'), mem) == [UP]
    assert password.password_step(screen('top-reset-8'), mem) == [A]


def test_the_top_edge_turns_the_loop_down_one_row_per_observation():
    # k=7 wants オヴァリウムよ (last row of the collected list, off screen at
    # the top). Two unchanged frames at the top are tolerated as latency;
    # the loop then scans down.
    mem = {}
    assert password.password_step(screen('scan-up-00'), mem) == [UP]
    assert password.password_step(screen('scan-up-02'), mem) == [UP]
    assert password.password_step(screen('scan-up-04'), mem) == [UP]
    assert password.password_step(screen('scan-up-04'), mem) == [UP]
    assert password.password_step(screen('scan-up-04'), mem) == [DOWN]
    assert password.password_step(screen('scan-down-01'), mem) == [DOWN]
    assert password.password_step(screen('scan-down-06'), mem) == [DOWN]
    assert password.password_step(screen('scan-down-07'), mem) == [DOWN]
    assert password.password_step(screen('scan-down-08'), mem) == [DOWN]
    # オヴァリウムよ is now under the hand on the bottom row.
    assert password.password_step(screen('scan-down-09'), mem) == [A]


def test_all_ten_words_hold_without_any_reset_input():
    mem = {}
    assert password.password_step(screen('all-ten'), mem) == []
    assert mem['password_entry']['entered'] == len(password.PHRASE)
    # The frame right after the tenth A is still the password screen.
    for _ in range(3):
        assert password.password_step(screen('all-ten'), mem) == []


def test_a_wrong_first_word_is_held_and_never_continued(monkeypatch):
    # Measured: the door accepted かんじゅく as the first word; continuing it
    # would build a wrong sequence, and the screen has no measured reset.
    events = []
    monkeypatch.setattr(password, '_record', lambda _, event, **kw: events.append(event))
    for _ in range(3):
        assert password.password_step(screen('wrong-first-word'), {}) == []
    assert events == ['password_entry_prefix_mismatch']


def test_a_used_grey_row_is_never_reconfirmed():
    # かんじゅく + われらが entered after the hand already sat on them.
    assert password.password_step(screen('duplicate-grey'), {}) == []

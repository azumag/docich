"""美女イベント: 1000G以上なら家、それ以外・読取不明は断る。"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import json

import pytest

from docich import hanjuku_policy as policy


def bijin_pixels(*, question=True, gold='1ねん 6のつき 500G',
                 rows=('うんデヘヘ!', 'けえんな!'), selected=0, hand=True,
                 x=(40, 40), y=(183, 199), cursor=None, unread=False):
    from test_hanjuku_chart_bot import Canvas, GREEN
    # A bottom dialogue panel, without the all-green Canvas's shop background.
    canvas = Canvas((0, 0, 0))
    canvas.rgb[144 * 256 * 3:] = bytes(GREEN) * (256 * 80)
    if gold:
        canvas.text(48, 15, gold)
    if question:
        canvas.text(24, 151, 'きょうは おねがいが')
        canvas.text(24, 167, 'あったの きいてくれる?')
    for i, word in enumerate(rows):
        canvas.text(x[i], y[i], word)
    if unread:
        canvas.tile(x[1], y[1], 0x0123456789ABCDEF)
    if hand:
        hx, hy = cursor if cursor is not None else (x[selected] - 22, y[selected] - 6)
        canvas.hand(hx, hy)
    return canvas.frame()


def bijin_state():
    return {'policy': {'tick': 10}}


def prose_pixels(*, prose='むらの ひとは はなしている', prose_x=24, tiles=()):
    """A bottom panel with an unrelated readable page and no choice band.

    ``tiles`` places raw unread tiles, which is how a partly read body and a
    fully unread body are built: ``joined()`` strips the row edges, so those
    frames are exactly the ones ``screen.text`` cannot describe.
    """
    from test_hanjuku_chart_bot import Canvas, GREEN
    canvas = Canvas((0, 0, 0))
    canvas.rgb[144 * 256 * 3:] = bytes(GREEN) * (256 * 80)
    canvas.text(48, 15, '1ねん 6のつき 500G')
    if prose:
        canvas.text(prose_x, 151, prose)
    for x, y in tiles:
        canvas.tile(x, y, 0x0123456789ABCDEF)
    return canvas.frame()


def decide_bijin(frame, state=None):
    from docich.hanjuku_bot import classify, decide
    from docich.hanjuku_screen import parse
    screen = parse(frame)
    assert classify(frame) == 'dialogue' and screen.kind == 'text'
    return decide(frame, bijin_state() if state is None else state)


def test_bijin_question_and_choices_decline_in_the_public_pixel_decision_path():
    actions, state = decide_bijin(bijin_pixels())
    assert actions == [policy.pad('down')]
    assert state['policy']['bijin_guard']['chosen'] is False
    actions, state = decide_bijin(bijin_pixels(selected=1), state)
    assert actions == [policy.pad('a')]
    record = state['_records'][-1]
    assert record['strategy_variant'] == 'decline_bijin_below_floor'
    assert record['choice'] == 'けえんな!'
    assert state['policy']['bijin_guard']['chosen'] is True


def test_bijin_choice_is_already_declined_when_the_hand_rests_on_it():
    actions, state = decide_bijin(bijin_pixels(selected=1))
    assert actions == [policy.pad('a')]
    assert state['_records'][-1]['choice'] == 'けえんな!'


@pytest.mark.parametrize('gold', [1000, 1001, 2000])
def test_at_or_above_floor_accepts_for_house(gold):
    actions, state = decide_bijin(bijin_pixels(gold=f'1ねん 6のつき {gold}G'))
    assert actions == [policy.pad('a')]
    assert state['_records'][-1]['strategy_variant'] == 'accept_bijin_house'



def test_missing_hand_holds_the_readable_refusal_instead_of_legacy_a():
    state = bijin_state()
    actions, state = decide_bijin(bijin_pixels(hand=False), state)
    assert actions == []
    assert state['policy']['bijin_guard']['held'] is True
    assert not state['policy']['bijin_guard']['chosen']
    assert any(row['decision'] == 'situation_held' for row in state['_records'])
    actions, state = decide_bijin(bijin_pixels(selected=1), state)
    assert actions == [policy.pad('a')]


def test_unreadable_refusal_row_holds_without_a_blind_a():
    actions, state = decide_bijin(bijin_pixels(unread=True))
    assert actions == []
    assert state['policy']['bijin_guard']['held'] is True
    assert not any(row.get('strategy_variant') == 'decline_bijin_below_floor'
                   for row in state['_records'])


def test_json_restored_bijin_hold_survives_observations_100_and_101():
    from docich import hanjuku_bot as bot, hanjuku_screen as screen_module
    from docich.hanjuku_bot import NO_INPUT_HOLD_MAX, decide
    from docich.hanjuku_screen import parse
    from hanjuku_frame_read_cache import reuse_frame_reads
    frame = bijin_pixels(hand=False)
    assert parse(frame).kind == 'text'
    state = bijin_state()
    # Reuse only immutable frame reads, as the public no-input gate contracts
    # do. Every policy/FSM decision and JSON persistence round trip still runs.
    with reuse_frame_reads(bot, screen_module):
        for observation in range(1, NO_INPUT_HOLD_MAX + 11):
            state = json.loads(json.dumps(state, ensure_ascii=False))
            actions, state = decide(frame, state)
            assert actions == []
            assert state['no_input_streak'] == observation
            assert state['policy']['bijin_guard']['held'] is True
            assert not state['policy']['bijin_guard']['chosen']
            assert not any(row['decision'] == 'no_input_fallback' for row in state['_records'])


def test_an_expired_lead_in_without_any_seen_choice_releases_normally():
    state = bijin_state()
    actions, state = decide_bijin(bijin_pixels(rows=(), hand=False), state)
    assert isinstance(state['policy'].get('bijin_guard'), dict)   # armed, no choice seen yet
    assert not state['policy']['bijin_guard'].get('choice_seen')
    # The question disappeared before any choice was read; the lease expires.
    state['policy']['tick'] = 10 + policy.BIJIN_GUARD_OBSERVATIONS + 1
    actions, state = decide_bijin(bijin_pixels(question=False, rows=(), hand=False), state)
    assert actions == [policy.pad('a')]
    assert 'bijin_guard' not in state['policy']


def test_unread_question_after_a_seen_choice_keeps_the_request_pending():
    # Owner review (2026-10-15, PR #2286): once the choices of a pending
    # request have been seen, a partly unread question is not end evidence --
    # the episode must not slide back to the generic A while the accept line
    # may still be selected with the hand blinking over it.
    state = bijin_state()
    # 1. question and both choices readable, no hand -> choice_seen / held.
    actions, state = decide_bijin(bijin_pixels(hand=False), state)
    assert actions == []
    assert state['policy']['bijin_guard']['choice_seen'] is True
    assert state['policy']['bijin_guard']['held'] is True
    # 2. only the question is missing; the dialogue frame and both choices stay.
    actions, state = decide_bijin(bijin_pixels(question=False, hand=False), state)
    assert actions == []
    assert state['policy']['bijin_guard']['held'] is True
    assert state['policy']['bijin_guard']['choice_seen'] is True
    assert state['policy']['bijin_guard']['chosen'] is False
    assert not any(row['decision'] == 'no_input_fallback' for row in state['_records'])
    # 3. an explicit end condition still releases the pending request.
    actions, state = decide_bijin(bijin_pixels(selected=1), state)
    assert actions == [policy.pad('a')]
    assert state['policy']['bijin_guard']['chosen'] is True


def test_a_readable_page_without_the_request_releases_the_held_episode():
    # Follow-up to PR #2286: the hold needs trade_guard's "a readable different
    # prose screen" boundary as well. Without it, `bijin_guard_hold` keeps the
    # no-input fallback suppressed on an unrelated readable page (orchestrator
    # L407) and the bot sends nothing at all, on every observation.
    from docich.hanjuku_screen import parse
    state = bijin_state()
    actions, state = decide_bijin(bijin_pixels(hand=False), state)
    assert actions == []
    assert state['policy']['bijin_guard']['choice_seen'] is True
    # No request wording, no choice band, no hand and an actually readable body:
    # the end evidence is read from the raw cells (PR #2325 review), so the page
    # has to carry prose and cannot be the header alone.
    frame = prose_pixels()
    assert parse(frame).kind == 'text'
    actions, state = decide_bijin(frame, state)
    assert actions == [policy.pad('a')]
    assert 'bijin_guard' not in state['policy']


def test_a_partly_unread_unrelated_page_still_holds_without_a_blind_a():
    # The release above needs a positively readable page: one UNKNOWN cell is
    # enough to keep the episode held, so the owner review's blind A cannot
    # come back through this boundary.
    from docich.hanjuku_screen import parse
    state = bijin_state()
    actions, state = decide_bijin(bijin_pixels(hand=False), state)
    assert actions == []
    frame = prose_pixels(tiles=((24 + 8 * 3, 151),))   # one unread cell in the prose
    assert parse(frame).kind == 'text'
    actions, state = decide_bijin(frame, state)
    assert actions == []
    assert state['policy']['bijin_guard']['held'] is True
    assert not any(row['decision'] == 'no_input_fallback' for row in state['_records'])


@pytest.mark.parametrize('tiles, prose_x', [
    (((24, 151),), 32),      # the body lost its leading cell
    (((136, 151),), 24),     # the body lost its trailing cell
])
def test_a_body_that_lost_an_edge_cell_keeps_the_hold(tiles, prose_x):
    # `joined()` strips UNKNOWN from both ends of every row, so both pages still
    # read as prose in `screen.text`. The old `UNKNOWN not in flat` check
    # released the pending request here and handed the page to the generic A.
    from docich.hanjuku_screen import parse
    from docich.hanjuku_font import UNKNOWN
    state = bijin_state()
    actions, state = decide_bijin(bijin_pixels(hand=False), state)
    assert actions == []
    frame = prose_pixels(prose_x=prose_x, tiles=tiles)
    screen = parse(frame)
    assert screen.kind == 'text'
    assert UNKNOWN not in screen.text          # the aggregate hides the gap
    actions, state = decide_bijin(frame, state)
    assert actions == []
    assert state['policy']['bijin_guard']['held'] is True
    assert not any(row['decision'] == 'no_input_fallback' for row in state['_records'])


def test_a_header_only_page_keeps_the_hold():
    # The gold header alone leaves `screen.text` non-empty, so the old boundary
    # released a page whose body was never read (PR #2325 review, P1).
    from docich.hanjuku_screen import parse
    state = bijin_state()
    actions, state = decide_bijin(bijin_pixels(hand=False), state)
    assert actions == []
    frame = prose_pixels(prose='')
    assert parse(frame).kind == 'text'
    actions, state = decide_bijin(frame, state)
    assert actions == []
    assert state['policy']['bijin_guard']['held'] is True
    assert not any(row['decision'] == 'no_input_fallback' for row in state['_records'])


def test_a_body_that_is_entirely_unread_keeps_the_hold():
    from docich.hanjuku_screen import parse
    state = bijin_state()
    actions, state = decide_bijin(bijin_pixels(hand=False), state)
    assert actions == []
    frame = prose_pixels(prose='', tiles=tuple((24 + 8 * i, 151) for i in range(4)))
    assert parse(frame).kind == 'text'
    actions, state = decide_bijin(frame, state)
    assert actions == []
    assert state['policy']['bijin_guard']['held'] is True
    assert not any(row['decision'] == 'no_input_fallback' for row in state['_records'])


def test_a_completed_bijin_request_releases_its_guard():
    actions, state = decide_bijin(bijin_pixels(selected=1))
    assert state['policy']['bijin_guard']['chosen'] is True
    actions, state = decide_bijin(bijin_pixels(question=False, rows=(), hand=False), state)
    assert actions == [policy.pad('a')]
    assert 'bijin_guard' not in state['policy']


def test_a_new_monthly_owner_clears_a_previous_bijin_hold():
    from docich.hanjuku_screen import parse
    actions, state = decide_bijin(bijin_pixels(hand=False))
    assert actions == []
    mem = state['policy']
    mem['month_sub'] = {'kind': 'recruit'}
    assert policy.bijin_guard_step(parse(bijin_pixels(hand=False)), mem, phase='dialogue') is None
    assert 'bijin_guard' not in mem


def test_a_bijin_hold_cannot_exempt_an_unrelated_unknown_screen():
    from docich.hanjuku_bot import NO_INPUT_HOLD_MAX, classify, decide
    from docich.hanjuku_screen import parse
    from test_hanjuku_chart_bot import Canvas
    _, state = decide_bijin(bijin_pixels(hand=False))
    state = json.loads(json.dumps(state))
    frame = Canvas((0, 0, 0)).frame()
    assert classify(frame) == 'transition' and parse(frame).kind == 'unknown'
    state['no_input_frames'] = [frame.digest()]
    state['no_input_streak'] = NO_INPUT_HOLD_MAX
    actions, state = decide(frame, state)
    assert actions == [policy.pad('a')]
    assert 'bijin_guard' not in state['policy']
    assert any(row['decision'] == 'no_input_fallback' for row in state['_records'])


def monthly_event_pixels(*, gold='1ねん 6のつき 500G',
                         question=('きょうは おねがいが', 'あったの きいてくれる?'),
                         rows=('うんデヘヘ!', 'けえんな!'), selected=0,
                         hand=True, unread=None):
    """The measured monthly-event scene: red curtains, green bottom window.

    ``classify()`` returns 'concert' for this frame -- the real gcgx
    ninja.png does -- which is why the old dialogue-only ownership never
    reached the beauty guard in production.
    """
    from test_hanjuku_chart_bot import Canvas, GREEN
    canvas = Canvas((118, 78, 42))                      # the room behind her
    for y in range(128):
        for x in tuple(range(0, 32)) + tuple(range(224, 256)):
            canvas.put(x, y, (205, 0, 0))               # the red curtain
    canvas.rgb[144 * 256 * 3:] = bytes(GREEN) * (256 * 80)
    if gold:
        canvas.text(48, 15, gold)
    if question:
        canvas.text(24, 151, question[0])
        canvas.text(24, 167, question[1])
    for i, word in enumerate(rows):
        canvas.text(184, (183, 199)[i], word)
    if unread is not None:
        canvas.tile(unread[0], unread[1], 0x0123456789ABCDEF)
    if hand:
        canvas.hand(162, 177 + 16 * selected)
    return canvas.frame()


def decide_bijin_monthly(frame, state=None):
    from docich.hanjuku_bot import classify, decide
    from docich.hanjuku_screen import parse
    assert classify(frame) == 'concert' and parse(frame).kind == 'text'
    return decide(frame, bijin_state() if state is None else state)


def test_the_measured_monthly_event_phase_declines_below_the_floor():
    # The event is drawn on the red-curtain scene, so the frame is phase
    # 'concert'. The guard used to require 'dialogue' and never owned it: the
    # generic concert A accepted the request whatever the gold was.
    frame = monthly_event_pixels(gold='1ねん 6のつき 999G')
    actions, state = decide_bijin_monthly(frame)
    assert actions == [policy.pad('down')]
    assert state['policy']['bijin_guard']['chosen'] is False
    actions, state = decide_bijin_monthly(monthly_event_pixels(gold='1ねん 6のつき 999G',
                                                               selected=1), state)
    assert actions == [policy.pad('a')]
    record = state['_records'][-1]
    assert record['strategy_variant'] == 'decline_bijin_below_floor'
    assert record['choice'] == 'けえんな!'


def test_native_choice_labels_alone_decline_when_the_question_is_unread():
    # One unread cell inside おねがい breaks the wording match. The event's own
    # labels are still the identity, so the blind A must not come back.
    frame = monthly_event_pixels(gold='1ねん 6のつき 500G', unread=(64, 151))
    from docich.hanjuku_screen import parse
    assert 'おねがい' not in parse(frame).text.replace(' ', '')
    actions, state = decide_bijin_monthly(frame)
    assert actions == [policy.pad('down')]
    actions, state = decide_bijin_monthly(
        monthly_event_pixels(gold='1ねん 6のつき 500G', selected=1, unread=(64, 151)), state)
    assert actions == [policy.pad('a')]
    assert state['_records'][-1]['strategy_variant'] == 'decline_bijin_below_floor'


def test_at_the_floor_the_measured_event_accepts_for_the_house():
    actions, state = decide_bijin_monthly(monthly_event_pixels(gold='1ねん 6のつき 1000G'))
    assert actions == [policy.pad('a')]
    assert state['_records'][-1]['strategy_variant'] == 'accept_bijin_house'


def test_yes_no_step_applies_the_floor_to_the_bijin_request_too():
    # Defense in depth for the measured yes/no listing: a monthly owner or any
    # other frame owner can still hand this screen to the ordinary yes/no step.
    from docich.hanjuku_screen import parse
    rows = ('うむッ!', 'いかんッ!')
    screen = parse(monthly_event_pixels(gold='1ねん 6のつき 999G', rows=rows))
    mem = {'_records': []}
    assert screen.kind == 'yes_no'
    assert policy.yes_no_step(screen, mem) == [policy.pad('down')]
    screen = parse(monthly_event_pixels(gold='1ねん 6のつき 999G', rows=rows, selected=1))
    assert policy.yes_no_step(screen, mem) == [policy.pad('a')]
    record = mem['_records'][-1]
    assert record['strategy_variant'] == 'decline_bijin_below_floor'
    assert record['observed_metric'] == {'gold': 999, 'minimum_gold': 1000}
    screen = parse(monthly_event_pixels(gold=None, rows=rows, selected=1))
    assert policy.yes_no_step(screen, mem) == [policy.pad('a')]
    assert mem['_records'][-1]['strategy_variant'] == 'decline_bijin_unread_gold'
    screen = parse(monthly_event_pixels(gold='1ねん 6のつき 1000G', rows=rows))
    assert policy.yes_no_step(screen, mem) == [policy.pad('a')]
    assert mem['_records'][-1]['strategy_variant'] == 'accept_bijin_house'


def test_a_held_request_suppresses_the_no_input_fallback_in_the_event_phase():
    from docich.hanjuku_bot import NO_INPUT_HOLD_MAX, decide
    frame = monthly_event_pixels(gold='1ねん 6のつき 500G', hand=False)
    state = {'policy': {'tick': 10}, 'no_input_frames': [frame.digest()],
             'no_input_streak': NO_INPUT_HOLD_MAX}
    actions, state = decide(frame, state)
    assert actions == []
    assert state['policy']['bijin_guard']['held'] is True
    assert not any(row['decision'] == 'no_input_fallback' for row in state['_records'])


def test_unreadable_choice_labels_decline_by_the_measured_rows_after_the_bound():
    # The corner ends after 300 s without a screen change, so a held request may
    # not wait forever. After the bounded hold the measured row order (upper
    # accept / lower refuse) and the visible hand select the refusal.
    from docich.hanjuku_bot import decide
    from docich.hanjuku_screen import parse
    rows = ('????????', '????????')
    frame = monthly_event_pixels(gold='1ねん 6のつき 500G', rows=rows)
    assert parse(frame).kind == 'text'
    state = {'policy': {'tick': 10}}
    for n in range(policy.BIJIN_GUARD_HOLD_MAX):
        state['policy']['tick'] = 11 + n
        actions, state = decide(frame, state)
        assert actions == []
        assert state['policy']['bijin_guard']['held'] is True
    state['policy']['tick'] += 1
    actions, state = decide(frame, state)
    assert actions == [policy.pad('down')]     # toward the lower refusal row
    moved = monthly_event_pixels(gold='1ねん 6のつき 500G', rows=rows, selected=1)
    state['policy']['tick'] += 1
    actions, state = decide(moved, state)
    assert actions == [policy.pad('a')]
    record = state['_records'][-1]
    assert record['strategy_variant'] == 'decline_bijin_position'
    assert record['choice'] == 'けえんな（位置）'
    assert record['observed_metric']['held_observations'] > policy.BIJIN_GUARD_HOLD_MAX

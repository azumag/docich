"""美女イベント（月イチ）: 所持金1000G以下・読取不明では必ず断る。

Owner (2026-10-15): お金に余裕のないとき（少なくとも1000G以下）は必ず「いかん」
で断る。倒転王国 monthlyevent.html: 依頼文は「〜お願いがあって来たの……
聞いてくれる?」、選択は うんデヘヘ!（承諾 → どれか ほしいなー → 香水100G/
車200G/家500G の購入メニュー）と けえんな!（断り）。半熟値が上がるのは家500G
だけで、香水・車は何も起きない。
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import json

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


def test_at_the_floor_is_declined_and_one_g_gold_above_keeps_the_existing_route():
    actions, state = decide_bijin(bijin_pixels(gold='1ねん 6のつき 1000G'))
    assert actions == [policy.pad('down')]   # 1000G is still <= the floor
    actions, state = decide_bijin(bijin_pixels(gold='1ねん 6のつき 1001G'))
    assert actions == [policy.pad('a')]      # over the floor: the legacy answer, as before
    assert 'bijin_guard' not in state['policy']


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

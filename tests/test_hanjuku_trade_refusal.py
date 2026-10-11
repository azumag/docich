"""Recognized trade confirmations cannot borrow a monthly acceptance owner.

Synthetic glyph/hand compositions only; no ROM, process or input transport.
"""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich import hanjuku_policy as policy
from docich.hanjuku_bot import NO_INPUT_HOLD_MAX, classify, decide
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import Canvas, GREEN, _fewer_generals_than_castles_memory
from test_hanjuku_month_foreground import monthly_canvas, monthly_memory


def confirmation(*, background=False, cursor='accept', trade=True, both_choices=True,
                 prompt='しょうぐんをぼしゅうしますか?'):
    canvas = monthly_canvas() if background else Canvas((50, 30, 10))
    if not background:
        # A dialogue panel without merchant/monthly green rectangles above it.
        for y in range(128, 207):
            for x in range(18, 236):
                canvas.put(x, y, GREEN)
    if trade:
        # Strong ally offered up, with the literal トレード token lost to OCR.
        canvas.text(24, 135, 'そちらのゼウスしょうぐんと')
        canvas.text(24, 151, 'わがぐんのイキのいいの')
    else:
        canvas.text(24, 151, prompt)
    canvas.text(184, 183, 'うむッ!')
    if both_choices:
        canvas.text(184, 199, 'いかんッ!')
    if cursor in ('accept', 'ambiguous'):
        canvas.hand(162, 177)
    if cursor in ('decline', 'ambiguous'):
        canvas.hand(162, 193)
    return canvas.frame()


def owned_state(owner):
    mem = monthly_memory()
    if owner == 'recruit':
        # A genuine recruitment-capable owner: confirmed recovery and a
        # complete same-month roster with fewer generals than owned castles.
        observed = _fewer_generals_than_castles_memory()
        observed['month'] = mem['month']
        observed['recruit_roster']['month'] = mem['month']
        for income in observed['castle_income'].values():
            income['month'] = mem['month']
        mem.update(observed)
        mem['shop'] = {'key': '2-11', 'egg': 'not_needed'}
        mem['month_sub'] = {'kind': 'recruit', 'gold_before': 59, 'presses': 0,
                            'key': '2-11', 'left_menu': True}
    elif owner == 'month_exit':
        mem['month_exit'] = True
    return {'policy': mem}


@pytest.mark.parametrize('background', [False, True])
@pytest.mark.parametrize('owner', [None, 'recruit', 'month_exit'])
def test_trade_refusal_overrides_monthly_acceptance_owner(background, owner):
    state = owned_state(owner)
    actions, state = decide(confirmation(background=background), state)
    assert actions == [policy.pad('down')]
    assert not any(row['decision'] == 'month_sub_step' for row in state['_records'])

    actions, state = decide(confirmation(background=background, cursor='decline'), state)
    assert actions == [policy.pad('a')]
    refusal = [row for row in state['_records']
               if row['strategy_variant'] == 'decline_general_trade']
    assert len(refusal) == 1 and refusal[0]['choice'] == 'いかんッ!'
    # Refusal cannot declare recruitment, monthly exit or a payment complete.
    assert state['policy']['gold'] == 59
    if owner == 'recruit':
        assert state['policy']['month_sub']['presses'] == 0
        assert not state['policy']['month_sub'].get('recruit_paid_gold')
    elif owner == 'month_exit':
        assert state['policy']['month_exit'] is True


@pytest.mark.parametrize('background', [False, True])
@pytest.mark.parametrize('owner', [None, 'recruit', 'month_exit'])
@pytest.mark.parametrize('cursor', ['missing', 'ambiguous'])
def test_trade_with_unreadable_cursor_never_falls_back_to_accept(background, owner, cursor):
    frame = confirmation(background=background, cursor=cursor)
    screen = parse(frame)
    assert classify(frame) == ('month_menu' if background else 'dialogue')
    assert screen.hand is None and screen.selected is None
    state = owned_state(owner)
    actions, state = decide(frame, state)
    assert actions == []
    assert any(row['decision'] == 'situation_held' for row in state['_records'])

    state['no_input_frames'] = [frame.digest()]
    state['no_input_streak'] = NO_INPUT_HOLD_MAX
    actions, state = decide(frame, state)
    assert actions == [policy.pad('b')]
    assert any(row['decision'] == 'no_input_fallback' for row in state['_records'])


@pytest.mark.parametrize('background', [False, True])
@pytest.mark.parametrize('owner', ['recruit', 'month_exit'])
@pytest.mark.parametrize('prompt', [
    'しょうぐんをぼしゅうしますか?',
    'よろしいですかな?',
    'これでもうみせじまいしますが',
    'たまごをつかいますか?',
    'ごあいてをしましょうか?',
])
def test_nontrade_confirmation_keeps_monthly_acceptance_owner(background, owner, prompt):
    # A regular recruit or owned monthly-exit confirmation is still accepted.
    frame = confirmation(background=background, trade=False, prompt=prompt)
    actions, state = decide(frame, owned_state(owner))
    assert actions == [policy.pad('a')]
    assert not any(row['strategy_variant'] == 'decline_general_trade'
                   for row in state['_records'])


def test_trade_words_without_both_choices_do_not_take_over_recruit_dialogue():
    frame = confirmation(both_choices=False)
    actions, state = decide(frame, owned_state('recruit'))
    assert actions == [policy.pad('a')]
    assert state['policy']['month_sub']['presses'] == 1
    assert not any(row['strategy_variant'] == 'decline_general_trade'
                   for row in state['_records'])

"""Actual public offer wording, drawn with synthetic native glyphs.

https://gcgx.games/hanjuku/img/ninja.png reads うちとってみせるが, not
the former fixture's うちとおすか. No live input or incident match is claimed.
"""
import json

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku_bot import decide, NO_INPUT_HOLD_MAX
from docich.hanjuku_screen import parse
from test_hanjuku_goninja_cash_floor import offer, owned_state

NATIVE = 'うちとってみせるがいかがかな!?'


@pytest.mark.parametrize('background', [False, True])
@pytest.mark.parametrize('owner', ['none', 'recruit', 'month_exit'])
def test_native_twenty_seven_gold_offer_moves_to_decline_then_confirms(background, owner):
    frame = offer(27, background=background, prompt=NATIVE)
    assert parse(frame).header['gold'] == 27
    actions, state = decide(frame, owned_state(owner))
    assert actions == [p.pad('down')]
    frame = offer(27, background=background, cursor='decline', prompt=NATIVE)
    actions, state = decide(frame, json.loads(json.dumps(state)))
    assert actions == [p.pad('a')]
    record = state['_records'][-1]
    assert record['choice'] == 'いかんッ!' and record['strategy_variant'] == 'decline_goninja_below_floor'
    assert record['observed_metric'] == {'gold': 27, 'minimum_gold': 300}


@pytest.mark.parametrize('gold,choice,variant', [(None, 'いかんッ!', 'decline_goninja_unreadable'),
    (299, 'いかんッ!', 'decline_goninja_below_floor'),
    (300, 'うむッ!', 'accept_goninja'), (301, 'うむッ!', 'accept_goninja')])
def test_native_wording_keeps_the_current_cash_boundary(gold, choice, variant):
    frame = offer(gold, cursor='decline' if choice == 'いかんッ!' else 'accept', prompt=NATIVE)
    actions, state = decide(frame, owned_state('recruit'))
    assert actions == [p.pad('a')]
    assert state['_records'][-1]['choice'] == choice
    assert state['_records'][-1]['strategy_variant'] == variant


@pytest.mark.parametrize('text', ['50Gでいいですか?', 'うちとってみるか?', 'ごあいてしましょう',
                                 'しょうぐんをぼしゅうしますか?'])
def test_partial_or_generic_words_do_not_become_goninja(text):
    assert not p._is_goninja_prompt(text)


@pytest.mark.parametrize('background', [False, True])
@pytest.mark.parametrize('cursor', [None, 'ambiguous'])
def test_native_low_cash_unknown_cursor_cannot_borrow_stall_fallback_a(background, cursor):
    frame = offer(27, background=background, cursor=cursor, prompt=NATIVE)
    state = owned_state('recruit')
    state.update(no_input_streak=NO_INPUT_HOLD_MAX + 1, no_input_frames=[frame.digest()])
    actions, state = decide(frame, state)
    assert actions == [p.pad('b')]
    assert not any(r.get('choice') == 'うむッ!' for r in state['_records'])

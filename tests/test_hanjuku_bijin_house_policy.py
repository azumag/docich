"""Owner 2026-10-21: >=1000G chooses a house; below declines the request."""
import json
import pytest
from docich import hanjuku_policy as policy
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import Canvas
from test_hanjuku_bijin_guard import bijin_pixels, decide_bijin


def gifts(gold=1000, selected=0, house='いえ500G'):
    c = Canvas()
    if gold is not None:
        c.text(48, 15, f'1ねん 6のつき {gold}G')
    c.text(24, 39, 'どれか ほしいなー')
    for y, text in zip((167, 183, 199), ('こうすい100G', 'くるま200G', house)):
        c.text(168, y, text)
    c.hand(146, (161, 177, 193)[selected])
    return c.frame()


@pytest.mark.parametrize('gold', [999, 1000, 1001])
def test_boundary_question_then_house(gold):
    actions, state = decide_bijin(bijin_pixels(gold=f'1ねん 6のつき {gold}G'))
    if gold < 1000:
        assert actions == [policy.pad('down')]
        actions, state = decide_bijin(bijin_pixels(gold=f'1ねん 6のつき {gold}G', selected=1), state)
        assert actions == [policy.pad('a')]
        assert state['_records'][-1]['strategy_variant'] == 'decline_bijin_below_floor'
    else:
        assert actions == [policy.pad('a')]
        mem = json.loads(json.dumps(state['policy']))
        for selected in (0, 1):
            assert policy.gift_step(parse(gifts(gold, selected)), mem) == [policy.pad('down')]
        assert policy.gift_step(parse(gifts(gold, 2)), mem) == [policy.pad('a')]
        assert mem['_records'][-1]['item'] == 'いえ'
        assert mem['_records'][-1]['price'] == 500


@pytest.mark.parametrize('gold,house', [(999,'いえ500G'), (None,'いえ500G'),
                                      (1000,'????500G'), (1000,'いえ999G')])
def test_unproven_house_purchase_never_falls_back_to_cheapest(gold, house):
    mem = {}
    assert policy.gift_step(parse(gifts(gold, 2, house)), mem) == []
    assert mem['bijin_gift_hold'] is True
    assert not mem.get('_records')


def test_rich_request_moves_off_decline_row_before_accepting():
    actions, _ = decide_bijin(bijin_pixels(gold='1ねん 6のつき 1000G', selected=1))
    assert actions == [policy.pad('up')]


def test_public_pixel_path_selects_house_and_does_not_fallback_on_unread_menu():
    from docich.hanjuku_bot import decide, NO_INPUT_HOLD_MAX
    frame = gifts(1000, 2)
    actions, state = decide(frame, {'policy': {'tick': 10}})
    assert actions == [policy.pad('a')]
    assert any(r.get('strategy_variant') == 'bijin_house' for r in state['_records'])
    frame = gifts(999, 2)
    state.update(no_input_frames=[frame.digest()], no_input_streak=NO_INPUT_HOLD_MAX)
    actions, state = decide(frame, state)
    assert actions == []
    assert not any(r.get('decision') == 'no_input_fallback' for r in state['_records'])

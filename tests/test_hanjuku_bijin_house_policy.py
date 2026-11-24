"""Owner 2026-10-21: >=1000G chooses a house; below declines the request."""
import json
import pytest
from docich import hanjuku_policy as policy
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import Canvas
from test_hanjuku_bijin_guard import bijin_pixels, decide_bijin


def gifts(gold=1000, selected=0, house='いえ500G', prompt=True):
    c = Canvas()
    if gold is not None:
        c.text(48, 15, f'1ねん 6のつき {gold}G')
    if prompt:
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


@pytest.mark.parametrize('gold', [999, 1000, 1001])
def test_complete_bijin_menu_routes_without_readable_request(gold):
    from docich.hanjuku_bot import decide, NO_INPUT_HOLD_MAX
    frame = gifts(gold, 2, prompt=False)
    assert parse(frame).kind == 'gift_request'
    state = {'policy': {'tick': 10}, 'no_input_frames': [frame.digest()],
             'no_input_streak': NO_INPUT_HOLD_MAX}
    actions, state = decide(frame, state)
    assert actions == ([] if gold < 1000 else [policy.pad('a')])
    assert not any(r.get('decision') == 'no_input_fallback' for r in state['_records'])


def test_changed_item_set_without_request_is_not_bijin_proof():
    assert parse(gifts(1000, 2, house='コート500G', prompt=False)).kind == 'shop_list'


@pytest.mark.parametrize('gold,house', [(999, 'いえ500G'), (None, 'いえ500G'), (1000, '????500G')])
def test_item_hold_survives_unread_observation_and_json_resume(gold, house):
    from docich.hanjuku_bot import decide, NO_INPUT_HOLD_MAX
    from test_hanjuku_bijin_guard import prose_pixels
    actions, state = decide(gifts(gold, 2, house), {'policy': {'tick': 10}})
    assert actions == [] and state['policy']['bijin_gift_hold'] is True
    state = json.loads(json.dumps(state))
    frame = Canvas((0, 0, 0)).frame()
    state.update(no_input_frames=[frame.digest()], no_input_streak=NO_INPUT_HOLD_MAX)
    for _ in range(3):
        actions, state = decide(frame, state)
        assert actions == [] and state['policy']['bijin_gift_hold'] is True
        assert not any(r.get('decision') == 'no_input_fallback' for r in state['_records'])
    # A positively readable different page releases the episode.
    actions, state = decide(prose_pixels(), state)
    assert 'bijin_gift_hold' not in state['policy']
    assert actions == [policy.pad('a')]


@pytest.mark.parametrize('kind', ['map', 'map_target', 'world_map', 'month_menu',
                                  'battle', 'battle_menu', 'egg_battle_menu', 'monster_menu'])
def test_item_hold_releases_on_explicit_foreground_end(kind):
    from docich.hanjuku_screen import Screen
    mem = {'bijin_gift_hold': True}
    assert policy.bijin_guard_step(Screen([], None, '', kind=kind), mem) is None
    assert 'bijin_gift_hold' not in mem


def test_item_menu_hold_ends_with_the_cheapest_item_after_the_bound():
    # The measured item menu has no refusal row: holding it leaves the frame
    # unchanged until the 300 s corner watchdog ends the run (2026-10-21,
    # end_reason=screen_stalled). The bound ends the event with the cheapest
    # non-house item instead.
    mem = {}
    frame = gifts(999, 2)          # the hand rests on いえ500G
    for _ in range(policy.BIJIN_GIFT_HOLD_MAX):
        assert policy.gift_step(parse(frame), mem) == []
    actions = policy.gift_step(parse(frame), mem)
    assert actions == [policy.pad('up')]      # toward こうすい100G, never いえ
    actions = policy.gift_step(parse(gifts(999, 0)), mem)
    assert actions == [policy.pad('a')]
    record = mem['_records'][-1]
    assert record['strategy_variant'] == 'bijin_hold_bound_cheapest'
    assert (record['item'], record['price']) == ('こうすい', 100)
    assert record['deviation_reason'] == 'bijin_item_hold_bound'
    assert 'bijin_gift_waits' not in mem


# ------------------------------------------------- issue #3011: level 14 cap

@pytest.mark.parametrize('level', [14, 15, 16])
def test_level_cap_declines_the_request_regardless_of_gold(level):
    mem = {'hanjuku_level': level}
    actions, state = decide_bijin(bijin_pixels(gold='1ねん 6のつき 2000G'), {'policy': dict(mem)})
    assert actions == [policy.pad('down')]
    actions, state = decide_bijin(bijin_pixels(gold='1ねん 6のつき 2000G', selected=1), state)
    assert actions == [policy.pad('a')]
    assert state['_records'][-1]['strategy_variant'] == 'decline_bijin_level_cap'


@pytest.mark.parametrize('level', [None, 0, 13])
def test_below_the_level_cap_keeps_buying_the_house(level):
    mem = {} if level is None else {'hanjuku_level': level}
    for selected in (0, 1):
        assert policy.gift_step(parse(gifts(2000, selected)), mem) == [policy.pad('down')]
    assert policy.gift_step(parse(gifts(2000, 2)), mem) == [policy.pad('a')]
    assert mem['_records'][-1]['strategy_variant'] == 'bijin_house'
    assert mem['_records'][-1]['item'] == 'いえ'


def test_level_cap_gift_menu_buys_the_cheapest_non_house_item():
    # The item menu has no refusal row: at the cap the event ends with the
    # cheapest non-earning item instead of いえ.
    mem = {'hanjuku_level': 14}
    assert policy.gift_step(parse(gifts(2000, 2)), mem) == [policy.pad('up')]
    assert policy.gift_step(parse(gifts(2000, 1)), mem) == [policy.pad('up')]
    assert policy.gift_step(parse(gifts(2000, 0)), mem) == [policy.pad('a')]
    record = mem['_records'][-1]
    assert record['strategy_variant'] == 'bijin_level_cap'
    assert (record['item'], record['price']) == ('こうすい', 100)
    assert record['observed_metric']['hanjuku_level'] == 14

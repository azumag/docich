"""An unverified boss kit must cancel, never approve or hold its UI forever.

The native fixture is historical public calibration; current generation839
has only a diagnostics card_select/no-input pattern, not this captured panel.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku_bot import decide, NO_INPUT_HOLD_MAX
from docich.hanjuku_pixels import Frame, read_png
from docich.hanjuku_screen import parse, Screen
from test_hanjuku_sortie_evidence import memory, measured_card_select

ID = {'game': 'hanjuku-hero', 'runtime_id': 'card-hold-fixture', 'generation': 1, 'lease_id': 'fixture-lease'}


def unread_native(cause='hand', change=0):
    f = read_png(Path(__file__).parent / 'fixtures/hanjuku/g706-resume-card-select.png')
    rgb = bytearray(f.rgb)
    rect = (138, 97, 160, 111) if cause == 'hand' else (232, 55, 240, 63)
    for y in range(rect[1], rect[3]):
        for x in range(rect[0], rect[2]):
            start = (y * f.width + x) * 3
            rgb[start:start + 3] = bytes((16, 72, 57))
    rgb[:3] = bytes((change, 0, 0))
    return Frame(f.width, f.height, bytes(rgb))


def native_state():
    mem = memory()
    mem['sortie_general']['1-B1'] = 'シェーブル'
    mem.update(_run_identity=dict(ID), survey_run_identity=dict(ID))
    return {'policy': mem}


def boss_order():
    return next(o for o in p.chart.orders(1) if o['step'] == '1-B1')


@pytest.mark.parametrize('cause', ['hand', 'quantity'])
def test_public_decide_native_unread_boss_panel_cancels_with_kit_requirements_intact(cause):
    state = native_state(); before = p._deploy_cards(boss_order(), state['policy'])
    for n in range(p.CARD_UNREADABLE_LIMIT):
        frame = unread_native(cause, n)
        assert parse(frame).kind == 'card_select' and p._measured_card_select(parse(frame)) is None
        actions, state = decide(frame, json.loads(json.dumps(state)), run_identity=ID)
        assert actions == ([] if n + 1 < p.CARD_UNREADABLE_LIMIT else [p.pad('b'), p.pad('b')])
    mem = state['policy']
    assert mem['active'] is None and mem['orders']['1-B1'] == 'pending'
    assert not mem['picked'] and not mem.get('card_drop') and not mem.get('kit_spent')
    assert p._deploy_cards(next(o for o in p.chart.orders(1) if o['step'] == '1-B1'), mem) == before
    assert not mem.get('sorties') and not mem.get('order_context')
    for _ in range(3):
        actions, state = decide(frame, state, run_identity=ID)
        assert actions == [p.pad('b')]  # an unclosed menu never gets A after the cancellation


@pytest.mark.parametrize('case', ['full', 'offscreen', 'alternating'])
def test_strict_unverified_stock_or_capacity_has_one_budget_across_json_and_changed_reads(case):
    mem = memory()
    unavailable = measured_card_select(('クースカン', 'ノリウツール', 'ブラッキー', 'バルムンク'),
                                      remaining='0')
    if case == 'offscreen':
        unavailable = measured_card_select(('イッテツーン', 'ダイチスイム', 'ブラッキー', 'フットバース'))
        mem['card_scroll'] = {'1-B1': p.CARD_SCROLL_LIMIT}
    wanted = deepcopy(p._deploy_cards(boss_order(), mem))
    for i in range(p.CARD_UNREADABLE_LIMIT):
        screen = parse(unread_native()) if case == 'alternating' and i % 2 else unavailable
        mem = json.loads(json.dumps(mem))
        if case == 'alternating': mem['sortie_general']['1-B1'] = 'シェーブル'
        # Structured observations share the real native actor for this mixed sequence.
        if case == 'alternating' and i % 2 == 0:
            from test_hanjuku_sortie_evidence import actor_panel
            screen = actor_panel(screen, 'シェーブル')
        actions = p.deploy_step(screen, mem)
        assert actions == ([] if i + 1 < p.CARD_UNREADABLE_LIMIT else [p.pad('b'), p.pad('b')])
    assert mem['orders']['1-B1'] == 'pending' and mem['active'] is None
    assert not mem.get('card_drop') and not mem.get('kit_spent')
    assert p._deploy_cards(boss_order(), mem) == wanted


def test_readable_live_required_card_at_the_deadline_still_uses_normal_selection():
    mem = memory(); unread = parse(unread_native())
    mem['sortie_general']['1-B1'] = 'シェーブル'
    for _ in range(p.CARD_UNREADABLE_LIMIT - 1):
        assert p.deploy_step(unread, mem) == []
    from test_hanjuku_sortie_evidence import actor_panel
    live = actor_panel(measured_card_select(('クースカン', 'ノリウツール', 'ブラッキー', 'バルムンク')),
                       'シェーブル')
    assert p.deploy_step(live, mem) == [p.pad('a')]
    assert mem['picked'] == ['クースカン'] and mem['active'] == '1-B1'


@pytest.mark.parametrize('saved', [None, True, -1, '5', {'1-B1': True}, {'1-B1': -1}, {'1-B1': 1.0}])
def test_malformed_saved_budget_fails_closed_without_restoring_waits(saved):
    mem = native_state()['policy']; mem['boss_card_hold'] = saved
    if saved is None:
        # An absent newly introduced budget is valid, a saved noninteger counter is not.
        mem['boss_card_hold'] = {'1-B1': None}
    assert p.deploy_step(parse(unread_native()), mem) == [p.pad('b'), p.pad('b')]
    assert mem['active'] is None and not mem.get('card_drop')


def test_requeued_intent_cannot_refresh_an_exhausted_ui_until_observed_backout():
    mem = native_state()['policy']; screen = parse(unread_native())
    for _ in range(p.CARD_UNREADABLE_LIMIT): p.deploy_step(screen, mem)
    for _ in range(4):
        mem = json.loads(json.dumps(mem))
        mem.update(active='1-B1', picked=[])
        mem['sortie_general'] = {'1-B1': 'シェーブル'}
        assert p.deploy_step(screen, mem) == [p.pad('b')]
        assert mem['boss_card_hold']['1-B1'] == p.CARD_UNREADABLE_LIMIT
    p.deploy_step(Screen([], None, '', kind='castle_menu'), mem)
    assert not mem.get('boss_card_hold')
    mem.update(active='1-B1'); mem['sortie_general'] = {'1-B1': 'シェーブル'}
    assert p.deploy_step(screen, mem) == []
    assert mem['boss_card_hold']['1-B1'] == 1


def test_missing_selected_actor_cache_has_the_same_finite_hold_without_borrowing_the_plan():
    mem = memory(); mem.pop('sortie_general')
    mem.update(_run_identity=dict(ID), general_override={'1-B1': 'ゼウス'})
    screen = measured_card_select()
    for i in range(p.CARD_UNREADABLE_LIMIT):
        mem = json.loads(json.dumps(mem))
        assert p.deploy_step(screen, mem) == ([] if i + 1 < p.CARD_UNREADABLE_LIMIT else [p.pad('b'), p.pad('b')])
    assert not mem.get('picked') and not mem.get('card_drop')


def cancelled_state():
    state = native_state()
    for i in range(p.CARD_UNREADABLE_LIMIT):
        _, state = decide(unread_native(change=i % 3), json.loads(json.dumps(state)), run_identity=ID)
    assert state['policy']['boss_stock_restart']['cancel_only'] is True
    return state


def test_inherited_frozen_streak_cannot_override_the_local_read_budget():
    frame = unread_native(); state = native_state()
    state.update(no_input_streak=NO_INPUT_HOLD_MAX + 1, no_input_frames=[frame.digest()])
    actions, state = decide(frame, state, run_identity=ID)
    assert actions == [] and state['policy']['boss_card_hold']['1-B1'] == 1
    assert not state['policy'].get('picked')


def test_unaccepted_b_partial_confirm_and_fades_keep_cancellation_owner_without_a():
    from test_hanjuku_owned_sortie_backout import confirmation
    from test_hanjuku_chart_bot import Canvas
    state = cancelled_state()
    frames = [confirmation(True), Canvas((0, 0, 0)).frame(), unread_native()]
    for i in range(18):
        frame = frames[i % 3]
        actions, state = decide(frame, json.loads(json.dumps(state)), run_identity=ID)
        assert not any('a' in action.get('buttons', []) for action in actions)
        assert state['policy'].get('boss_stock_restart') or state['policy'].get('boss_stock_cancelled')
    for frame in frames:
        state.update(no_input_streak=NO_INPUT_HOLD_MAX + 1, no_input_frames=[frame.digest()])
        actions, state = decide(frame, json.loads(json.dumps(state)), run_identity=ID)
        assert actions == [] and state['policy']['boss_stock_cancelled']
    assert not state['policy'].get('sorties') and not state['policy'].get('kit_spent')


@pytest.mark.parametrize('kind', ['battle', 'battle_menu', 'month_menu', 'shop_quantity_prompt', 'yes_no'])
def test_cancel_only_owner_yields_to_unrelated_foregrounds_without_spending_its_budget(kind):
    mem = cancelled_state()['policy']; before = deepcopy(mem)
    assert p.boss_stock_restart_step(Screen([], None, 'べつのイベント', kind=kind), mem) is None
    assert mem == before


def test_a_different_active_order_does_not_get_the_old_cancellation_owner():
    mem = cancelled_state()['policy']; mem['active'] = '1-A2'
    assert p.boss_stock_restart_step(parse(unread_native()), mem) is None
    assert not mem.get('boss_stock_restart') and mem['active'] == '1-A2'


@pytest.mark.parametrize('capped', [False, True])
def test_another_run_cannot_inherit_the_cancel_only_owner_or_its_hold(capped):
    mem = cancelled_state()['policy']; screen = parse(unread_native())
    if capped:
        for _ in range(12): p.boss_stock_restart_step(screen, mem)
        assert mem['boss_stock_cancelled']
    mem['_run_identity'] = {**ID, 'lease_id': 'new-run'}
    assert p.boss_stock_restart_step(screen, mem) is None
    assert not mem.get('boss_stock_restart') and not mem.get('boss_stock_cancelled')


@pytest.mark.parametrize('capped', [False, True])
def test_free_map_completes_cancel_before_a_new_survey_world_map(capped):
    from test_hanjuku_off_chart import FRAME, map_screen, _world_map_frame
    mem = cancelled_state()['policy']
    if capped:
        for _ in range(12): p.boss_stock_restart_step(parse(unread_native()), mem)
    # Follow the public flow dispatch continuously: B completed, then normal
    # map_step opens Y. That new view belongs to survey, not the old cancel.
    screen = map_screen(100, 100)
    actions = p.boss_stock_restart_step(screen, mem)
    if actions is None: actions = p.map_step(screen, mem, FRAME)
    assert actions == [p.pad('y')]
    frame = _world_map_frame({'アルマムーン': 'own'})
    for _ in range(14):
        mem = json.loads(json.dumps(mem))
        actions = p.boss_stock_restart_step(parse(frame), mem)
        if actions is None: actions = p.world_map_step(parse(frame), mem, frame)
        assert actions == [p.pad('y')]
        assert not mem.get('boss_stock_restart') and not mem.get('boss_stock_cancelled')
    assert not mem.get('boss_card_hold') and not mem.get('boss_card_hold_scope')


@pytest.mark.parametrize('capped', [False, True])
def test_public_decide_cancel_then_free_map_then_survey_through_json(capped):
    from test_hanjuku_off_chart import _world_map_frame
    state = cancelled_state()
    if capped:
        for _ in range(12):
            _, state = decide(unread_native(), json.loads(json.dumps(state)), run_identity=ID)
    # Existing public chapter-2 map texture supplies only the measured free
    # field/cursor UI here, not evidence for this chapter-1 synthetic state.
    field = read_png(Path(__file__).parent / 'fixtures/hanjuku/resume-chapter-2-map.png')
    assert parse(field).kind == 'map' and parse(field).marker is None
    actions, state = decide(field, json.loads(json.dumps(state)), run_identity=ID)
    assert actions == [p.pad('y')]
    view = _world_map_frame({'アルマムーン': 'own'})
    for _ in range(14):
        actions, state = decide(view, json.loads(json.dumps(state)), run_identity=ID)
        assert actions == [p.pad('y')]
        assert not state['policy'].get('boss_stock_restart')
        assert not state['policy'].get('boss_stock_cancelled')
    assert not state['policy'].get('boss_card_hold')


@pytest.mark.parametrize('kind', ['card_select', 'general_list', 'world_map', 'sortie_confirm', 'unknown'])
def test_capped_cancel_cannot_hold_a_different_active_order(kind):
    mem = cancelled_state()['policy']
    for _ in range(12): p.boss_stock_restart_step(parse(unread_native()), mem)
    assert mem['boss_stock_cancelled'] and mem['active'] is None
    mem = json.loads(json.dumps(mem)); mem['active'] = '1-A2'
    for _ in range(14):
        assert p.boss_stock_restart_step(Screen([], None, '', kind=kind), mem) is None
        assert not mem.get('boss_stock_cancelled') and not mem.get('boss_stock_restart')
        assert mem['active'] == '1-A2'


def test_capped_cancel_is_bound_to_the_chapter_as_well_as_the_run():
    mem = cancelled_state()['policy']
    for _ in range(12): p.boss_stock_restart_step(parse(unread_native()), mem)
    mem['chapter'] = 2
    assert p.boss_stock_restart_step(parse(unread_native()), mem) is None
    assert not mem.get('boss_stock_cancelled')


@pytest.mark.parametrize('kind', ['card_select', 'world_map'])
def test_public_decide_capped_owner_yields_to_another_order_like_an_unowned_view(kind):
    from test_hanjuku_off_chart import _world_map_frame
    state = cancelled_state()
    for _ in range(12):
        _, state = decide(unread_native(), json.loads(json.dumps(state)), run_identity=ID)
    state['policy']['active'] = '1-A2'
    baseline = deepcopy(state); baseline['policy'].pop('boss_stock_cancelled')
    frame = unread_native() if kind == 'card_select' else _world_map_frame({'アルマムーン': 'own'})
    expected, _ = decide(frame, baseline, run_identity=ID)
    actions, state = decide(frame, json.loads(json.dumps(state)), run_identity=ID)
    assert expected and actions == expected
    assert not state['policy'].get('boss_stock_cancelled')


@pytest.mark.parametrize('kind', ['card_select', 'general_list', 'world_map'])
def test_legacy_unscoped_latch_cannot_claim_a_new_selection_or_survey(kind):
    mem = memory(); mem.update(boss_stock_cancelled=True, active='1-A2')
    assert p.boss_stock_restart_step(Screen([], None, '', kind=kind), mem) is None
    assert not mem.get('boss_stock_cancelled') and mem['active'] == '1-A2'

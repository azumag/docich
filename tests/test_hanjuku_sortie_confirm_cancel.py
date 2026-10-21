"""Bounded refusal contracts, not proof of a live NO-key transition.

The upper cursor/choice geometry is from the existing native g700 fixture.
The lower cursor, damaged readings and subsequent screens below are synthetic
Screen observations. No emulator, transport, ROM or live input is used.
"""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from docich.hanjuku.flows import deploy
from docich.hanjuku import orchestrator
from docich.hanjuku.os_layer.font import TextLine, UNKNOWN
from docich.hanjuku.os_layer.pixels import Frame, read_png
from docich.hanjuku.os_layer.screen import Screen, parse
from docich.hanjuku.os_layer.commands import pad
from test_hanjuku_sortie_evidence import memory, actor_panel, measured_loaded_sortie

FIXTURE = Path(__file__).parent / 'fixtures/hanjuku/g700-balmung-kit-confirm.png'
IDENTITY = {'game': 'hanjuku-hero', 'runtime_id': 'g889-1234abcd',
            'generation': 889, 'lease_id': 'test-lease'}


def panel(on='yes'):
    screen = actor_panel(parse(read_png(FIXTURE)), 'ゼウス')
    # Only this cursor displacement is synthetic, not a new native receipt.
    screen.hand = ((138, 105, 156, 118) if on == 'yes' else
                   (138, 121, 156, 134) if on == 'no' else None)
    return screen


def cancelled():
    mem = memory()
    mem.update(active=None, tick=100, _run_identity=deepcopy(IDENTITY))
    mem['boss_stock_cancelled'] = {'step': '1-B1', 'chapter': 1, 'identity': deepcopy(IDENTITY)}
    return mem


def observe(screen, mem):
    mem['tick'] += 1
    return deploy.boss_stock_restart_step(screen, mem)


def replace(screen, y, x, text):
    line = next(row for row in screen.lines if row.y == y)
    cells = tuple((cx, ch) for cx, ch in line.cells if not x <= cx < x + 8 * len(text)) + tuple(
        (x + 8 * i, ch) for i, ch in enumerate(text))
    screen.lines = [TextLine(y, tuple(sorted(cells))) if row.y == y else row for row in screen.lines]


def test_native_panel_recognizes_no_but_does_not_prove_its_effect():
    screen = parse(read_png(FIXTURE))
    assert screen.selected == 'うむッ!'
    assert screen.hand == (138, 105, 156, 118)
    assert deploy._sortie_decline_panel(screen)
    assert deploy.menu_to(screen, 'いかんッ!') == pad('down')


def test_strict_mismatch_is_bounded_without_dropping_stock_or_spending():
    mem = memory()
    mem.update(tick=0, card_stock={'クースカン': 2}, card_drop={'other': ['ブラッキー']},
               kit_spent={'other': ['イッテツーン']},
               month_sub={'kind': 'recruit', 'paid': True, 'gold_before': 200})
    before = deepcopy({key: mem[key] for key in ('card_stock', 'card_drop', 'kit_spent', 'month_sub')})
    screen = panel()  # native event-card kit is not the chapter-one boss kit
    for _ in range(deploy.SORTIE_CONFIRM_LIMIT - 1):
        mem['tick'] += 1
        assert deploy.deploy_step(screen, mem) == []
    mem['tick'] += 1
    assert deploy.deploy_step(screen, mem) == [pad('down')]
    assert mem['active'] is None and mem['orders']['1-B1'] == 'pending'
    assert mem['boss_stock_cancelled']['decline_reads'] == 1
    assert {key: mem[key] for key in before} == before
    assert not any(r['decision'] == 'sortie_confirm' for r in mem['_records'])


def test_direction_then_later_read_no_allows_one_refusal_a_not_departure():
    mem = cancelled()
    assert observe(panel(), mem) == [pad('down')]
    assert observe(panel('no'), mem) == [pad('a')]
    assert mem['boss_stock_cancelled']['decline_sent'] is True
    record = mem['_records'][-1]
    assert record['decision'] == 'sortie_cancel_requested'
    assert record['observed_metric']['choice'] == 'いかんッ!'
    assert record['resulting_event'] == 'refusal_planned_not_yet_confirmed'
    for _ in range(20):
        assert observe(panel('no'), mem) == []
    assert mem.get('boss_stock_cancelled') and not mem.get('sortie_attempt')
    assert mem['orders']['1-B1'] == 'pending' and not mem.get('sorties')


def test_first_no_read_and_same_observation_replay_do_not_confirm():
    mem = cancelled()
    assert observe(panel('no'), mem) == []
    assert deploy.boss_stock_restart_step(panel('no'), mem) == []
    assert observe(panel('no'), mem) == []
    assert observe(panel(), mem) == [pad('down')]
    assert observe(panel('no'), mem) == [pad('a')]


@pytest.mark.parametrize('change', ['hand_missing', 'hand_wrong_row', 'prompt_missing',
    'yes_missing', 'no_missing', 'no_unknown', 'suffix_unknown', 'carry_heading', 'status_heading'])
def test_incomplete_or_ambiguous_reading_revokes_previous_refusal_read(change):
    mem = cancelled()
    assert observe(panel(), mem) == [pad('down')]
    damaged = panel('no')
    if change == 'hand_missing': damaged.hand = None
    elif change == 'hand_wrong_row': damaged.hand = (138, 89, 156, 102)
    elif change == 'prompt_missing': replace(damaged, 95, 136, UNKNOWN)
    elif change == 'yes_missing': damaged.lines = [l for l in damaged.lines if l.y != 111]
    elif change == 'no_missing': damaged.lines = [l for l in damaged.lines if l.y != 127]
    elif change == 'no_unknown': replace(damaged, 127, 160, UNKNOWN)
    elif change == 'suffix_unknown': replace(damaged, 127, 208, UNKNOWN)
    elif change == 'carry_heading': replace(damaged, 47, 136, UNKNOWN)
    elif change == 'status_heading': replace(damaged, 31, 80, UNKNOWN)
    assert observe(damaged, mem) == []
    assert 'decline_ready_tick' not in mem['boss_stock_cancelled']
    assert observe(panel('no'), mem) == []
    assert observe(panel(), mem) == [pad('down')]
    assert observe(panel('no'), mem) == [pad('a')]


@pytest.mark.parametrize('kind', ['yes_no', 'text', 'unknown'])
def test_only_full_measured_layout_may_decline_under_a_generic_kind(kind):
    mem = cancelled(); screen = panel(); screen.kind = kind
    assert observe(screen, mem) == [pad('down')]
    screen = panel('no'); screen.kind = kind
    assert observe(screen, mem) == [pad('a')]


@pytest.mark.parametrize('kind', ['battle', 'battle_menu', 'month_menu', 'shop_quantity_prompt', 'yes_no'])
def test_unrelated_payment_and_battle_keep_their_own_receipts(kind):
    mem = cancelled(); mem['month_sub'] = {'kind': 'recruit', 'paid': True, 'receipt': {'amount': 50}}
    before = deepcopy(mem)
    screen = Screen([], None, '50Gうむッ!いかんッ!', kind=kind)
    assert deploy.boss_stock_restart_step(screen, mem) is None
    assert mem == before


@pytest.mark.parametrize('change', ['run', 'lease', 'chapter', 'active', 'missing_identity'])
def test_stale_cancel_owner_never_drives_a_new_foreground(change):
    mem = cancelled()
    if change == 'run': mem['_run_identity']['runtime_id'] = 'g890-1234abcd'
    elif change == 'lease': mem['_run_identity']['lease_id'] = 'another-lease'
    elif change == 'chapter': mem['chapter'] = 2
    elif change == 'active': mem['active'] = '1-C1'
    else: del mem['boss_stock_cancelled']['identity']
    assert observe(panel('no'), mem) is None
    assert not mem.get('boss_stock_cancelled')


def test_budget_survives_json_and_reading_changes_without_infinite_direction_or_b():
    mem = cancelled(); planned = []
    for i in range(50):
        mem = json.loads(json.dumps(mem))
        screen = panel('yes' if i % 3 else None)
        planned += observe(screen, mem)
    assert len(planned) <= deploy.SORTIE_CANCEL_READ_LIMIT
    assert all(a == pad('down') for a in planned)
    assert mem['boss_stock_cancelled']['decline_reads'] == deploy.SORTIE_CANCEL_READ_LIMIT
    assert not any('a' in a.get('buttons', []) for a in planned)


@pytest.mark.parametrize('kind', ['general_list', 'card_select'])
def test_after_refusal_intermediate_sortie_menu_requests_only_bounded_backout(kind):
    mem = cancelled()
    assert observe(panel(), mem) == [pad('down')]
    assert observe(panel('no'), mem) == [pad('a')]
    for _ in range(deploy.SORTIE_CANCEL_READ_LIMIT - 2):
        assert observe(Screen([], None, '', kind=kind), mem) == [pad('b')]
    assert observe(Screen([], None, '', kind=kind), mem) == []
    assert mem.get('boss_stock_cancelled')


@pytest.mark.parametrize('kind', ['map', 'castle_menu'])
def test_owner_is_not_cleared_by_a_planned_refusal_only_an_observed_backout(kind):
    mem = cancelled(); observe(panel(), mem); observe(panel('no'), mem)
    assert mem.get('boss_stock_cancelled')
    assert observe(Screen([], None, '', kind=kind), mem) is None
    assert not mem.get('boss_stock_cancelled')
    assert mem['orders']['1-B1'] == 'pending'
    assert mem['_records'][-1]['decision'] == 'sortie_cancel_backout_observed'


def test_confirmed_normal_boss_kit_keeps_existing_departure_permission():
    mem = memory()
    screen = measured_loaded_sortie(('クースカン', 'ノリウツール'))
    assert deploy.deploy_step(screen, mem) == [pad('a')]
    assert mem['_records'][-1]['decision'] == 'sortie_confirm'
    assert not mem.get('boss_stock_cancelled')


@pytest.mark.parametrize('status', ['launched', 'launched_unconfirmed'])
def test_unconfirmed_sent_sortie_never_becomes_pending_or_loses_receipts(status):
    mem = memory(); mem['orders']['1-B1'] = status
    mem['sorties'] = {'1-B1': {'status': status, 'general': 'ゼウス'}}
    receipt = deepcopy(mem['sorties'])
    for _ in range(20): assert deploy.deploy_step(panel(), mem) == []
    assert mem['orders']['1-B1'] == status and mem['sorties'] == receipt
    assert not mem.get('boss_stock_cancelled')


def test_no_inventory_success_is_required_when_refusal_cursor_moves_to_no():
    screen = panel('no')
    assert deploy._sortie_inventory(screen) is None
    mem = cancelled(); observe(panel(), mem)
    assert observe(screen, mem) == [pad('a')]


def test_full_decide_cancels_with_changing_rgb_and_does_not_use_generic_a():
    mem = memory(); mem.update(name={'done': True}, month='2-12')
    state = {'policy': mem, 'phase': 'field_menu', 'screen_kind': 'sortie_confirm'}
    with patch.object(orchestrator, 'classify', return_value='field_menu'), patch(
            'docich.hanjuku.os_layer.screen.parse', return_value=panel()):
        for i in range(deploy.SORTIE_CONFIRM_LIMIT):
            frame = Frame(256, 224, bytes([i + 1]) * (256 * 224 * 3))
            actions, state = orchestrator.decide(frame, json.loads(json.dumps(state)))
    assert actions == [pad('down')]
    assert state['policy'].get('boss_stock_cancelled')
    assert not any(r['decision'] == 'sortie_confirm' for r in state['_records'])


@pytest.mark.parametrize('change', ['actor', 'tick_gap', 'same_tick', 'missing_tick'])
def test_refusal_a_needs_previous_direction_and_fresh_same_actor_readback(change):
    mem = cancelled(); assert observe(panel(), mem) == [pad('down')]
    no = panel('no')
    if change == 'actor': no = actor_panel(no, 'ヴィーナス')
    elif change == 'tick_gap': mem['tick'] += 50
    elif change == 'same_tick': mem['tick'] -= 1
    else: del mem['tick']
    if change == 'missing_tick':
        assert deploy.boss_stock_restart_step(no, mem) == []
    else:
        assert observe(no, mem) == []
    assert not mem['boss_stock_cancelled'].get('decline_sent')


@pytest.mark.parametrize('kind', ['yes_no', 'text'])
@pytest.mark.parametrize('missing_y', [111, 127])
def test_owned_choice_fragment_never_falls_to_a_generic_yes_no_a(kind, missing_y):
    mem = cancelled(); assert observe(panel(), mem) == [pad('down')]
    screen = panel('no'); screen.kind = kind
    screen.lines = [l for l in screen.lines if l.y != missing_y]
    assert observe(screen, mem) == []
    assert not mem['boss_stock_cancelled'].get('decline_sent')
    assert 'decline_ready_tick' not in mem['boss_stock_cancelled']


@pytest.mark.parametrize('change', ['step_missing', 'step_empty', 'chapter_bool', 'reads_bad'])
def test_malformed_owner_never_authorizes_a(change):
    mem = cancelled(); owner = mem['boss_stock_cancelled']
    if change == 'step_missing': del owner['step']
    elif change == 'step_empty': owner['step'] = ''
    elif change == 'chapter_bool': owner['chapter'] = True
    else: owner['decline_reads'] = 'invalid'
    result = observe(panel(), mem)
    assert result in (None, [])
    assert not mem.get('boss_stock_cancelled', {}).get('decline_sent')


@pytest.mark.parametrize('phase', ['field', 'field_menu', 'dialogue', 'transition'])
def test_hotloaded_high_no_input_streak_cannot_override_a_new_cancel_owner(phase):
    mem = memory(); mem.update(name={'done': True}, month='2-12',
        sortie_confirm_miss={'1-B1': 100})
    frame = Frame(256, 224, bytes([1]) * (256 * 224 * 3))
    screen = panel(None)
    state = {'policy': mem, 'phase': phase, 'screen_kind': 'sortie_confirm',
             'no_input_streak': orchestrator.NO_INPUT_HOLD_MAX + 1,
             'no_input_frames': [frame.digest()]}
    with patch.object(orchestrator, 'classify', return_value=phase), patch(
            'docich.hanjuku.os_layer.screen.parse', return_value=screen):
        actions, state = orchestrator.decide(frame, state)
    assert actions == [] and state['policy'].get('boss_stock_cancelled')
    assert not any(r['decision'] in {'sortie_confirm', 'no_input_fallback'} for r in state['_records'])


@pytest.mark.parametrize('hand', [(78, 121, 96, 134), (118, 121, 136, 134),
    (138, 121, 176, 134), (138, 121, 156, 145), (137, 121, 156, 134),
    (138, 120, 156, 134), (138, 121, 157, 134), (138, 121, 156, 133)])
def test_wrong_x_or_size_hand_cannot_confirm_a_distant_refusal_label(hand):
    mem = cancelled(); assert observe(panel(), mem) == [pad('down')]
    screen = panel('no'); screen.hand = hand
    assert observe(screen, mem) == []
    assert not mem['boss_stock_cancelled'].get('decline_sent')
    assert 'decline_ready_tick' not in mem['boss_stock_cancelled']

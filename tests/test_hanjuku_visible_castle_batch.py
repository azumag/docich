"""Batch only the visible path to an explicitly requested castle row."""
from copy import deepcopy
from dataclasses import replace
import json
from unittest.mock import patch

import pytest

from docich.actions import parse_actions
from docich.agent.loop import _run_iteration
from docich.hanjuku.os_layer import commands
from docich.hanjuku.os_layer.font import UNKNOWN
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import parse
from test_hanjuku_castle_jump import (
    CHAPTER_6_LONG, M, _castle_list, _jump, _memory, _order,
)
from test_hanjuku_house import roster
from test_hanjuku_menu_cursor_tempo import entry, state
from test_agent_loop import FakeAdapter, FakeBrain


NAMES = CHAPTER_6_LONG[:8]
RELEASE = {'type': 'wait', 'ms': 50}


def taps(button, count):
    actions = [commands.pad(button)]
    for _ in range(count - 1):
        actions += [dict(RELEASE), commands.pad(button)]
    return actions


def memory(target):
    mem = _memory(_order(source=target))
    mem['castle_jump'] = _jump('list', menu_presses=2, source=target)
    return mem


@pytest.mark.parametrize('selected,target,count', [(0, 2, 2), (0, 3, 3), (0, 7, 4),
                                                  (7, 0, 4), (4, 6, 2), (6, 4, 2)])
def test_visible_castle_path_uses_bounded_taps_with_a_release_between_them(selected, target, count):
    screen = _castle_list(NAMES, hand_y=39 + 16 * selected)
    mem = memory(NAMES[target])
    actions = M.castle_jump_step(screen, mem, None)
    direction = 'down' if target > selected else 'up'
    assert actions == taps(direction, count)
    assert mem['castle_jump']['phase'] == 'list'
    observed = state(screen)
    before = deepcopy((observed, actions))
    assert entry.observation_interval_ms(observed, actions) == 500
    assert (observed, actions) == before


def test_seven_visible_rows_take_two_movement_observations_then_a_fresh_select():
    mem = memory(NAMES[-1])
    moves = []
    for selected, count in [(0, 4), (4, 3)]:
        screen = _castle_list(NAMES, hand_y=39 + 16 * selected)
        actions = M.castle_jump_step(screen, mem, None)
        assert actions == taps('down', count)
        moves.extend(action for action in actions if action['type'] == 'pad')
        assert mem['castle_jump']['phase'] == 'list'
    assert moves == [commands.pad('down')] * 7
    final = _castle_list(NAMES, hand_y=151)
    assert M.castle_jump_step(final, mem, None) == [commands.pad('select')]
    assert mem['castle_jump']['phase'] == 'confirm'


@pytest.mark.parametrize('landed', [0, 2, 6])
def test_missing_or_partial_movement_never_confirms_the_planned_landing(landed):
    mem = memory(NAMES[-1])
    assert M.castle_jump_step(_castle_list(NAMES), mem, None) == taps('down', 4)
    # The sender/game may have accepted fewer inputs. Read the actual hand,
    # rather than advancing a predicted cursor by the number of planned taps.
    screen = _castle_list(NAMES, hand_y=39 + 16 * landed)
    actions = M.castle_jump_step(screen, mem, None)
    remaining = min(4, 7 - landed)
    expected = taps('down', remaining) if remaining > 1 else [commands.pad('down')]
    assert actions == expected
    assert mem['castle_jump']['phase'] == 'list'


@pytest.mark.parametrize('change', ['gap', 'unknown', 'duplicate_target', 'wrong_kind', 'general_roster'])
def test_incomplete_or_foreign_visible_paths_keep_the_existing_single_step(change):
    names = list(NAMES)
    if change == 'duplicate_target':
        names[2] = names[4]
    elif change == 'general_roster':
        names = ['どうし', 'ゼウス', 'ヴィーナス', 'ココット', NAMES[4]]
    screen = _castle_list(names)
    if change == 'gap':
        screen.lines = [line for line in screen.lines if line.y != 71]
    elif change == 'unknown':
        screen.lines = [replace(line, cells=line.cells + ((160, UNKNOWN),))
                        if line.y == 71 else line for line in screen.lines]
    elif change == 'wrong_kind':
        # The original flow still sees both help strings in screen.text;
        # the stricter semantic reader must not authorize a batch.
        screen.kind = 'unknown'
    assert M.castle_jump_step(screen, memory(NAMES[4]), None) == [commands.pad('down')]


def test_a_gap_beyond_the_four_tap_cap_still_prevents_a_batch():
    screen = _castle_list(NAMES)
    screen.lines = [line for line in screen.lines if line.y != 135]
    assert M.castle_jump_step(screen, memory(NAMES[-1]), None) == [commands.pad('down')]


def test_hidden_target_keeps_single_row_scrolling_and_its_original_budget():
    mem = memory(CHAPTER_6_LONG[-1])
    screen = _castle_list(NAMES)
    assert M.castle_jump_step(screen, mem, None) == [commands.pad('down')]
    assert mem['castle_jump']['scrolls'] == 1
    assert mem['castle_jump']['phase'] == 'list'


def test_real_glyph_pixels_supply_the_visible_path_without_changing_menu_metadata():
    screen = parse(roster(names=tuple(NAMES), selected=0))
    observed = state(screen)
    assert observed['menu_navigation_reading']['menu'] == 'castle_list'
    before = deepcopy(screen)
    assert M.castle_jump_step(screen, memory(NAMES[4]), None) == taps('down', 4)
    assert screen == before


def test_the_action_plan_keeps_every_release_and_the_source_pixels(tmp_path):
    frame = roster(names=tuple(NAMES), selected=0)
    screen = parse(frame)
    observed = state(screen)
    actions = M.castle_jump_step(screen, memory(NAMES[4]), None)
    identity = {'game': 'hanjuku-hero', 'runtime_id': 'test-runtime',
                'generation': 1, 'lease_id': 'test-lease'}
    with patch('docich.hanjuku_scene.observe'), patch('docich.hanjuku_progress.record'):
        entry.persist(tmp_path, observed, [], {'hanjuku': identity}, actions=actions,
                      frame_sha256=frame.digest(), frame=frame)
    rows = [json.loads(row) for row in (tmp_path / 'hanjuku_decisions.jsonl').read_text().splitlines()]
    plan = next(row for row in rows if row['event'] == 'action_plan')
    assert plan['planned_actions'] == taps('down', 4)
    assert plan['observation_interval_ms'] == 500
    assert plan['dispatch_status'] == 'planned_not_yet_sent'
    assert read_png(tmp_path / 'hanjuku_frames' / plan['snapshot']).digest() == frame.digest()


def test_the_existing_sender_executes_all_taps_and_release_waits_in_order():
    screen = _castle_list(NAMES)
    actions = M.castle_jump_step(screen, memory(NAMES[4]), None)
    adapter = FakeAdapter()
    sent = []
    adapter.act = lambda action: sent.append(action.buttons[0])
    with patch('docich.agent.loop.time.sleep', side_effect=lambda seconds: sent.append(seconds)):
        assert _run_iteration(adapter, FakeBrain(parse_actions(actions)), 1500) == 7
    assert sent == ['down', 0.05, 'down', 0.05, 'down', 0.05, 'down']


@pytest.mark.parametrize('actions', [
    [commands.pad('down')] * 2, taps('down', 5),
    [commands.pad('down'), RELEASE, commands.pad('up')],
    [commands.pad('down'), RELEASE, commands.pad('select')],
    [commands.pad('down'), {'type': 'wait', 'ms': 0}, commands.pad('down')],
    [commands.pad('down'), {'type': 'wait', 'ms': 50.0}, commands.pad('down')],
    [commands.pad('down'), {'type': 'wait', 'ms': True}, commands.pad('down')],
    [commands.pad('down'), RELEASE, commands.pad('down'), RELEASE],
    [commands.pad('down'), None, commands.pad('down')],
    [commands.pad('down'), RELEASE, {**commands.pad('down'), 'hold_ms': 200}],
])
def test_castle_batch_feedback_accepts_only_bounded_taps_and_explicit_release(actions):
    assert entry.observation_interval_ms(state(_castle_list(NAMES)), actions) == 1500


@pytest.mark.parametrize('change', ['old_step', 'different_kind', 'no_cursor', 'other_menu'])
def test_castle_batch_feedback_needs_the_current_castle_panel(change):
    screen = _castle_list(NAMES)
    if change == 'no_cursor':
        screen.hand = None
    observed = state(screen)
    if change == 'old_step':
        observed['step'] += 1
    elif change == 'different_kind':
        observed['screen_kind'] = 'map'
    elif change == 'other_menu':
        observed['menu_navigation_reading']['menu'] = 'global_roster'
    assert entry.observation_interval_ms(observed, taps('down', 4)) == 1500

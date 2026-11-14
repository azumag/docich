"""A visible sortie destination can skip observations, never cursor proof."""
from copy import deepcopy
from dataclasses import replace
import json
from unittest.mock import patch

import pytest

from docich import hanjuku_policy as policy
from docich.hanjuku.flows import deploy
from docich.hanjuku.os_layer import commands
from docich.hanjuku.os_layer.font import UNKNOWN
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.state.regions import plan as plan_region
from test_hanjuku_general_list_scan import general_list, memory, boss_site_memory, window
from test_hanjuku_menu_cursor_tempo import entry, state
from test_hanjuku_visible_castle_batch import taps


NAMES = ['アルファルファ', 'ゼウス', 'ラズベリー', 'キャンディー',
         'ヘルメス', 'リースリング', 'ココット', 'アテナ']


@pytest.mark.parametrize('selected,target,count', [(0, 2, 2), (0, 3, 3), (0, 6, 4),
                                                  (7, 0, 4), (4, 6, 2), (6, 4, 2)])
def test_visible_general_path_has_bounded_taps_and_fast_feedback(selected, target, count):
    screen = general_list(NAMES, selected)
    before = deepcopy(screen)
    actions = commands.general_list_batch(screen, NAMES[target])
    assert actions == taps('down' if target > selected else 'up', count)
    observed = state(screen)
    saved = deepcopy((observed, actions))
    assert entry.observation_interval_ms(observed, actions) == 500
    assert (observed, actions) == saved
    assert screen == before


def test_six_rows_take_two_movement_observations_then_a_fresh_confirm():
    mem = memory()  # 1-C1 requests ココット, row 6 here.
    for selected, count in [(0, 4), (4, 2)]:
        actions = policy.deploy_step(general_list(NAMES, selected), mem)
        assert actions == taps('down', count)
        assert plan_region(mem)['orders']['1-C1'] == 'pending'
        assert not plan_region(mem).get('sortie_general')
    assert policy.deploy_step(general_list(NAMES, 6), mem) == [commands.pad('a')]
    # Names on this page are not a proof of the entire garrison.
    assert mem['general_list_scan']['end'] == 'named'
    assert mem['general_list_scan']['presses'] == 0


def test_four_visible_rows_need_one_move_observation_then_a_fresh_confirm():
    names = ['アルファルファ', 'ゼウス', 'ラズベリー', 'ココット']
    mem = memory()
    assert policy.deploy_step(general_list(names), mem) == taps('down', 3)
    assert policy.deploy_step(general_list(names, 3), mem) == [commands.pad('a')]


def test_named_castle_transfer_uses_the_same_batch_without_changing_the_actor():
    order = {'step': 'test-move', 'source': 'アルマムーン', 'target': 'ジョンリギ',
             'general': 'ココット', 'purpose': 'move'}
    mem = memory()
    actions = deploy._move_general_pick(general_list(NAMES), mem, order)
    assert actions == taps('down', 4)
    assert plan_region(mem)['general_override']['test-move'] == 'ココット'
    assert not plan_region(mem).get('sortie_general')


@pytest.mark.parametrize('landed', [0, 2, 7])
def test_partial_missing_and_excess_inputs_use_the_actual_next_cursor(landed):
    mem = memory()
    assert policy.deploy_step(general_list(NAMES), mem) == taps('down', 4)
    actions = policy.deploy_step(general_list(NAMES, landed), mem)
    expected = [commands.pad('up')] if landed == 7 else taps('down', 4)
    assert actions == expected
    assert not any(a.get('buttons') == ['a'] for a in actions)


@pytest.mark.parametrize('change', ['gap', 'gap_after_cap', 'unknown', 'duplicate_target',
                                   'duplicate_row', 'no_hand', 'wrong_pane', 'wrong_kind',
                                   'no_prompt', 'unknown_name', 'global_roster'])
def test_unreadable_or_foreign_paths_never_authorize_a_batch(change):
    screen = general_list(NAMES)
    if change in ('gap', 'gap_after_cap'):
        y = 71 if change == 'gap' else 119
        screen.lines = [line for line in screen.lines if line.y != y]
    elif change == 'unknown':
        screen.lines = [replace(line, cells=line.cells + ((136, UNKNOWN),))
                        if line.y == 71 else line for line in screen.lines]
    elif change == 'duplicate_target':
        screen = general_list([*NAMES[:2], 'ココット', *NAMES[3:]])
    elif change == 'duplicate_row':
        screen.lines.append(screen.lines[2])
    elif change == 'no_hand':
        screen.hand = None
    elif change == 'wrong_pane':
        screen.hand = (42, 25, 62, 38)
    elif change == 'wrong_kind':
        screen.kind = 'unknown'
    elif change == 'no_prompt':
        screen.text = ''
    elif change == 'unknown_name':
        screen = general_list([*NAMES[:2], 'よめない', *NAMES[3:]])
    else:
        from docich.hanjuku.os_layer.screen import parse
        from test_hanjuku_house import roster
        screen = parse(roster(names=tuple(NAMES)))
    assert commands.general_list_batch(screen, 'ココット') is None


def test_single_step_and_hidden_target_keep_the_existing_navigation():
    mem = memory()
    assert policy.deploy_step(general_list(NAMES, 5), mem) == [commands.pad('down')]
    mem = memory()
    assert policy.deploy_step(window(0, 0), mem) == [commands.pad('down')]
    assert mem['general_list_scan']['presses'] == 1


def test_a_gap_on_the_visible_path_keeps_the_original_single_step():
    screen = general_list(NAMES)
    screen.lines = [line for line in screen.lines if line.y != 71]
    assert policy.deploy_step(screen, memory()) == [commands.pad('down')]


def test_full_roster_scan_still_visits_each_row():
    mem = boss_site_memory()
    for row in range(4):
        assert policy.deploy_step(window(0, row), mem) == [commands.pad('down')]
    assert mem['general_list_scan']['presses'] == 4


@pytest.mark.parametrize('actions', [
    [commands.pad('down')] * 2, taps('down', 5),
    [commands.pad('down'), {'type': 'wait', 'ms': 50}, commands.pad('up')],
    [commands.pad('down'), {'type': 'wait', 'ms': 50}, commands.pad('a')],
    [commands.pad('down'), {'type': 'wait', 'ms': 0}, commands.pad('down')],
])
def test_general_batch_feedback_rejects_unsafe_sequences(actions):
    assert entry.observation_interval_ms(state(general_list(NAMES)), actions) == 1500


@pytest.mark.parametrize('change', ['old_step', 'different_kind', 'other_menu'])
def test_feedback_needs_the_same_observation_and_menu(change):
    observed = state(general_list(NAMES))
    if change == 'old_step':
        observed['step'] += 1
    elif change == 'different_kind':
        observed['screen_kind'] = 'unknown'
    else:
        observed['menu_navigation_reading']['menu'] = 'fort_roster'
    assert entry.observation_interval_ms(observed, taps('down', 4)) == 1500


@pytest.mark.parametrize('menu', [None, [], {}, 'fort_roster'])
def test_malformed_menu_context_does_not_enable_feedback_or_raise(menu):
    observed = state(general_list(NAMES))
    observed['menu_navigation_reading']['menu'] = menu
    assert entry.observation_interval_ms(observed, taps('down', 4)) == 1500


def test_action_plan_keeps_the_actual_frame_all_taps_and_releases(tmp_path):
    from docich.hanjuku.os_layer.screen import parse
    from test_hanjuku_house import Canvas
    c = Canvas()
    c.text(64, 31, 'しゅつげき')
    c.text(64, 47, 'ステータス')
    for i, name in enumerate(NAMES):
        c.text(144, 39 + i * 16, name)
    c.hand(122, 33)
    frame = c.frame()
    screen = parse(frame)
    actions = policy.deploy_step(screen, memory())
    assert actions == taps('down', 4)
    identity = {'game': 'hanjuku-hero', 'runtime_id': 'test-runtime',
                'generation': 1, 'lease_id': 'test-lease'}
    with patch('docich.hanjuku_scene.observe'), patch('docich.hanjuku_progress.record'):
        entry.persist(tmp_path, state(screen), [], {'hanjuku': identity}, actions=actions,
                      frame_sha256=frame.digest(), frame=frame)
    rows = [json.loads(row) for row in (tmp_path / 'hanjuku_decisions.jsonl').read_text().splitlines()]
    plan = next(row for row in rows if row['event'] == 'action_plan')
    assert plan['planned_actions'] == taps('down', 4)
    assert plan['observation_interval_ms'] == 500
    assert plan['dispatch_status'] == 'planned_not_yet_sent'
    assert read_png(tmp_path / 'hanjuku_frames' / plan['snapshot']).digest() == frame.digest()

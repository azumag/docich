"""One early status observation after an owned A, with no faster resend/close."""
from copy import deepcopy
import json
from unittest.mock import patch

import pytest

from docich.hanjuku.flows import post_month_status as post
from docich.hanjuku.os_layer import commands
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku_bot import decide
from test_hanjuku_house import roster, status
from test_hanjuku_menu_cursor_tempo import entry
from test_hanjuku_post_month_status import memory


def open_status():
    mem = memory(month='1-6')
    post.arm(mem)
    mem['post_month_status'].update(stage='roster', top_confirmed=True, highlight='どうし')
    state = {'policy': mem}
    frame = roster(names=('どうし', 'ゼウス'))
    for _ in range(post.MARKER_RETRIES + 1):
        actions, state = decide(frame, state)
        if actions:
            assert actions == [commands.pad('a')]
            return frame, state, actions
        assert entry.observation_interval_ms(state, actions) == 1500
    raise AssertionError('the bounded marker readings never opened the status')


def test_owned_roster_status_open_gets_one_fast_observation_and_keeps_the_close():
    _, state, actions = open_status()
    before = deepcopy(state)
    assert entry.observation_interval_ms(state, actions) == 500
    assert state == before
    owner = state['policy']['post_month_status']
    assert owner['stage'] == 'status' and owner['selected'] == 'どうし'
    assert owner['phase_reads'] == 0
    # A is only a request. The next complete named panel still supplies facts.
    assert 'どうし' not in owner['statuses']
    actions, state = decide(status('どうし', egg='エラベルエッグ4'), state)
    assert actions == [commands.pad('b')]
    assert state['status_panel_feedback'] is None
    assert entry.observation_interval_ms(state, actions) == 1500
    assert state['policy']['post_month_status']['statuses']['どうし']['uses'] == 4


def test_early_old_roster_does_not_resend_a_or_keep_fast_polling():
    frame, state, actions = open_status()
    assert entry.observation_interval_ms(state, actions) == 500
    actions, state = decide(frame, state)
    assert actions == []
    assert state['status_panel_feedback'] is None
    assert entry.observation_interval_ms(state, actions) == 1500
    assert state['policy']['post_month_status']['phase_reads'] == 1
    assert 'どうし' not in state['policy']['post_month_status']['statuses']


def test_another_generals_status_does_not_count_as_the_requested_panel():
    _, state, _ = open_status()
    actions, state = decide(status('ゼウス'), state)
    assert actions == []
    assert entry.observation_interval_ms(state, actions) == 1500
    assert not state['policy']['post_month_status']['statuses']


@pytest.mark.parametrize('actions', [
    [], [commands.pad('b')], [commands.pad('select')], [commands.pad('down')],
    [commands.pad('a'), commands.pad('a')], [commands.pad('down'), commands.pad('a')],
    [{**commands.pad('a'), 'buttons': ['a', 'b']}],
    [{**commands.pad('a'), 'hold_ms': 200}],
    [{**commands.pad('a'), 'hold_ms': 100.0}],
])
def test_status_open_feedback_cannot_speed_other_inputs_or_batches(actions):
    _, state, _ = open_status()
    assert not commands.status_panel_feedback(state, actions)
    # Existing single-row navigation feedback remains independently valid.
    expected = 500 if actions == [commands.pad('down')] else 1500
    assert entry.observation_interval_ms(state, actions) == expected


@pytest.mark.parametrize('change', ['missing', 'old_step', 'different_kind', 'wrong_transition',
                                     'boolean_step', 'extra_field'])
def test_status_feedback_is_current_and_has_only_fixed_metadata(change):
    _, state, actions = open_status()
    assert state['status_panel_feedback'] is not None
    if change == 'missing':
        state.pop('status_panel_feedback')
    elif change == 'old_step':
        state['step'] += 1
    elif change == 'different_kind':
        state['screen_kind'] = 'yes_no'
    elif change == 'wrong_transition':
        state['status_panel_feedback']['transition'] = 'purchase'
    elif change == 'boolean_step':
        state['status_panel_feedback']['step'] = True
    else:
        state['status_panel_feedback']['extra'] = 'unexpected'
    assert entry.observation_interval_ms(state, actions) == 1500


def test_status_open_without_a_clean_navigation_reading_keeps_normal_cadence():
    with patch.object(commands, 'menu_navigation_reading', return_value=None):
        _, state, actions = open_status()
    assert state['status_panel_feedback'] is None
    assert entry.observation_interval_ms(state, actions) == 1500


def test_generic_menu_a_without_the_post_month_owner_stays_at_normal_cadence():
    actions, state = decide(roster(names=('どうし', 'ゼウス')), {})
    assert state['status_panel_feedback'] is None
    assert entry.observation_interval_ms(state, [commands.pad('a')]) == 1500


def test_fast_status_plan_keeps_the_actual_source_frame_and_fixed_feedback(tmp_path):
    frame, state, actions = open_status()
    assert entry.observation_interval_ms(state, actions) == 500
    with patch('docich.hanjuku_scene.observe'), patch('docich.hanjuku_progress.record'):
        entry.persist(tmp_path, state, [], {'hanjuku': {
            'game': 'hanjuku-hero', 'runtime_id': 'test-status-tempo', 'generation': 1,
            'lease_id': 'test-lease',
        }}, actions=actions, frame_sha256=frame.digest(), frame=frame)
    rows = [json.loads(line) for line in (tmp_path / 'hanjuku_decisions.jsonl').read_text().splitlines()]
    plan = next(row for row in rows if row['event'] == 'action_plan')
    assert plan['observation_interval_ms'] == 500
    assert plan['status_panel_feedback'] == state['status_panel_feedback']
    assert plan['planned_actions'] == [commands.pad('a')]
    assert plan['dispatch_status'] == 'planned_not_yet_sent'
    assert plan['frame_sha256'] == read_png(tmp_path / 'hanjuku_frames' / plan['snapshot']).digest()

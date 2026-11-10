"""Reobserve existing sortie-card tap batches sooner, without new inputs."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from docich import hanjuku_policy as policy
from docich.hanjuku.os_layer.commands import menu_navigation_feedback
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import parse
from docich.hanjuku_bot import decide
from test_hanjuku_cattlemyu import memory
from test_hanjuku_menu_cursor_tempo import entry, state
from test_hanjuku_sortie_evidence import measured_card_select


@pytest.mark.parametrize('count', [2, 3, 4])
@pytest.mark.parametrize('direction', ['up', 'down'])
def test_measured_sortie_card_taps_get_fast_feedback_without_mutating_the_plan(count, direction):
    observed = state(measured_card_select(selected=3))
    actions = [policy.pad(direction) for _ in range(count)]
    before = deepcopy((observed, actions))
    assert observed['menu_navigation_reading']['menu'] == 'sortie_cards'
    assert entry.observation_interval_ms(observed, actions) == 500
    assert (observed, actions) == before


@pytest.mark.parametrize('actions', [
    [], [policy.pad('down')] * 5,
    [policy.pad('down'), policy.pad('up')],
    [policy.pad('left')] * 2, [policy.pad('right')] * 2,
    [policy.pad('down'), policy.pad('a')],
    [policy.pad('b'), policy.pad('down')],
    [policy.pad('down'), {'type': 'wait', 'ms': 50}],
    [policy.pad('down'), None],
    [policy.pad('down'), {**policy.pad('down'), 'hold_ms': 200}],
    [policy.pad('down'), {**policy.pad('down'), 'hold_ms': 50}],
    [policy.pad('down'), {**policy.pad('down'), 'hold_ms': 100.0}],
    [policy.pad('down'), {**policy.pad('down'), 'hold_ms': True}],
    [policy.pad('down'), {**policy.pad('down'), 'type': 'key'}],
    [policy.pad('down'), {**policy.pad('down'), 'buttons': ['down', 'a']}],
    [policy.pad('down'), {**policy.pad('down'), 'buttons': []}],
    [policy.pad('down'), {**policy.pad('down'), 'buttons': None}],
])
def test_only_existing_bounded_same_direction_tap_batches_are_fast(actions):
    assert entry.observation_interval_ms(state(measured_card_select()), actions) == 1500


@pytest.mark.parametrize('change', [
    'missing', 'old_step', 'different_screen', 'no_context', 'other_menu',
    'battle_cards', 'bad_cursor', 'unobserved_direction',
])
def test_batch_feedback_cannot_use_stale_blind_or_other_menu_readings(change):
    observed = state(measured_card_select())
    reading = observed['menu_navigation_reading']
    if change == 'missing':
        observed['menu_navigation_reading'] = None
    elif change == 'old_step':
        observed['step'] += 1
    elif change == 'different_screen':
        observed['screen_kind'] = 'unknown'
    elif change == 'no_context':
        reading.pop('menu')
    elif change == 'other_menu':
        reading['menu'] = 'castle_roster'
    elif change == 'battle_cards':
        observed['screen_kind'] = reading['kind'] = 'text'
        reading['menu'] = 'battle_cards'
    elif change == 'bad_cursor':
        reading['cursor'] = [160, True]
    else:
        reading['directions'] = ['up']
    assert not menu_navigation_feedback(observed, [policy.pad('down')] * 4)


def test_unreadable_sortie_cursor_cannot_enable_batch_feedback():
    screen = measured_card_select()
    screen.hand = None
    assert state(screen)['menu_navigation_reading'] is None
    assert entry.observation_interval_ms(state(screen), [policy.pad('down')] * 4) == 1500


def test_the_next_observation_replaces_a_previous_card_batch_reading():
    observed = state(measured_card_select())
    fixture = Path(__file__).parent / 'fixtures/hanjuku/g745-god-dialogue-8128.png'
    _, next_state = decide(read_png(fixture), observed)
    assert next_state['menu_navigation_reading'] is None
    assert entry.observation_interval_ms(next_state, [policy.pad('down')] * 4) == 1500


def test_existing_discovery_and_rewind_keep_their_inputs_and_selection_state():
    order, mem = memory()
    mem.pop('rare_card_kit')
    screen = measured_card_select()
    inventory = policy._measured_card_select(screen)
    discovery = policy._rare_card_inventory(screen, mem, order, inventory)
    assert discovery == [policy.pad('down')] * 4
    assert entry.observation_interval_ms(state(screen), discovery) == 500
    assert mem['picked'] == [] and not mem.get('rare_card_kit')
    # The existing flow decides when to rewind. Timing cannot change its
    # remaining movement budget, or declare any card selected/delivered.
    mem['rare_scan']['rewind'] = 7
    rewind = policy._rare_card_inventory(screen, mem, order, inventory)
    assert rewind == [policy.pad('up')] * 4
    assert mem['rare_scan']['rewind'] == 3
    assert entry.observation_interval_ms(state(screen), rewind) == 500
    assert mem['picked'] == [] and not mem.get('rare_card_kit')
    assert entry.observation_interval_ms(state(screen), []) == 1500
    assert entry.observation_interval_ms(state(screen), [policy.pad('a')]) == 1500


def test_batch_action_plan_keeps_original_pixels_and_all_taps(tmp_path):
    # Saved pixels supply the actual panel/cursor evidence; the batch is a
    # synthetic plan, not a claim that this run delivered four taps.
    fixture = Path(__file__).parent / 'fixtures/hanjuku/g706-resume-card-select.png'
    frame = read_png(fixture)
    observed = state(parse(frame))
    assert observed['menu_navigation_reading']['menu'] == 'sortie_cards'
    actions = [policy.pad('down')] * 4
    identity = {'game': 'hanjuku-hero', 'runtime_id': 'test-runtime',
                'generation': 1, 'lease_id': 'test-lease'}
    with patch('docich.hanjuku_scene.observe'), patch('docich.hanjuku_progress.record'):
        entry.persist(tmp_path, observed, [], {'hanjuku': identity}, actions=actions,
                      frame_sha256=frame.digest(), frame=frame)
    rows = [json.loads(row) for row in (tmp_path / 'hanjuku_decisions.jsonl').read_text().splitlines()]
    plan = next(row for row in rows if row['event'] == 'action_plan')
    assert plan['observation_interval_ms'] == 500
    assert plan['planned_actions'] == actions
    assert plan['menu_navigation_feedback'] == observed['menu_navigation_reading']
    assert plan['dispatch_status'] == 'planned_not_yet_sent'
    assert read_png(tmp_path / 'hanjuku_frames' / plan['snapshot']).digest() == frame.digest()

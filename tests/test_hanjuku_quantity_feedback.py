"""Fast observations for the measured soldier digit editor, without new inputs."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from docich.hanjuku.flows.shop import quantity_step
from docich.hanjuku.os_layer.commands import menu_navigation_reading, pad
from docich.hanjuku.os_layer.font import UNKNOWN
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import parse
from docich.hanjuku_bot import decide
from test_hanjuku_menu_cursor_tempo import entry, state
from test_hanjuku_soldier_receipts import memory

FIX = Path(__file__).parent / 'fixtures/hanjuku'


def native():
    return parse(read_png(FIX / 'g741-army-count-7460.png'))


def editor(value=1, *, ones=False):
    screen = native()
    screen.header = {'year': 1, 'month': 8, 'gold': 79}
    screen.hand = (202, 197, 222, 210) if ones else (194, 197, 214, 210)
    for i, line in enumerate(screen.lines):
        if line.y == 199:
            cells = [(x, ch) for x, ch in line.cells if x not in (216, 224)]
            if not ones:  # The hand hides the tens digit while selecting ones.
                cells.append((216, str(value // 10)))
            cells.append((224, str(value % 10)))
            screen.lines[i] = replace(line, cells=tuple(sorted(cells)))
    return screen


def test_native_editor_has_actual_digit_cursor_evidence_and_no_confirmation_feedback():
    screen = native()
    before = deepcopy(screen)
    assert menu_navigation_reading(screen) == {
        'kind': 'month_menu', 'menu': 'soldier_quantity', 'cursor': [216, 199],
        'directions': ['up', 'down', 'right']}
    for button in ('up', 'down', 'right'):
        assert entry.observation_interval_ms(state(screen), [pad(button)]) == 500
    for actions in ([], [pad('a')], [pad('b')], [pad('left')]):
        assert entry.observation_interval_ms(state(screen), actions) == 1500
    assert screen == before


def test_native_frame_runs_through_the_bot_with_a_single_fast_direction():
    mem = memory()
    mem.update(chapter=2, month='2-2', gold=131, soldiers_seen_key='2-2')
    mem['shop'].update(key='2-2', gold_start=131)
    frame = read_png(FIX / 'g741-army-count-7460.png')
    actions, observed = decide(frame, {'policy': mem})
    assert actions == [pad('up')]
    assert observed['menu_navigation_reading']['menu'] == 'soldier_quantity'
    assert entry.observation_interval_ms(observed, actions) == 500
    assert not observed['policy']['shop'].get('soldier_receipt')
    assert observed['policy']['soldiers_seen'] == 20


def test_hidden_tens_digit_can_only_get_feedback_for_the_observed_ones_cursor():
    screen = editor(21, ones=True)
    reading = menu_navigation_reading(screen)
    assert reading['cursor'] == [224, 199]
    assert reading['directions'] == ['up', 'down', 'left']
    for button in reading['directions']:
        assert entry.observation_interval_ms(state(screen), [pad(button)]) == 500
    assert entry.observation_interval_ms(state(screen), [pad('right')]) == 1500


def test_digit_changes_and_digit_switch_keep_existing_inputs_and_payment_separation():
    mem = memory()
    sequence = [(1, False, 'up'), (11, False, 'up'), (21, False, 'right'),
                (21, True, 'up'), (22, True, 'up'), (23, True, 'up')]
    for value, ones, button in sequence:
        screen = editor(value, ones=ones)
        actions = quantity_step(screen, mem, soldiers=True)
        assert actions == [pad(button)]
        before = deepcopy(mem)
        assert entry.observation_interval_ms(state(screen), actions) == 500
        assert mem == before
        assert not mem['shop'].get('soldier_receipt')
    screen = editor(24, ones=True)
    actions = quantity_step(screen, mem, soldiers=True)
    assert actions == [pad('a')]
    assert entry.observation_interval_ms(state(screen), actions) == 1500
    assert mem['shop']['soldier_receipt']['status'] == 'pending'
    assert mem['soldiers_seen'] == 20


@pytest.mark.parametrize('change', [
    'missing_prompt', 'missing_instructions', 'unknown_prompt', 'unknown_digit',
    'missing_digit', 'duplicate_number_row', 'duplicate_digit', 'no_hand',
    'wrong_y', 'wrong_x', 'wrong_kind', 'malformed_hand', 'bool_hand', 'wide_hand',
])
def test_incomplete_blind_or_foreign_editors_never_enable_fast_feedback(change):
    screen = editor()
    if change == 'missing_prompt':
        screen.text = screen.text.replace('じゅうじキー', '')
    elif change in ('missing_instructions', 'unknown_prompt'):
        screen.lines = [replace(line, cells=tuple((x, UNKNOWN if x == 24 else ch)
                                                  for x, ch in line.cells))
                        if line.y == 183 else line for line in screen.lines]
        if change == 'missing_instructions':
            screen.lines = [line for line in screen.lines if line.y != 183]
    elif change in ('unknown_digit', 'missing_digit', 'duplicate_digit'):
        lines = []
        for line in screen.lines:
            if line.y == 199:
                if change == 'duplicate_digit':
                    line = replace(line, cells=line.cells + ((216, '0'),))
                else:
                    cells = tuple((x, UNKNOWN if x == 216 else ch) for x, ch in line.cells
                                  if change != 'missing_digit' or x != 216)
                    line = replace(line, cells=cells)
            lines.append(line)
        screen.lines = lines
    elif change == 'duplicate_number_row':
        screen.lines.append(next(line for line in screen.lines if line.y == 199))
    elif change == 'no_hand':
        screen.hand = None
    elif change == 'wrong_y':
        screen.hand = (194, 177, 214, 190)
    elif change == 'wrong_x':
        screen.hand = (162, 197, 180, 210)
    elif change == 'wrong_kind':
        screen.kind = 'shop_quantity'
    elif change == 'malformed_hand':
        screen.hand = (194, 197)
    elif change == 'wide_hand':
        screen.hand = (20, 197, 214, 210)
    else:
        screen.hand = (194, 197, True, 210)
    assert menu_navigation_reading(screen) is None
    assert entry.observation_interval_ms(state(screen), [pad('up')]) == 1500


@pytest.mark.parametrize('actions', [
    [pad('up'), pad('up')], [pad('up'), pad('a')],
    [pad('up'), {'type': 'wait', 'ms': 50}, pad('up')],
    [{**pad('up'), 'hold_ms': 50}], [{**pad('up'), 'hold_ms': 200}],
    [{**pad('up'), 'buttons': ['up', 'a']}],
])
def test_only_one_existing_hundred_ms_direction_input_is_fast(actions):
    assert entry.observation_interval_ms(state(editor()), actions) == 1500


@pytest.mark.parametrize('change', ['old_step', 'other_screen', 'wrong_cursor'])
def test_stale_or_corrupt_editor_readings_are_slow(change):
    observed = state(editor())
    if change == 'old_step':
        observed['step'] += 1
    elif change == 'other_screen':
        observed['screen_kind'] = 'text'
    else:
        observed['menu_navigation_reading']['cursor'] = [64, 199]
    assert entry.observation_interval_ms(observed, [pad('up')]) == 1500


@pytest.mark.parametrize('name', ['g713-recovery-100g-112g.png',
                                'g747-month-refill-9336.png', 'g747-month-refill-9337.png'])
def test_saved_payment_confirmation_is_not_a_digit_editor(name):
    screen = parse(read_png(FIX / name))
    reading = menu_navigation_reading(screen)
    assert not reading or reading.get('menu') != 'soldier_quantity'
    assert entry.observation_interval_ms(state(screen), [pad('a')]) == 1500


def test_next_observation_discards_the_editor_reading_after_exit():
    frame = read_png(FIX / 'g745-god-dialogue-8128.png')
    _, observed = decide(frame, state(editor()))
    assert observed['menu_navigation_reading'] is None
    assert entry.observation_interval_ms(observed, [pad('up')]) == 1500


def test_action_plan_keeps_the_native_frame_and_single_input(tmp_path):
    frame = read_png(FIX / 'g741-army-count-7460.png')
    observed = state(parse(frame))
    identity = {'game': 'hanjuku-hero', 'runtime_id': 'test-runtime',
                'generation': 1, 'lease_id': 'test-lease'}
    with patch('docich.hanjuku_scene.observe'), patch('docich.hanjuku_progress.record'):
        entry.persist(tmp_path, observed, [], {'hanjuku': identity}, actions=[pad('up')],
                      frame_sha256=frame.digest(), frame=frame)
    rows = [json.loads(row) for row in (tmp_path / 'hanjuku_decisions.jsonl').read_text().splitlines()]
    plan = next(row for row in rows if row['event'] == 'action_plan')
    assert plan['observation_interval_ms'] == 500
    assert plan['planned_actions'] == [pad('up')]
    assert plan['dispatch_status'] == 'planned_not_yet_sent'
    assert plan['menu_navigation_feedback']['menu'] == 'soldier_quantity'
    assert read_png(tmp_path / 'hanjuku_frames' / plan['snapshot']).digest() == frame.digest()

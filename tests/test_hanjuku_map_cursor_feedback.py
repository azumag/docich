"""Reobserve measured own-roof corrections without changing navigation."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from docich.hanjuku.os_layer import navigation
from docich.hanjuku.os_layer.commands import pad
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import parse
from docich.hanjuku_bot import decide
from test_hanjuku_cursor_alignment_tempo import FRAME, GOAL, roof, view  # noqa: F401
from test_hanjuku_menu_cursor_tempo import entry

FIX = Path(__file__).parent / 'fixtures/hanjuku'


def state(mem, step=8, kind='map'):
    reading = mem.get('_map_cursor_feedback')
    return {'step': step, 'screen_kind': kind, 'policy': {},
            'map_cursor_feedback': {**reading, 'step': step} if reading else None}


@pytest.mark.parametrize('offset', [(4, 0), (-14, 0), (0, 9), (0, -9), (9, -7), (15, 15)])
def test_short_normal_cursor_corrections_keep_the_same_holds_and_get_fast_feedback(roof, offset):
    screen = view((GOAL[0] - offset[0], GOAL[1] - offset[1]))
    mem = {}
    actions = navigation._align_on_roof(screen, mem, FRAME, ('own',), 'test:map')
    observed = state(mem)
    before = deepcopy((observed, actions, mem))
    assert entry.observation_interval_ms(observed, actions) == 500
    assert (observed, actions, mem) == before
    assert sum(a['hold_ms'] for a in actions) <= 500
    assert not any(a['buttons'] == ['a'] for a in actions)


@pytest.mark.parametrize('change', ['target_marker', 'both_cursors', 'world_map', 'hand',
                                   'search', 'clipped', 'ambiguous', 'enemy', 'long_hold'])
def test_other_navigation_and_uncertain_roofs_keep_normal_cadence(roof, monkeypatch, change):
    mem = {}
    screen = view((90, 100))
    kinds = ('own',)
    if change == 'target_marker':
        screen = view((90, 100), marker=True)
    elif change == 'both_cursors':
        screen.marker = (90, 100)
    elif change == 'world_map':
        screen.kind = 'world_map'
    elif change == 'hand':
        screen.hand = (20, 20, 40, 33)
    elif change == 'search':
        mem['nav_search'] = True
    elif change in ('clipped', 'ambiguous', 'enemy'):
        roofs = [{'kind': 'enemy' if change == 'enemy' else 'own',
                  'target': GOAL, 'clipped': change == 'clipped'}]
        if change == 'ambiguous':
            roofs.append({'kind': 'own', 'target': (102, 100), 'clipped': False})
        monkeypatch.setattr(navigation, 'castle_roofs', lambda _frame: roofs)
        if change == 'enemy':
            kinds = ('enemy',)
    else:
        screen = view((84, 100))  # 16 frames = 267 ms, above the feedback cap.
    actions = navigation._align_on_roof(screen, mem, FRAME, kinds, 'test:map')
    assert not mem.get('_map_cursor_feedback')
    assert entry.observation_interval_ms(state(mem, kind=screen.kind), actions) == 1500


def test_a_confirm_and_an_unchanged_cursor_never_count_as_movement_success(roof):
    mem = {}
    screen = view((86, 100))
    for _ in range(2):
        actions = navigation._align_on_roof(screen, mem, FRAME, ('own',), 'test:map')
        assert actions == [pad('right', 14)]
        assert entry.observation_interval_ms(state(mem), actions) == 500
    # Preserve the original two-correction stall budget; never mark arrival.
    assert navigation._align_on_roof(screen, mem, FRAME, ('own',), 'test:map') is None
    assert mem.get('_map_cursor_feedback') is None
    assert navigation._align_on_roof(view(GOAL), mem, FRAME, ('own',), 'test:map') == 'on'
    assert entry.observation_interval_ms(state(mem), [pad('a')]) == 1500


@pytest.mark.parametrize('change', ['old_step', 'other_screen', 'float_cursor', 'bool_cursor',
                                   'outside_roof', 'wrong_kind', 'extra_field', 'missing'])
def test_stale_or_corrupt_feedback_is_not_reused(roof, change):
    mem = {}
    actions = navigation._align_on_roof(view((86, 100)), mem, FRAME, ('own',), 'test:map')
    observed = state(mem)
    if change == 'old_step':
        observed['step'] += 1
    elif change == 'other_screen':
        observed['screen_kind'] = 'text'
    elif change == 'float_cursor':
        observed['map_cursor_feedback']['cursor'] = [86.0, 100]
    elif change == 'bool_cursor':
        observed['map_cursor_feedback']['cursor'] = [True, 100]
    elif change == 'outside_roof':
        observed['map_cursor_feedback']['roof'] = [256, 100]
    elif change == 'wrong_kind':
        observed['map_cursor_feedback']['kind'] = 'map_target'
    elif change == 'extra_field':
        observed['map_cursor_feedback']['arrived'] = True
    else:
        observed['map_cursor_feedback'] = None
    assert entry.observation_interval_ms(observed, actions) == 1500


@pytest.mark.parametrize('actions', [None, [], [pad('a')], [pad('select')], [pad('y')],
    [pad('left', 14)], [pad('right', 7)], [pad('right', 14), pad('a')],
    [pad('right', 14)] * 2, [{**pad('right', 14), 'hold_ms': 233.0}],
    [{**pad('right', 14), 'buttons': ['right', 'down']}],
    [None], [pad('right', 14), {'type': 'wait', 'ms': 50}],
])
def test_feedback_requires_the_exact_existing_bounded_correction(roof, actions):
    mem = {}
    navigation._align_on_roof(view((86, 100)), mem, FRAME, ('own',), 'test:map')
    assert entry.observation_interval_ms(state(mem), actions) == 1500


def test_saved_map_pixels_supply_both_cursor_and_own_roof_without_a_new_input():
    frame = read_png(FIX / 'resume-chapter-2-map.png')
    screen = parse(frame)
    mem = {}
    actions = navigation._align_on_roof(screen, mem, frame, ('own',), 'test:map')
    assert actions == [pad('right', 7), pad('down', 4)]
    observed = state(mem)
    assert observed['map_cursor_feedback']['cursor'] == [140, 120]
    assert observed['map_cursor_feedback']['roof'] == [147, 124]
    assert entry.observation_interval_ms(observed, actions) == 500


def test_orchestrator_stamps_fresh_evidence_and_discards_it_on_the_next_frame():
    frame = read_png(FIX / 'resume-chapter-2-map.png')
    # Control only which foreground owner handles the saved frame. Its
    # cursor, roofs, correction and the orchestrator's result are real code.
    def own_roof(screen, mem, source):
        return navigation._align_on_roof(screen, mem, source, ('own',), 'test:map')
    with patch('docich.hanjuku.flows.map_registration.step', return_value=None), \
            patch('docich.hanjuku.flows.post_month_status.step', return_value=None), \
            patch('docich.hanjuku.flows.arrival_status.step', return_value=None), \
            patch('docich.hanjuku.flows.house.step', side_effect=own_roof):
        actions, observed = decide(frame, {'step': 17, 'policy': {'chapter': 2}})
    assert actions == [pad('right', 7), pad('down', 4)]
    assert observed['map_cursor_feedback']['step'] == observed['step'] == 18
    assert '_map_cursor_feedback' not in observed['policy']
    assert entry.observation_interval_ms(observed, actions) == 500
    _, following = decide(read_png(FIX / 'g745-god-dialogue-8128.png'), observed)
    assert following['map_cursor_feedback'] is None
    assert entry.observation_interval_ms(following, actions) == 1500


def test_chapter_transition_also_discards_in_flight_roof_evidence(roof):
    from docich.hanjuku.strategy import campaign
    mem = {'chapter': 1}
    navigation._align_on_roof(view((86, 100)), mem, FRAME, ('own',), 'test:map')
    assert mem.get('_map_cursor_feedback')
    campaign._enter_chapter(mem, 2, reason='test')
    assert '_map_cursor_feedback' not in mem


def test_action_plan_records_all_holds_and_the_original_saved_frame(tmp_path):
    frame = read_png(FIX / 'resume-chapter-2-map.png')
    mem = {}
    actions = navigation._align_on_roof(parse(frame), mem, frame, ('own',), 'test:map')
    observed = state(mem)
    identity = {'game': 'hanjuku-hero', 'runtime_id': 'test-runtime',
                'generation': 1, 'lease_id': 'test-lease'}
    with patch('docich.hanjuku_scene.observe'), patch('docich.hanjuku_progress.record'):
        entry.persist(tmp_path, observed, [], {'hanjuku': identity}, actions=actions,
                      frame_sha256=frame.digest(), frame=frame)
    rows = [json.loads(line) for line in (tmp_path / 'hanjuku_decisions.jsonl').read_text().splitlines()]
    plan = next(row for row in rows if row['event'] == 'action_plan')
    assert plan['planned_actions'] == actions
    assert plan['observation_interval_ms'] == 500
    assert plan['map_cursor_feedback'] == observed['map_cursor_feedback']
    assert plan['dispatch_status'] == 'planned_not_yet_sent'
    assert read_png(tmp_path / 'hanjuku_frames' / plan['snapshot']).digest() == frame.digest()

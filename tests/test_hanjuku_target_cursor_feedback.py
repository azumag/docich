"""Target-marker cadence retains half-offset holds and fresh arrival checks."""
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from docich.hanjuku.os_layer import navigation
from docich.hanjuku.os_layer.commands import pad
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import parse
from test_hanjuku_cursor_alignment_tempo import FRAME, GOAL, roof, view  # noqa: F401
from test_hanjuku_map_cursor_feedback import state
from test_hanjuku_menu_cursor_tempo import entry

FIX = Path(__file__).parent / 'fixtures/hanjuku'


@pytest.mark.parametrize('owner', ['own', 'enemy'])
@pytest.mark.parametrize('offset', [(4, 0), (-14, 0), (0, 9), (0, -9), (9, -7), (28, 28)])
def test_target_marker_keeps_existing_holds_with_fast_fresh_feedback(monkeypatch, owner, offset):
    monkeypatch.setattr(navigation, 'castle_roofs', lambda _frame: [
        {'kind': owner, 'target': GOAL, 'clipped': False}])
    screen = view((GOAL[0] - offset[0], GOAL[1] - offset[1]), marker=True)
    mem = {}
    actions = navigation._align_on_roof(screen, mem, FRAME, (owner,), 'test:target')
    expected = [pad(pos if d > 0 else neg, (abs(d) + 1) // 2)
                for d, neg, pos in ((offset[0], 'left', 'right'), (offset[1], 'up', 'down'))
                if abs(d) > navigation.ALIGN_TOL]
    assert actions == expected
    observed = state(mem, kind='map_target')
    before = deepcopy((observed, actions, mem))
    assert entry.observation_interval_ms(observed, actions) == 500
    assert (observed, actions, mem) == before
    assert sum(a['hold_ms'] for a in actions) <= 500
    assert entry.observation_interval_ms(observed, [pad('a')]) == 1500


@pytest.mark.parametrize('change', ['both_cursors', 'no_marker', 'hand', 'search',
                                   'clipped', 'ambiguous', 'wrong_owner', 'outside_radius'])
def test_uncertain_target_marker_never_gets_fast_feedback(roof, monkeypatch, change):
    mem = {}
    screen = view((86, 100), marker=True)
    kinds = ('own',)
    if change == 'both_cursors':
        screen.cursor = (86, 100)
    elif change == 'no_marker':
        screen.marker = None
    elif change == 'hand':
        screen.hand = (20, 20, 40, 33)
    elif change == 'search':
        mem['nav_search'] = True
    elif change == 'outside_radius':
        screen.marker = (71, 100)
    else:
        roofs = [{'kind': 'enemy' if change == 'wrong_owner' else 'own',
                  'target': GOAL, 'clipped': change == 'clipped'}]
        if change == 'ambiguous':
            roofs.append({'kind': 'own', 'target': (102, 100), 'clipped': False})
        monkeypatch.setattr(navigation, 'castle_roofs', lambda _frame: roofs)
    actions = navigation._align_on_roof(screen, mem, FRAME, kinds, 'test:target')
    assert mem.get('_map_cursor_feedback') is None
    assert entry.observation_interval_ms(state(mem, kind='map_target'), actions) == 1500


def test_unchanged_target_exhausts_the_same_budget_and_arrival_needs_a_new_read(roof):
    mem = {}
    screen = view((86, 100), marker=True)
    for _ in range(2):
        actions = navigation._align_on_roof(screen, mem, FRAME, ('own',), 'test:target')
        assert actions == [pad('right', 7)]
        assert entry.observation_interval_ms(state(mem, kind='map_target'), actions) == 500
        assert 'near_goal' not in mem
    assert navigation._align_on_roof(screen, mem, FRAME, ('own',), 'test:target') is None
    assert mem.get('_map_cursor_feedback') is None
    assert navigation._align_on_roof(view(GOAL, marker=True), mem, FRAME,
                                     ('own',), 'test:target') == 'on'
    assert entry.observation_interval_ms(state(mem, kind='map_target'), [pad('a')]) == 1500


@pytest.mark.parametrize('change', ['old_step', 'other_screen', 'wrong_model', 'confirm', 'long_hold'])
def test_target_feedback_cannot_reuse_a_normal_cursor_model_or_old_observation(roof, change):
    mem = {}
    actions = navigation._align_on_roof(view((86, 100), marker=True), mem, FRAME,
                                        ('own',), 'test:target')
    observed = state(mem, kind='map_target')
    if change == 'old_step':
        observed['step'] += 1
    elif change == 'other_screen':
        observed['screen_kind'] = 'map'
    elif change == 'wrong_model':
        actions = [pad('right', 14)]
    elif change == 'confirm':
        actions += [pad('a')]
    else:
        actions = [pad('right', 56)]
    assert entry.observation_interval_ms(observed, actions) == 1500


def test_saved_enemy_target_pixels_get_fast_feedback_and_keep_original_input_log(tmp_path):
    frame = read_png(FIX / 'g1075-target-marker-beside-rigua.png')
    screen = parse(frame, phase='field')
    assert screen.kind == 'map_target' and screen.marker == (128, 118)
    mem = {}
    actions = navigation._align_on_roof(screen, mem, frame, ('enemy',), 'test:target')
    assert actions == [pad('down', 5)]
    observed = state(mem, kind='map_target')
    assert observed['map_cursor_feedback']['roof'] == [128, 127]
    assert entry.observation_interval_ms(observed, actions) == 500
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
    assert read_png(tmp_path / 'hanjuku_frames' / plan['snapshot']).digest() == frame.digest()

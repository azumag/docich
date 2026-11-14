"""A cursor-less map is nudged instead of waiting the whole no-input hold.

g1153 2026-11-07 22:53-22:55 (issue #3721): the next chapter's map sat with no
HUD, text or cursor for 174 s. map_step has no cell to steer and the generic
gate only fires after NO_INPUT_HOLD_MAX observations, so this bounded nudge
sends SELECT once (the measured recentre) and then, when no order owns the
map, one A to advance a scene drawn over it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich.hanjuku import orchestrator
from docich.hanjuku.os_layer.screen import Screen
from docich.hanjuku_pixels import Frame


def bare_map():
    return Screen(lines=[], hand=None, text='', kind='map', cursor=None)


def test_an_aimless_bare_map_recentres_once():
    mem = {'tick': 100}
    actions = orchestrator.map_unsteerable_nudge(
        bare_map(), mem, 'map', 'field', orchestrator.MAP_UNSTEERABLE_HOLD_MAX + 1, 'd1')
    assert actions == [orchestrator.pad('select')]
    assert mem['uncertain'] is True
    assert mem['map_unsteerable']['steps'] == 1
    rec = mem['_records'][-1]
    assert rec['decision'] == 'map_unsteerable_nudge'
    assert rec['observed_metric']['steps'] == 1


def test_a_second_frozen_episode_advances_with_one_a_without_orders():
    mem = {'tick': 100}
    orchestrator.map_unsteerable_nudge(bare_map(), mem, 'map', 'field', 9, 'd1')
    actions = orchestrator.map_unsteerable_nudge(bare_map(), mem, 'map', 'field', 9, 'd1')
    assert actions == [orchestrator.pad('a')]
    assert mem['map_unsteerable']['steps'] == 2
    # Each frozen episode gets exactly one SELECT and one A.
    assert orchestrator.map_unsteerable_nudge(bare_map(), mem, 'map', 'field', 9, 'd1') is None


def test_a_plan_still_owns_the_map_so_no_blind_a_is_sent():
    mem = {'tick': 100, 'plan': {'orders': {'S1': 'pending'}}}
    orchestrator.map_unsteerable_nudge(bare_map(), mem, 'map', 'field', 9, 'd1')
    assert orchestrator.map_unsteerable_nudge(bare_map(), mem, 'map', 'field', 9, 'd1') is None
    assert mem['map_unsteerable']['steps'] == 1


def test_a_new_frame_starts_a_new_episode():
    mem = {'tick': 100}
    orchestrator.map_unsteerable_nudge(bare_map(), mem, 'map', 'field', 9, 'd1')
    actions = orchestrator.map_unsteerable_nudge(bare_map(), mem, 'map', 'field', 9, 'd2')
    assert actions == [orchestrator.pad('select')]
    assert mem['map_unsteerable']['digest'] == 'd2'


def test_the_nudge_only_applies_to_a_frozen_cursorless_map():
    mem = {'tick': 100}
    assert orchestrator.map_unsteerable_nudge(bare_map(), mem, 'map', 'field', 8, 'd1') is None
    assert orchestrator.map_unsteerable_nudge(bare_map(), mem, 'map', 'transition', 9, 'd1') is None
    assert orchestrator.map_unsteerable_nudge(bare_map(), mem, 'unknown', 'field', 9, 'd1') is None
    cursor = Screen(lines=[], hand=None, text='', kind='map', cursor=(100, 80))
    assert orchestrator.map_unsteerable_nudge(cursor, mem, 'map', 'field', 9, 'd1') is None
    titled = Screen(lines=[], hand=None, text='しゅつげき', kind='map', cursor=None)
    assert orchestrator.map_unsteerable_nudge(titled, mem, 'map', 'field', 9, 'd1') is None


def test_decide_nudges_a_frozen_cursorless_map_before_the_generic_hold(monkeypatch):
    fake = bare_map()
    monkeypatch.setattr(orchestrator, 'classify', lambda frame: 'field')
    import docich.hanjuku.os_layer.screen as screen_module
    monkeypatch.setattr(screen_module, 'parse', lambda frame, phase=None: fake)
    frame = Frame(256, 224, bytes((40, 140, 20)) * (256 * 224))
    state = {'policy': {'chapter': 1}}
    seen = []
    for _ in range(orchestrator.MAP_UNSTEERABLE_HOLD_MAX + 1):
        actions, state = orchestrator.decide(frame, state)
        seen.append(actions)
    assert all(actions == [] for actions in seen[:-1])
    assert seen[-1] == [orchestrator.pad('select')]
    assert state['_records'][-1]['decision'] == 'map_unsteerable_nudge'

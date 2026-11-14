"""#3978: an ordinary camp recall with no own roof in view Y-jumps to the goal.

Before this change the ``dest`` picker for a plain camp (not the hero, not a
砦) fell back to ``picker_forced`` — confirming the marker wherever it stood —
or cancelled. The hero/fort route already jumps the marker onto the measured
goal castle; the ordinary camp now shares that route so the recall lands on a
real own castle and finishes in fewer observations (less exposure to the
month-start scene cutting the picker).
"""
import pytest

from docich import hanjuku_policy as p
from docich.hanjuku.flows import recall as recall_flow
from docich.hanjuku.orchestrator import pad
from docich.hanjuku.os_layer.screen import Screen
from docich.hanjuku.state.regions import flow as _flow


def memory(**recall):
    return {'chapter': 1, 'tick': 100, '_records': [],
            'captured': ['キカンドン', 'ジョンリギ', 'ゴーメン'],
            'recall': {'stage': 'dest', 'steps': 1, **recall}}


def no_roofs(monkeypatch):
    monkeypatch.setattr(recall_flow, 'castle_roofs', lambda *_a, **_k: [])


def test_plain_camp_without_own_roof_y_jumps_to_the_goal(monkeypatch):
    mem = memory()
    no_roofs(monkeypatch)
    monkeypatch.setattr(recall_flow, '_recall_goal', lambda _m: 'キカンドン')
    screen = Screen([], None, '', kind='map_target', marker=(90, 80))
    assert recall_flow._camp_recall_step(screen, mem, None) == [pad('y')]
    assert mem['y_jump']['goal'] == 'キカンドン'
    assert mem['y_jumps']['RECALL:target'] == 1
    assert _flow(mem)['recall']['goal'] == 'キカンドン'


def test_a_measured_jump_confirms_on_the_hidden_goal(monkeypatch):
    mem = memory(goal='キカンドン')
    no_roofs(monkeypatch)
    mem['y_jumped'] = {'RECALL': 'キカンドン'}
    screen = Screen([], None, '', kind='map_target', marker=(90, 80))
    assert recall_flow._camp_recall_step(screen, mem, None) == [pad('a')]
    done = _flow(mem)['recall']
    assert done['stage'] == 'await_dispatch' and done['goal'] == 'キカンドン'
    assert mem['_records'][-1]['decision'] == 'camp_recall_requested'


def test_the_spent_jump_budget_falls_back_to_confirming_anywhere(monkeypatch):
    mem = memory()
    no_roofs(monkeypatch)
    monkeypatch.setattr(recall_flow, '_recall_goal', lambda _m: 'キカンドン')
    mem['y_jumps'] = {'RECALL:target': 2}
    screen = Screen([], None, '', kind='map_target', marker=(90, 80))
    assert recall_flow._camp_recall_step(screen, mem, None) == [pad('a')]
    done = _flow(mem)['recall']
    assert done['stage'] == 'await_dispatch' and done['picker_forced'] is True


def test_an_unknown_goal_still_confirms_anywhere(monkeypatch):
    mem = memory()
    no_roofs(monkeypatch)
    monkeypatch.setattr(recall_flow, '_recall_goal', lambda _m: None)
    screen = Screen([], None, '', kind='map_target', marker=(90, 80))
    assert recall_flow._camp_recall_step(screen, mem, None) == [pad('a')]
    done = _flow(mem)['recall']
    assert done['stage'] == 'await_dispatch' and done['picker_forced'] is True


def test_a_fresh_recall_starts_a_fresh_jump_receipt(monkeypatch):
    """The last recall's jump receipt must not confirm this picker's marker."""
    from docich.hanjuku_pixels import Frame
    mem = {'chapter': 1, 'tick': 100, '_records': [],
           'captured': ['キカンドン', 'ジョンリギ', 'ゴーメン'],
           'y_jumped': {'RECALL': 'キカンドン'}, 'y_jumps': {'RECALL:target': 2}}
    camp = {'target': (141, 128), 'clipped': False}
    screen = Screen([], None, '', kind='map', cursor=(100, 100))
    frame = Frame(1, 1, b'\x00' * 3)
    monkeypatch.setattr(recall_flow, 'own_camps', lambda _f: [camp])
    monkeypatch.setattr(recall_flow, 'castle_roofs', lambda *_a, **_k: [])
    monkeypatch.setattr(recall_flow, 'boss_redirect_goal', lambda _m: None)
    monkeypatch.setattr(recall_flow, 'camp_stays', lambda _m: False)
    actions = recall_flow._camp_recall_step(screen, mem, frame, observed_camps=[camp])
    assert actions is not None                 # to_camp starts walking
    assert _flow(mem)['recall']['stage'] == 'to_camp'
    assert 'RECALL' not in (mem.get('y_jumped') or {})
    assert 'RECALL:target' not in (mem.get('y_jumps') or {})


def test_a_missing_marker_still_cancels(monkeypatch):
    mem = memory()
    no_roofs(monkeypatch)
    monkeypatch.setattr(recall_flow, '_recall_goal', lambda _m: 'キカンドン')
    screen = Screen([], None, '', kind='map_target')
    assert recall_flow._camp_recall_step(screen, mem, None) == [pad('b')]
    assert 'recall' not in _flow(mem)

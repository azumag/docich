"""Regression: a stuck recall must leave the return screen instead of waiting forever."""
from docich import hanjuku_policy as p
from docich.hanjuku_screen import Screen
from docich.hanjuku.state.regions import flow as _flow, journal as _journal


def test_stuck_recall_aborts_with_b_on_map_target_menu():
    mem = {'chapter': 1, 'tick': 100, '_records': [],
           'recall': {'stage': 'menu', 'steps': p.RECALL_LIMIT, 'hero': False}}
    screen = Screen([], None, '', kind='map_target')
    assert p.camp_recall_step(screen, mem, None) == [p.pad('b')]
    assert 'recall' not in _flow(mem)


def test_stuck_recall_confirms_where_the_picker_stands_instead_of_cancelling():
    """Owner 2026-11-05: 最悪キャンセルにせず、どこでも良いから指定する。"""
    mem = {'chapter': 1, 'tick': 100, '_records': [],
           'recall': {'stage': 'dest', 'steps': 0, 'picker_observations': p.RECALL_LIMIT - 1}}
    screen = Screen([], None, '', kind='world_map')
    assert p.world_map_step(screen, mem, None) == [p.pad('a'), {'type': 'wait', 'ms': 700}, p.pad('a')]
    assert _flow(mem)['recall']['stage'] == 'await_dispatch'
    assert [r['decision'] for r in mem['_records']].count('camp_recall_picker_fallback') == 1


def test_stuck_recall_aborts_with_b_on_map_target_to_camp():
    mem = {'chapter': 1, 'tick': 100, '_records': [],
           'recall': {'stage': 'to_camp', 'steps': 5, 'target': [1, 1], 'hero': False}}
    screen = Screen([], None, '', kind='map_target')
    assert p.camp_recall_step(screen, mem, None) == [p.pad('b')]
    assert 'recall' not in _flow(mem)

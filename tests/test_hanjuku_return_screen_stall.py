"""Regression: a stuck recall must leave the return screen instead of waiting forever."""
from docich import hanjuku_policy as p
from docich.hanjuku_screen import Screen


def test_stuck_recall_aborts_with_b_on_map_target_menu():
    mem = {'chapter': 1, 'tick': 100, '_records': [],
           'recall': {'stage': 'menu', 'steps': p.RECALL_LIMIT, 'hero': False}}
    screen = Screen([], None, '', kind='map_target')
    assert p.camp_recall_step(screen, mem, None) == [p.pad('b')]
    assert 'recall' not in mem


def test_stuck_recall_aborts_with_b_on_world_map_dest():
    mem = {'chapter': 1, 'tick': 100, '_records': [],
           'recall': {'stage': 'dest', 'steps': 0, 'picker_observations': p.RECALL_LIMIT - 1}}
    screen = Screen([], None, '', kind='world_map')
    assert p.world_map_step(screen, mem, None) == [p.pad('b')]
    assert 'recall' not in mem


def test_stuck_recall_aborts_with_b_on_map_target_to_camp():
    mem = {'chapter': 1, 'tick': 100, '_records': [],
           'recall': {'stage': 'to_camp', 'steps': 5, 'target': [1, 1], 'hero': False}}
    screen = Screen([], None, '', kind='map_target')
    assert p.camp_recall_step(screen, mem, None) == [p.pad('b')]
    assert 'recall' not in mem

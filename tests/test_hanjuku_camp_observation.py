"""Camp actions need a current tent and yield to real battle foregrounds."""
from copy import deepcopy

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku_screen import Screen


MAP = Screen([], None, '', kind='map', cursor=(120, 100))


def memory():
    return {'chapter': 1, 'tick': 100,
            'recall': {'stage': 'to_camp', 'steps': 0, 'target': [120, 100]}}


@pytest.mark.parametrize('frame', [None, object()])
def test_a_missing_current_tent_never_confirms_the_saved_screen_coordinate(monkeypatch, frame):
    monkeypatch.setattr(p, 'own_camps', lambda _frame: [])
    mem = memory()
    for _ in range(3):
        assert p.camp_recall_step(MAP, mem, frame) == []
    assert 'recall' not in mem
    assert mem['recall_verification']['status'] == 'camp_unobserved'
    assert not mem['recall_verification'].get('input_sent')


def test_a_fresh_tent_replaces_the_old_coordinate_before_any_confirmation(monkeypatch):
    monkeypatch.setattr(p, 'own_camps', lambda _frame: [{'target': (150, 100)}])
    mem = memory()
    assert p.camp_recall_step(MAP, mem, object()) == [p.pad('right', 8)]
    assert mem['recall']['target'] == [150, 100]
    assert mem['recall']['stage'] == 'to_camp'


def test_one_missing_read_can_recover_only_from_a_new_visible_tent(monkeypatch):
    current = []
    monkeypatch.setattr(p, 'own_camps', lambda _frame: current)
    mem = memory()
    assert p.camp_recall_step(MAP, mem, object()) == []
    current.append({'target': (120, 100)})
    assert p.camp_recall_step(MAP, mem, object()) == [p.pad('a')]
    assert mem['recall']['stage'] == 'menu'
    assert not mem['recall'].get('camp_missing_reads')


@pytest.mark.parametrize('kind', ['battle', 'battle_menu', 'egg_battle_menu', 'monster_menu',
                                'attack_started', 'defense_started', 'boss_attack_started'])
@pytest.mark.parametrize('stage', ['to_camp', 'await_dispatch'])
def test_combat_foreground_keeps_the_pending_recall_but_does_not_advance_it(monkeypatch, kind, stage):
    monkeypatch.setattr(p, 'own_camps', lambda _frame: [{'target': (120, 100)}])
    mem = memory()
    mem['recall']['stage'] = stage
    before = deepcopy(mem['recall'])
    assert p.camp_recall_step(Screen([], None, '', kind=kind), mem, object()) is None
    assert mem['recall'] == before


def test_a_map_frame_with_an_unfinished_battle_does_not_start_camp_movement(monkeypatch):
    monkeypatch.setattr(p, 'own_camps', lambda _frame: [{'target': (120, 100)}])
    mem = {'chapter': 1, 'battle': {'ally': 'ココット', 'away': 1}}
    assert p.camp_recall_step(MAP, mem, object()) == []
    assert 'recall' not in mem


def test_a_battle_result_frame_does_not_discard_an_already_pending_camp(monkeypatch):
    monkeypatch.setattr(p, 'own_camps', lambda _frame: [{'target': (120, 100)}])
    mem = memory()
    mem['battle'] = {'ally': 'ココット', 'away': 1}
    before = deepcopy(mem['recall'])
    assert p.camp_recall_step(MAP, mem, object()) == []
    assert mem['recall'] == before

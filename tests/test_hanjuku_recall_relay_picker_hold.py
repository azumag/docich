"""The picker screen keeps the relay aim until the camp menu returns (#4533).

g1190 2026-11-18 09:53: relay_from_picker re-aimed camp (140,112) at the
relay cell and sent B, but the very next observation was still the picker.
world_map_step owns that screen, ran its destination logic, dropped the
recall flow, and the post-month task finished as if the recall was done.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich.hanjuku.flows import map as m
from docich.hanjuku.flows import recall as r
from docich.hanjuku.os_layer.commands import pad
from docich.hanjuku.os_layer.screen import Screen
from docich.hanjuku.state.regions import flow as _flow

PICKER = Screen([], None, '', kind='world_map')


def memory():
    mem = {'chapter': 11, 'tick': 100, '_records': [], 'captured': ['タタール'],
           'garrison': {}, 'recall': {'stage': 'menu', 'steps': 0, 'target': [140, 112],
                                      'camp_world': [638, 348],
                                      'relay': {'cell': [508, 264], 'hub': 'タタール'},
                                      'move_visible': {'cell': [508, 264], 'relay': True}}}
    mem['flow'] = {'recall': mem['recall']}
    return mem


def test_the_picker_holds_instead_of_dropping_the_relay_aim(monkeypatch):
    mem = memory()
    recall = _flow(mem)['recall']
    monkeypatch.setattr(m, 'world_markers', lambda *_: [])
    actions = m.world_map_step(PICKER, mem, None)
    assert actions == []
    assert _flow(mem)['recall'] is recall
    assert recall['relay_menu_waits'] == 1


def test_the_hold_is_bounded(monkeypatch):
    mem = memory()
    recall = _flow(mem)['recall']
    monkeypatch.setattr(m, 'world_markers', lambda *_: [])
    for _ in range(r.RELAY_MENU_WAIT):
        assert m.world_map_step(PICKER, mem, None) == []
    assert _flow(mem).get('recall') is recall
    assert recall['relay_menu_waits'] == r.RELAY_MENU_WAIT

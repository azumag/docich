"""Measured field-menu status panels (出撃ステータス / てきぜんいん) close with B.

Chapter 12 run: the panel sprite animates, so every frame is a new digest and
the frozen-screen fallback never fired; thousands of no-input holds followed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich.hanjuku import orchestrator as O
from docich.hanjuku_screen import Screen


def _screen(text, kind='text', hand=(40, 20, 60, 34)):
    return Screen(kind=kind, text=text, hand=hand, lines=[])


def test_status_panel_closes_with_b_after_bounded_holds():
    mem = {'_records': [], 'chapter': 12, 'tick': 100}
    screen = _screen('しゅつげステータス')
    for _ in range(O.STATUS_PANEL_HOLD_LIMIT - 1):
        assert O.status_panel_close_step(screen, mem, 'field_menu', []) is None
    assert O.status_panel_close_step(screen, mem, 'field_menu', []) == [O.pad('b')]
    assert any(r['decision'] == 'status_panel_close' for r in mem['_records'])


def test_ordinary_dialogue_is_not_closed():
    mem = {'_records': [], 'chapter': 12, 'tick': 100}
    screen = _screen('かれは つぎの たたかいに そなえた')
    for _ in range(5):
        assert O.status_panel_close_step(screen, mem, 'field_menu', []) is None


def test_panel_with_an_owned_action_is_left_alone():
    mem = {'_records': [], 'chapter': 12, 'tick': 100}
    screen = _screen('しゅつげステータス')
    assert O.status_panel_close_step(screen, mem, 'field_menu', [O.pad('a')]) is None

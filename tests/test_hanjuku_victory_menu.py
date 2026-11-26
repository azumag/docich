"""A battle menu left open after the enemy reached HP0 must advance with A.

Measured chapter 5 (クロチェット defence): ヴィーナス 82 vs マングスチン 0 and
the command menu stayed on screen with no input until input_stalled.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich.hanjuku import orchestrator as O
from docich.hanjuku_screen import Screen


def _screen():
    return Screen(kind='battle_menu', text='こうげき', hand=None, lines=[])


def _mem(enemy_hp=0):
    return {'battle': {'ally': 'ヴィーナス', 'enemy': 'マングスチン',
                       'ally_hp': 82, 'enemy_hp': enemy_hp},
            '_records': []}


def test_enemy_at_zero_advances_after_the_bound():
    screen, mem = _screen(), _mem()
    for _ in range(O.VICTORY_MENU_HOLD_LIMIT - 1):
        assert O.victory_menu_advance(screen, mem, 'battle_menu', 'field', []) is None
    assert O.victory_menu_advance(screen, mem, 'battle_menu', 'field', []) == [O.pad('a')]
    assert any(r['decision'] == 'victory_menu_advance' for r in mem['_records'])


def test_a_live_enemy_is_left_alone():
    screen, mem = _screen(), _mem(enemy_hp=12)
    for _ in range(6):
        assert O.victory_menu_advance(screen, mem, 'battle_menu', 'field', []) is None


def test_a_planned_action_is_never_overridden():
    assert O.victory_menu_advance(_screen(), _mem(), 'battle_menu', 'field', [O.pad('b')]) is None

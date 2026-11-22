"""A plain こうげき/たいきゃく list is a normal combat menu, not a held parent.

g1190 chapter 9 2026-11-15: ``ねばねば!! こうげき/たいきゃく`` had no battle
record, took the orphan-menu path and held until input_stalled.
"""
from pathlib import Path

from docich.hanjuku_bot import decide
from docich.hanjuku.flows.battle_menus import orphan_battle_menu_step
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse

ROOT = Path(__file__).resolve().parents[1]
FRAME = ROOT / 'tests' / 'fixtures' / 'hanjuku' / 'g1190-ch9-nebaneba-battle-menu.png'


def test_a_plain_attack_menu_acts_instead_of_holding():
    screen = parse(read_png(FRAME))
    assert screen.kind == 'battle_menu'
    assert orphan_battle_menu_step(screen, {}) == [{'type': 'pad', 'buttons': ['a'], 'hold_ms': 100}]


def test_the_decision_sends_the_attack_and_logs_the_reason():
    actions, result = decide(read_png(FRAME), {'policy': {'chapter': 9, 'month': '55-3',
                                                          'name': {'done': True}}})
    assert actions == [{'type': 'pad', 'buttons': ['a'], 'hold_ms': 100}]
    assert result['_records'][-1]['decision'] == 'battle_menu_plain_command'

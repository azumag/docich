"""After a plain attack the fade/result frames advance, not hold forever.

g1190 chapter 9 2026-11-15: one ``battle_menu_plain_command`` A was sent, the
battle animation frame took the orphan path again and the run ended with
input_stalled.
"""
from pathlib import Path

from docich.hanjuku.flows.battle_menus import orphan_battle_menu_step, pad
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import Screen, parse

ROOT = Path(__file__).resolve().parents[1]
MENU = ROOT / 'tests' / 'fixtures' / 'hanjuku' / 'g1190-ch9-nebaneba-battle-menu.png'


def fade():
    return Screen([], None, '', kind='unknown')


def flow(plain=True):
    return {'orphan_battle_menu': {'identity': None, 'attempts': 3, 'unconfirmed': True,
                                   'plain_command': plain}}


def test_choosing_the_plain_attack_remembers_it_for_the_fade():
    screen = parse(read_png(MENU))
    assert screen.kind == 'battle_menu'
    mem = {}
    assert orphan_battle_menu_step(screen, mem) == [pad('a')]
    assert mem['orphan_battle_menu']['plain_command'] is True


def test_the_plain_attacks_fade_advances_boundedly():
    mem = flow()
    assert orphan_battle_menu_step(fade(), mem) == [pad('a')]
    assert orphan_battle_menu_step(fade(), mem) == []
    assert orphan_battle_menu_step(fade(), mem) == [pad('a')]
    assert mem['_records'][-1]['decision'] == 'battle_menu_plain_advance'


def test_a_gray_orphan_fade_still_holds():
    mem = flow(plain=False)
    assert orphan_battle_menu_step(fade(), mem) == []

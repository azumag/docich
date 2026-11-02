"""#2979: a summon tries its other skill when one never moves the enemy's HP.

g1075-1d5f65e9 2026-10-29 JST: a defending おめでとり used ミカンをおす 30
times (13:37-13:57) while ラズベリー stayed at 45 HP and its own HP fell 1000
-> 603. g1075-omedetori-skill-menu.png is one of those menus (13:41, ally
1000 HP, menu ミカンをおす / あたためる / たまごにもどれ, cursor on row 1).
"""
from pathlib import Path
from types import SimpleNamespace

from docich.hanjuku.flows.battle_menus import (MONSTER_STALL_TURNS, _monster_stall_switch,
                                                monster_menu_step)
from docich.hanjuku.os_layer.phase import classify
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import parse

ROOT = Path(__file__).resolve().parents[1]
MENU = ROOT / 'tests' / 'fixtures' / 'hanjuku' / 'g1075-omedetori-skill-menu.png'


def screen():
    frame = read_png(MENU)
    return parse(frame, phase=classify(frame))


def turn(sc, mem, observations=20):
    """One own turn: many observations later, a new menu draw forgets the cached choice."""
    mem['tick'] += observations
    mem.pop('monster_menu_choice', None)
    mem['_records'] = []
    monster_menu_step(sc, mem)
    return mem['monster_menu_choice'], [r.get('decision') for r in mem['_records']]


def test_the_saved_frame_is_the_omedetori_menu_against_raspberry():
    sc = screen()
    assert sc.kind == 'monster_menu'
    assert [''.join(r.known.split()) for r in sc.menu_rows] == ['ミカンをおす', 'あたためる', 'たまごにもどれ']
    assert {r.side: (r.name, r.hp) for r in sc.egg_rows if r.side} == {
        'ally': ('おめでとり', 1000), 'enemy': ('ラズベリー', 45)}


def test_mikan_once_then_warm_is_the_omedetori_order_and_no_stall_is_counted():
    # #2945 owner rule supersedes the #2979 count for おめでとり: ミカン once, then あたためる.
    sc = screen()
    mem = {'chapter': 3, 'tick': 1, 'battle': {'ally': 'ココット', 'enemy': 'ラズベリー'}}
    sc.menu_cursor = sc.menu_rows[0].y
    assert turn(sc, mem)[0] == 'skill1'
    assert mem['battle']['omedetori_cycle']['mikan_submitted'] is True
    sc.menu_cursor = sc.menu_rows[1].y
    for _ in range(2 * MONSTER_STALL_TURNS):
        choice, records = turn(sc, mem)
        assert choice == 'skill2'
        assert 'monster_skill_stall' not in records
    assert 'monster_stall' not in mem


def unit(name, hp):
    return SimpleNamespace(name=name, hp=hp)


def test_a_change_in_the_enemy_hp_starts_the_count_over():
    mem = {'tick': 0}
    ally = unit('おめでとり', 900)
    for _ in range(MONSTER_STALL_TURNS):
        mem['tick'] += 20
        assert _monster_stall_switch(mem, 'ally', ally, unit('ラズベリー', 45), 'skill1', 2) == 'skill1'
    mem['tick'] += 20
    assert _monster_stall_switch(mem, 'ally', ally, unit('ラズベリー', 40), 'skill1', 2) == 'skill1'
    assert mem['monster_stall']['turns'] == {'skill1': 1}


def test_another_pair_starts_its_own_count():
    mem = {'tick': 0}
    for _ in range(MONSTER_STALL_TURNS):
        mem['tick'] += 20
        _monster_stall_switch(mem, 'ally', unit('おめでとり', 900), unit('ラズベリー', 45), 'skill1', 2)
    assert _monster_stall_switch(mem, 'ally', unit('なると', 300), unit('ラズベリー', 45), 'skill1', 2) == 'skill1'


def test_enemy_menus_single_skill_menus_and_retreats_are_left_alone():
    mem = {'monster_stall': {'scope': ['おめでとり', 'ラズベリー'], 'enemy_hp': 45,
                             'turns': {'skill1': MONSTER_STALL_TURNS}}}
    ally, enemy = unit('おめでとり', 900), unit('ラズベリー', 45)
    assert _monster_stall_switch(mem, 'enemy', ally, enemy, 'skill1', 2) == 'skill1'
    assert _monster_stall_switch(mem, 'ally', ally, enemy, 'skill1', 1) == 'skill1'
    assert _monster_stall_switch(mem, 'ally', ally, enemy, 'retreat', 2) == 'retreat'
    assert _monster_stall_switch(mem, 'ally', ally, unit('ラズベリー', None), 'skill1', 2) == 'skill1'

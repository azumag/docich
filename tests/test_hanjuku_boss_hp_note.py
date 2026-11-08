"""Keep the boss monster's displayed HP even when the battle cannot be recorded.

g1133 2026-11-06: the boss fight ran but the ally panel was unreadable, so no
battle_start/battle_result survived. The enemy monster (ハードロボ) HP was
readable and fell 1041 -> 1005 -> 860 -> 754 across entries.
"""
from types import SimpleNamespace

from docich.hanjuku.flows import battle_menus as B


def memory(castle='ボス'):
    return {'tick': 5000, '_records': [],
            'attack': {'castle': castle, 'general': 'ポセイドン', 'side': 'attack', 'step': 'I:x'}}


def test_a_boss_monster_hp_is_remembered():
    mem = memory()
    B.note_boss_monster_hp(mem, {'ally': 'ポセイドン'}, [SimpleNamespace(name='ハードロボ', hp=754)])
    assert mem['boss_hp_seen'] == {'castle': 'ボス', 'monster': 'ハードロボ', 'hp': 754,
                                   'general': 'ポセイドン', 'tick': 5000}


def test_a_normal_castle_does_not_write_boss_progress():
    mem = memory('オケアナ')
    B.note_boss_monster_hp(mem, {'ally': 'ポセイドン'}, [SimpleNamespace(name='ハードロボ', hp=754)])
    assert 'boss_hp_seen' not in mem


def test_an_unreadable_hp_is_not_invented():
    mem = memory()
    B.note_boss_monster_hp(mem, {}, [SimpleNamespace(name='ハードロボ', hp=None)])
    B.note_boss_monster_hp(mem, {}, [])
    mem['attack'] = {'castle': 'ボス'}
    B.note_boss_monster_hp(mem, {}, [SimpleNamespace(name=None, hp=100)])
    assert 'boss_hp_seen' not in mem

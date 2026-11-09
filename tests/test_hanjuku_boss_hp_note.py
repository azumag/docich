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


def test_the_boss_panel_readings_keep_a_bounded_history():
    """The boss's HP falling across entries is the only proof a wave hurt it."""
    mem = {'chapter': 7, 'tick': 100, '_records': [],
           'attack': {'general': 'ミモザ', 'castle': 'ボス', 'side': 'attack', 'step': 'A:x:M1'}}
    B.note_boss_monster_hp(mem, {'ally': 'ミモザ'}, [SimpleNamespace(name='ランパイア', hp=426)])
    mem['tick'] = 200
    mem['attack']['general'] = 'ココット'
    B.note_boss_monster_hp(mem, {'ally': 'ココット'},
                           [SimpleNamespace(name='ランパイア', hp=298)])
    assert [row['hp'] for row in mem['boss_hp_history']] == [426, 298]
    assert [row['general'] for row in mem['boss_hp_history']] == ['ミモザ', 'ココット']


def test_a_wave_that_left_the_hp_unchanged_is_remembered():
    """g1163 chapter 7: the boss sat at 426 through one arrival."""
    mem = {'chapter': 7, 'tick': 100, '_records': [],
           'attack': {'general': 'ココット', 'castle': 'ボス', 'side': 'attack', 'step': 'A:x:M1'}}
    B.note_boss_monster_hp(mem, {'ally': 'ココット'}, [SimpleNamespace(name='ランパイア', hp=426)])
    mem['tick'] = 200
    mem['attack']['general'] = 'ミモザ'
    B.note_boss_monster_hp(mem, {'ally': 'ミモザ'}, [SimpleNamespace(name='ランパイア', hp=426)])
    assert mem['boss_no_damage'] == ['ココット']
    assert [row['hp'] for row in mem['boss_hp_history']] == [426, 426]

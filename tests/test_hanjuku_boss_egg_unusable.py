"""A greyed egg row in a boss fight marks the general as unable to summon.

g1163 chapter 6 2026-11-09 13:36: ヘーゼル entered ハードロボ (152 HP), the egg
row read as unavailable, the fight dealt no damage, and the same general was
queued for the next wave.
"""
from types import SimpleNamespace

from docich import hanjuku_chart as chart
from docich.hanjuku.flows import battle_menus as B
from docich.hanjuku.os_layer.screen import Screen

BOSS = chart.boss_castle(6)


def screen(text='たまごをつかうきりふだたいきゃく', kind='egg_battle_menu'):
    value = Screen([], None, text, kind=kind)
    return value


def memory(castle=BOSS, general='ヘーゼル', uses=None):
    mem = {'chapter': 6, 'tick': 100, '_records': [],
           'attack': {'general': general, 'castle': castle, 'side': 'attack', 'step': 'A:x:M1'},
           'battle': {'ally': general, 'enemy': 'ハードロボ', 'step': 'A:x:M1',
                      'castle': castle, 'side': 'attack'},
           'sorties': {'A:x:M1': {'general': general, 'target': castle,
                                  'status': 'arrived', 'tick': 90}}}
    if uses is not None:
        mem['egg_uses'] = {general: uses}
    return mem


def test_a_boss_entry_with_a_grey_egg_row_records_the_general_as_unusable():
    mem = memory()
    B.egg_battle_step(screen(text='こうげき'), mem)
    assert mem['egg_uses']['ヘーゼル'] == 0
    assert any(r['decision'] == 'egg_unusable_recorded' for r in mem['_records'])


def test_a_normal_castle_battle_never_records_it():
    mem = memory(castle='セレーネ')
    B.egg_battle_step(screen(text='こうげき'), mem)
    assert not (mem.get('egg_uses') or {})
    assert not any(r['decision'] == 'egg_unusable_recorded' for r in mem['_records'])

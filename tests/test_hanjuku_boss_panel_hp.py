"""A boss battle's opening panel records the boss HP.

Live g1288 2026-11-20 20:46: the first ch9 boss battle happened (ambush
contact) but ``boss_hp_seen`` stayed None — the existing recorder keys off a
charted boss sortie's plan, and the ambush path has none. The battle panel's
enemy HP is the progress reading, so record it by enemy name.
"""
from types import SimpleNamespace

from docich import hanjuku_chart as chart
from docich.hanjuku.flows import battle


def fake(enemy, hp, ally='どうし'):
    return SimpleNamespace(enemy=enemy, enemy_hp=hp, ally=ally)


def test_a_boss_panel_records_hp_and_history():
    mem = {'chapter': 9, 'tick': 100}
    battle._note_boss_panel_hp(mem, fake('ハデス', 420))
    assert mem['boss_hp_seen']['monster'] == 'ハデス'
    assert mem['boss_hp_seen']['hp'] == 420
    battle._note_boss_panel_hp(mem, fake('ハデス', 350))
    assert [row['hp'] for row in mem['boss_hp_history']] == [420, 350]


def test_an_ordinary_enemy_is_not_recorded():
    mem = {'chapter': 9, 'tick': 100}
    battle._note_boss_panel_hp(mem, fake('コリアンダー', 85))
    assert 'boss_hp_seen' not in mem


def test_an_invalid_hp_is_ignored():
    mem = {'chapter': 9, 'tick': 100}
    battle._note_boss_panel_hp(mem, fake('ハデス', 0))
    battle._note_boss_panel_hp(mem, fake('ハデス', None))
    assert 'boss_hp_seen' not in mem

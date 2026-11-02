"""Owner 2026-11-02: ボスモンスターに「かぼちゃ」はきかない。

ランプキン vs a chapter boss or named boss-general always takes
「へんなおどり」 (skill1); the wasted かぼちゃ turn is never sent. No native
ランプキン menu frame is kept, so the native g749 monster menu is reused with
substituted panel names/HP and skill rows — the decision path, not the reader.
"""
from dataclasses import replace
import pytest
from docich.hanjuku.flows.battle_menus import monster_menu_step
from docich.hanjuku.judgment.events import _monster_lampkin_choice
from docich.hanjuku.os_layer.commands import pad
from docich.hanjuku.os_layer.font import TextLine
from test_hanjuku_naruto_damage import native

SKILLS = ['へんなおどり', 'かぼちゃ']


def row(y, text):
    return TextLine(y=y, cells=tuple((176 + 8 * i, ch) for i, ch in enumerate(text)))


def menu(enemy, enemy_hp, ally_hp=95, skills=SKILLS):
    s = native(8549)
    s.menu_rows = [row(176, skills[0]), row(192, skills[1]), s.menu_rows[2]]
    s.egg_rows = [replace(r, name='ランプキン', hp=ally_hp) if r.side == 'ally'
                  else replace(r, name=enemy, hp=enemy_hp) for r in s.egg_rows]
    return s


def memory():
    return {'chapter': 1, 'battle': {'ally': 'ヴィーナス', 'enemy': 'クイーン', 'ally_hp': 60,
                                     'enemy_hp': 70, 'side': 'attack', 'castle': 'けっかい'}}


def panel(s):
    return {r.side: r for r in s.egg_rows}


@pytest.mark.parametrize('enemy', ['クイーン', 'にせヒーロー', 'プリンス', 'せいめいたい',
                                   '四季王', 'だいまおう', 'ハードマン', 'ランパイア',
                                   'ノブナーガ', 'ハデス', 'だいとうりょう', 'エッグママ', 'クーモン'])
def test_every_boss_takes_hennaodori(enemy):
    s = menu(enemy, 500, ally_hp=10)   # even behind, かぼちゃ never goes out
    m = memory()
    assert monster_menu_step(s, m) == [pad('a')]
    assert m['monster_menu_choice'] == 'skill1'
    rec = m['_records'][-1]
    assert rec['observed_metric']['lampkin_choice'] == 'skill1'
    assert 'へんなおどり' in rec['reason'] and 'かぼちゃ' in rec['reason']


def test_experience_and_stall_never_switch_to_kabocha():
    m = memory()
    s = menu('クイーン', 500, ally_hp=10)
    m['monster_menu_choice'] = 'skill2'     # a stale/learned pumpkin pick is discarded
    m['monster_menu_choice_hp'] = (10, 500)
    assert monster_menu_step(s, m) == [pad('a')]
    assert m['monster_menu_choice'] == 'skill1'


@pytest.mark.parametrize('enemy', ['レモン', 'ニンフ', 'スプリミョーネ'])
def test_generals_and_ordinary_monsters_keep_existing_policy(enemy):
    s = menu(enemy, 50)
    assert _monster_lampkin_choice('ally', panel(s)['ally'], panel(s)['enemy'], SKILLS) is None


def test_enemy_owned_or_partial_menu_never_forces_skill1():
    s = menu('クイーン', 500, skills=['かぼちゃ', 'へんなおどり'])  # wrong order: not our pair
    assert _monster_lampkin_choice('ally', panel(s)['ally'], panel(s)['enemy'],
                                   ['かぼちゃ', 'へんなおどり']) is None
    s2 = menu('クイーン', 500, skills=['へんなおどり', 'かぼちゃ'])
    assert _monster_lampkin_choice('enemy', panel(s2)['ally'], panel(s2)['enemy'], SKILLS) is None

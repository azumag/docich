"""#3071 ちきゅうちゃん: ハルマゲドン finishes a lone general, だいじしん otherwise.

No native ちきゅうちゃん menu frame is kept. These tests reuse the native g749
monster menu (なると) and substitute the panel names/HP and the two skill
rows, so they check the decision path, not the screen reader.
"""
from dataclasses import replace
import pytest
from docich.hanjuku.flows.battle_menus import monster_menu_step
from docich.hanjuku.judgment.events import _monster_chikyu_choice
from docich.hanjuku.os_layer.commands import pad
from docich.hanjuku.os_layer.font import TextLine
from test_hanjuku_naruto_damage import native

SKILLS = ['だいじしん', 'ハルマゲドン']


def row(y, text):
    return TextLine(y=y, cells=tuple((176 + 8 * i, ch) for i, ch in enumerate(text)))


def menu(enemy, enemy_hp, ally_hp=95, skills=SKILLS):
    s = native(8549)
    s.menu_rows = [row(176, skills[0]), row(192, skills[1]), s.menu_rows[2]]
    s.egg_rows = [replace(r, name='ちきゅうちゃん', hp=ally_hp) if r.side == 'ally'
                  else replace(r, name=enemy, hp=enemy_hp) for r in s.egg_rows]
    return s


def memory():
    return {'chapter': 4, 'battle': {'ally': 'ブカティーニ', 'enemy': 'レモン', 'ally_hp': 14,
                                     'enemy_hp': 6, 'side': 'attack', 'castle': 'フヌイヌム'}}


def panel(s):
    return {r.side: r for r in s.egg_rows}


def test_lone_general_takes_harmagedon_g1075_0922():
    # 09:22:04 だいじしん at 95 against レモン 6 let him summon ニンフ.
    m = memory()
    assert monster_menu_step(menu('レモン', 6), m) == [pad('down')]
    assert m['monster_menu_choice'] == 'skill2'
    rec = m['_records'][-1]
    assert rec['observed_metric']['chikyu_choice'] == 'skill2'
    assert 'ハルマゲドン' in rec['reason'] and '全滅' in rec['reason']


def test_full_hp_general_also_takes_harmagedon():
    m = memory()
    assert monster_menu_step(menu('フェットチーネ', 67, ally_hp=9999), m) == [pad('down')]
    assert m['monster_menu_choice'] == 'skill2'


@pytest.mark.parametrize('enemy,enemy_hp,ally_hp', [
    ('ニンフ', 50, 47),            # 09:22:31 behind: ハルマゲドン left ニンフ alive
    ('ドラゴンフライ', 65, 1),      # 08:08:48 behind
    ('ハーフドラゴン', 135, 4999),  # ahead against a monster
])
def test_monster_keeps_daijishin_even_when_behind(enemy, enemy_hp, ally_hp):
    m = memory()
    assert monster_menu_step(menu(enemy, enemy_hp, ally_hp), m) == [pad('a')]
    assert m['monster_menu_choice'] == 'skill1'
    rec = m['_records'][-1]
    assert rec['observed_metric']['chikyu_choice'] == 'skill1'
    assert 'だいじしん' in rec['reason']


@pytest.mark.parametrize('enemy', ['クイーン', 'せいめいたい', 'スプリミョーネ', 'だいまおう'])
def test_bosses_keep_daijishin(enemy):
    s = menu(enemy, 500, ally_hp=9999)
    assert _monster_chikyu_choice('ally', panel(s)['ally'], panel(s)['enemy'], SKILLS) == 'skill1'


@pytest.mark.parametrize('enemy', ['不明', 'レモ'])
def test_unknown_opponent_keeps_existing_policy(enemy):
    s = menu(enemy, 6)
    assert _monster_chikyu_choice('ally', panel(s)['ally'], panel(s)['enemy'], SKILLS) is None


@pytest.mark.parametrize('side,hp', [('ally', None), ('enemy', None), ('ally', 0),
                                     ('enemy', 0), ('ally', True), ('enemy', True)])
def test_unread_dead_or_bool_hp_keeps_existing_policy(side, hp):
    s = menu('レモン', 6)
    s.egg_rows = [replace(r, hp=hp) if r.side == side else r for r in s.egg_rows]
    assert _monster_chikyu_choice('ally', panel(s)['ally'], panel(s)['enemy'], SKILLS) is None


@pytest.mark.parametrize('owner', ['enemy', None])
def test_owner_must_be_ally(owner):
    s = menu('レモン', 6)
    assert _monster_chikyu_choice(owner, panel(s)['ally'], panel(s)['enemy'], SKILLS) is None


@pytest.mark.parametrize('skills', [['だいじしん'], ['ハルマゲドン', 'だいじしん'],
                                    ['だいじしん', 'ハルマゲド'], SKILLS + ['ふくらむ']])
def test_complete_ordered_pair_required(skills):
    s = menu('レモン', 6)
    assert _monster_chikyu_choice('ally', panel(s)['ally'], panel(s)['enemy'], skills) is None


def test_other_monster_with_same_opponent_is_untouched():
    s = menu('レモン', 6)
    ally = replace(panel(s)['ally'], name='なると')
    assert _monster_chikyu_choice('ally', ally, panel(s)['enemy'], SKILLS) is None


@pytest.mark.parametrize('enemy,enemy_hp,learned,want,move', [
    ('レモン', 6, 'skill1', 'skill2', 'down'),
    ('ニンフ', 50, 'skill2', 'skill1', 'a'),
])
def test_learned_experience_cannot_override(monkeypatch, enemy, enemy_hp, learned, want, move):
    from docich.hanjuku.flows import battle_menus
    monkeypatch.setattr(battle_menus.experience, 'preferred', lambda *a, **kw: learned)
    m = memory()
    assert monster_menu_step(menu(enemy, enemy_hp, ally_hp=47), m) == [pad(move)]
    assert m['monster_menu_choice'] == want


def test_hotload_cached_daijishin_at_same_hp_is_redecided():
    s = menu('レモン', 6)
    m = memory()
    monster_menu_step(s, m)
    m['monster_menu_choice'] = 'skill1'
    m.pop('monster_menu_cursor', None)
    assert monster_menu_step(s, m) == [pad('down')]
    assert m['monster_menu_choice'] == 'skill2'

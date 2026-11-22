"""Owner 2026-11-02: ボスモンスターに「かぼちゃ」はきかない。

ランプキン vs a chapter boss, named boss-general or boss monster always takes
「へんなおどり」 (skill1); the wasted かぼちゃ turn is never sent. A boss
castle's monster changes form (第6話 ハードマン→ハードロボ, 第12話
クーモン→スーモン→ボイルド), and the later forms are not in chart.BOSSES, so
they regressed until 2026-11-05. No native ランプキン menu frame is kept, so
the native g749 monster menu is reused with substituted panel names/HP and
skill rows — the decision path, not the reader.
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
                                   'ノブナーガ', 'ハデス', 'だいとうりょう', 'エッグママ', 'クーモン',
                                   'ハードロボ', 'スーモン', 'ボイルド'])
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


# ---- owner 2026-11-02: かぼちゃ1回→へんなおどり3回の繰り返し / HP1は一択 ----
from docich.hanjuku.judgment.battle import reset_battle_controls

ROW_Y = {'skill1': 176, 'skill2': 192}


def turn(m, enemy='ニンフ', enemy_hp=50, tick=None, ally_hp=95):
    """One menu session: decide, put the cursor on the chosen row, confirm."""
    if tick is not None:
        m['tick'] = tick
    s = menu(enemy, enemy_hp, ally_hp=ally_hp)
    monster_menu_step(s, m)                     # decide (may move the cursor)
    s = menu(enemy, enemy_hp, ally_hp=ally_hp)
    s.menu_cursor = ROW_Y[m['monster_menu_choice']]
    assert monster_menu_step(s, m) == [pad('a')]
    return m['monster_menu_choice']


def leave_menu(m):
    """What the orchestrator does when the menu is no longer on screen."""
    for key in ('monster_menu_key', 'monster_menu_cursor', 'monster_menu_hold',
                'monster_menu_choice', 'monster_menu_choice_key', 'monster_panel'):
        m.pop(key, None)


@pytest.mark.parametrize('enemy', ['ニンフ', 'レモン'])   # an ordinary monster and a general
def test_kabocha_once_then_three_hennaodori_repeatedly(enemy):
    m = memory()
    seq = []
    for i in range(9):
        seq.append(turn(m, enemy, tick=100 + 10 * i))
        leave_menu(m)
    assert seq == ['skill2', 'skill1', 'skill1', 'skill1'] * 2 + ['skill2']
    assert 'かぼちゃ' in [r for r in m['_records'] if r['decision'] == 'monster_menu_choice'][0]['reason']


def test_the_same_confirm_seen_again_is_one_turn_not_two():
    m = memory()
    assert turn(m, tick=100) == 'skill2'
    assert m['lampkin_cycle']['n'] == 1
    # The menu is still up (or redrawn) a moment after the A: same pending turn.
    leave_menu(m)
    assert turn(m, tick=101) == 'skill2'
    assert m['lampkin_cycle']['n'] == 1
    leave_menu(m)
    assert turn(m, tick=110) == 'skill1'          # a real next turn
    assert m['lampkin_cycle']['n'] == 2


def test_later_boss_monster_form_never_sends_kabocha():
    """live g1133 (2026-11-05): ランプキン vs ハードロボ HP1127 sent かぼちゃ."""
    m = memory()
    m['chapter'] = 6
    m['battle']['enemy'] = 'ハードロボ'
    s = menu('ハードロボ', 1127, ally_hp=100)
    assert _monster_lampkin_choice('ally', panel(s)['ally'], panel(s)['enemy'], SKILLS) == 'skill1'
    assert monster_menu_step(s, m) == [pad('a')]
    rec = m['_records'][-1]
    assert m['monster_menu_choice'] == 'skill1'
    assert rec['observed_metric']['lampkin_choice'] == 'skill1'
    assert 'ボスモンスター' in rec['reason'] and 'かぼちゃ' in rec['reason']
    assert 'lampkin_cycle' not in m


@pytest.mark.parametrize('enemy', ['クイーン', 'ハードロボ', 'スーモン'])
def test_boss_is_always_hennaodori_and_does_not_advance_the_cycle(enemy):
    m = memory()
    for i in range(5):
        assert turn(m, enemy, 500, tick=100 + 10 * i) == 'skill1'
        leave_menu(m)
    assert m.get('lampkin_cycle', {}).get('n', 0) == 0


def test_enemy_at_one_hp_is_hennaodori_and_the_cycle_resumes_after():
    m = memory()
    assert turn(m, enemy_hp=40, tick=100) == 'skill2'; leave_menu(m)
    assert turn(m, enemy_hp=1, tick=110) == 'skill1'; leave_menu(m)     # HP 1: no pumpkin
    assert m['lampkin_cycle']['n'] == 1                                   # that turn was not counted
    assert 'HPが1' in [r for r in m['_records'] if r['decision'] == 'monster_menu_choice'][-1]['reason']
    assert turn(m, enemy_hp=30, tick=120) == 'skill1'; leave_menu(m)
    assert turn(m, enemy_hp=20, tick=130) == 'skill1'; leave_menu(m)
    assert turn(m, enemy_hp=10, tick=140) == 'skill1'; leave_menu(m)
    assert turn(m, enemy_hp=9, tick=150) == 'skill2'                      # 4 counted turns later


def test_a_new_opponent_or_the_end_of_the_battle_restarts_the_cycle():
    m = memory()
    assert turn(m, 'ニンフ', tick=100) == 'skill2'; leave_menu(m)
    assert turn(m, 'ニンフ', tick=110) == 'skill1'; leave_menu(m)
    assert turn(m, 'レモン', tick=120) == 'skill2'; leave_menu(m)         # another opponent
    reset_battle_controls(m)
    assert 'lampkin_cycle' not in m
    assert turn(m, 'レモン', tick=130) == 'skill2'


def test_only_our_two_skill_lampkin_menu_uses_the_cycle():
    m = memory()
    s = menu('ニンフ', 50, skills=['かぼちゃ', 'へんなおどり'])           # not the owned pair/order
    monster_menu_step(s, m)
    assert 'lampkin_cycle' not in m

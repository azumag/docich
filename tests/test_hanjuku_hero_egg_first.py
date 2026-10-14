"""g604 game over: the eggless hero met an instant-death summon in a defense.

- 主人公の卵だけでも回復する (全回復が所持金に足りない月)。
- 召喚戦では主人公は HP 優位でも卵を温存しない。
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import hanjuku_policy as policy
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import Canvas
from test_hanjuku_survival import memory, wounded_egg_screen


def recovery_list(hand_y):
    c = Canvas()
    c.text(32, 47, 'ぜんかいふく')
    c.text(32, 71, 'どうし'); c.text(104, 71, '0')
    c.text(32, 95, 'アルテミス'); c.text(104, 95, '1')
    c.hand(10, hand_y)
    return parse(c.frame())


ROSTER_WAGES = {'どうし': 0, 'ヴィーナス': 3, 'アンディーブ': 4, 'コリアンダー': 5, 'アルテミス': 4}


def g604_memory(gold, roster=True):
    mem = {'chapter': 1, 'month': '3-11', 'tick': 33830,
           'egg_uses': {'どうし': 0, 'アルテミス': 1, 'ヴィーナス': 4},
           'egg_types': {'どうし': 'エラベルエッグ', 'アルテミス': 'ワンダーエッグ'},
           'month_sub': {'kind': 'egg', 'gold_before': gold, 'presses': 0}}
    if roster:   # g604 3-11 measured payroll: 16G
        mem['recruit_roster'] = {'chapter': 1, 'month': '3-11', 'tick': 33819, 'complete': True,
                                 'names': list(ROSTER_WAGES), 'wages': dict(ROSTER_WAGES)}
    return mem


def quote(gold, text):
    c = Canvas()
    c.text(72, 15, f'3ねん 11のつき {gold}G')
    c.text(24, 183, text); c.text(184, 183, 'うむッ!'); c.text(184, 199, 'いかんッ!')
    c.hand(162, 177)
    return parse(c.frame())


def test_unaffordable_full_recovery_recovers_only_the_hero():
    mem = g604_memory(68)   # full: 2 measured eggs = 100G + 30G wage floor > 68G
    assert policy.month_sub_step(recovery_list(41), mem) == [policy.pad('down')]
    assert policy.month_sub_step(recovery_list(65), mem) == [policy.pad('a')]
    assert mem['month_sub']['hero_selected'] is True
    assert not mem['month_sub'].get('full_selected')
    assert policy.month_sub_step(quote(68, '1こで50Gになりまんな'), mem) == [policy.pad('a')]
    assert mem['month_sub']['stage'] == 'recovering'
    assert mem['month_sub']['quoted_cost'] == 50


def test_hero_only_selection_refuses_a_multi_egg_quote():
    mem = g604_memory(200)
    mem['month_sub']['hero_selected'] = True
    assert policy.month_sub_step(quote(200, '2こで100Gになりまんな'), mem) == [policy.pad('b')]


def test_hero_only_keeps_the_blind_wage_floor_without_a_payroll_reading():
    mem = g604_memory(79, roster=False)   # 50G + 30G floor = 80G
    assert policy.month_sub_step(recovery_list(65), mem) == [policy.pad('up')]
    assert not mem['month_sub'].get('hero_selected')
    assert policy._hero_egg_wage_floor(g604_memory(80, roster=False)) == 30


def test_measured_payroll_lowers_only_the_hero_floor():
    assert policy._hero_egg_wage_floor(g604_memory(68)) == 16
    mem = g604_memory(65)   # 65 - 50 = 15 < 16G payroll
    assert policy.month_sub_step(recovery_list(65), mem) == [policy.pad('up')]
    mem = g604_memory(68)
    mem['recruit_roster']['wages'].pop('アルテミス')   # one unread wage -> blind floor
    assert policy._hero_egg_wage_floor(mem) == 30


def test_affordable_full_recovery_keeps_the_full_option():
    mem = g604_memory(140)
    assert policy.month_sub_step(recovery_list(41), mem) == [policy.pad('a')]
    assert mem['month_sub']['full_selected'] is True


def test_full_hero_egg_keeps_the_full_option():
    mem = g604_memory(68)
    mem['egg_uses']['どうし'] = 4
    assert policy.month_sub_step(recovery_list(41), mem) == [policy.pad('a')]
    assert not mem['month_sub'].get('hero_selected')


def test_healthy_hero_answers_a_summon_with_his_egg():
    # g604: どうし 66/90 vs チコリ 11 -> soldier_force said "save the egg";
    # the ダークエルフ's instant death killed him.
    mem = memory(66, 11)
    mem['battle'].update(side='defense', planned_cards=[], start_ally_hp=90)
    mem['egg_uses'] = {'どうし': 4}
    screen = wounded_egg_screen(66, ally='どうし')
    screen.text += 'たまごをつかう'
    assert policy.egg_battle_step(screen, mem) == [policy.pad('down')]
    assert mem['egg_action'] == 'use_egg' and mem['egg_needed'] is True
    rec = [r for r in mem['_records'] if r['decision'] == 'egg_battle'][-1]
    assert rec['observed_metric']['egg_need']['rule'] in ('hero_vs_summon', 'ally_wounded',
                                                       'enemy_monster_threat')


def test_learned_attack_cannot_override_the_heros_egg():
    mem = memory(80, 11)
    mem['battle'].update(side='defense', planned_cards=[], start_ally_hp=90)
    mem['egg_uses'] = {'どうし': 4}
    from docich import hanjuku_experience as experience
    key = experience.situation_key('egg_summon', mem)
    mem['_experience'] = {key: {'attack': {'win': 9, 'loss': 0}}}
    screen = wounded_egg_screen(80, ally='どうし')
    screen.text += 'たまごをつかう'
    policy.egg_battle_step(screen, mem)
    assert mem['egg_action'] == 'use_egg'


def test_eggless_hero_still_falls_back_to_attack():
    mem = memory(66, 11)
    mem['battle'].update(side='defense', planned_cards=[], start_ally_hp=90)
    mem['egg_uses'] = {'どうし': 0}
    policy.egg_battle_step(wounded_egg_screen(66, ally='どうし'), mem)
    assert mem['egg_action'] == 'attack'

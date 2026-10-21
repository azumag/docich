"""Repeat uses require a real combat return; summon menus are counterfactual.

The g684 HP/monster pixels are native. The added okunote box and outcomes
below are synthetic, not evidence of a successful input or live effect.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku_bot import decide
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import Battle, Screen, parse
from test_hanjuku_chart_bot import Canvas
from test_hanjuku_defender_last_resort import memory, menu
from test_hanjuku_defender_egg_preempt import memory as preempt_memory, panel

ID = {'game': 'hanjuku-hero', 'runtime_id': 'okunote-test', 'generation': 1, 'lease_id': 'test'}


def choices():
    from test_hanjuku_defender_egg_preempt import menu as choice_menu
    s = choice_menu(('ヤケクソ', 'あやまる', 'しんだフリ'))
    s.kind = 'okunote_menu'
    return s


@pytest.mark.parametrize('route', ['last_resort', 'egg_preempt'])
def test_candidate_then_current_combat_return_allows_second_use_in_same_fight(route):
    mem = memory() if route == 'last_resort' else preempt_memory(enemy_hp=10)
    mem['_run_identity'] = deepcopy(ID)
    assert p.battle_menu_step(menu(), mem) == [p.pad('a')]
    assert p.okunote_step(choices(), mem) == [p.pad('a')]
    mem = json.loads(json.dumps(mem))
    p.battle_step(panel(mem), mem)
    assert p.battle_menu_step(menu(), mem) == [p.pad('a')]
    assert p.okunote_step(choices(), mem) == [p.pad('a')]


def summon_memory():
    return {'chapter': 1, 'tick': 100, '_run_identity': deepcopy(ID), 'survey_run_identity': deepcopy(ID),
        'egg_battle': True, 'battle': {'ally': 'アルテミス', 'enemy': 'ヘーゼル',
        'castle': 'ゴーメン', 'side': 'defense', 'start_ally_hp': 49, 'ally_hp': 49,
        'start_enemy_hp': 33, 'enemy_hp': 11, 'cards_used': [], 'planned_cards': [],
        'card_evidence_version': 1, 'egg_battle': True,
        'summon_general_reading': {'identity': deepcopy(ID), 'ally': 'アルテミス',
            'enemy': 'ヘーゼル', 'hp': 49, 'max_hp': 49}}}


def summon_frame():
    native = read_png(Path(__file__).parent / 'fixtures/hanjuku/g684-artemis-dark-elf.png')
    c = Canvas(); c.rgb = bytearray(native.rgb)
    for y in range(167, 216):
        for x in range(144, 248): c.put(x, y, (16, 72, 57))
    c.text(176, 208, 'おくのて')
    for y in range(200, 212):
        for x in range(152, 164): c.put(x, y, (230, 105, 74))
    return c.frame()


def test_current_own_general_vs_actual_monster_okunote_routes_through_decide():
    frame = summon_frame(); s = parse(frame)
    assert s.kind == 'battle_menu'
    assert [(r.side, r.name, r.hp) for r in s.egg_rows] == [
        ('ally', 'アルテミス', 49), ('enemy', 'ダークエルフ', 240)]
    actions, state = decide(frame, {'policy': summon_memory()}, run_identity=ID)
    assert actions == [p.pad('a')]
    cur = state['policy']['battle']
    assert cur['ally_hp'] == 49 and cur['enemy_hp'] == 11  # distinct HP pools


def test_unchanged_parent_and_candidate_never_resend_a_without_combat_return():
    mem = memory(); mem['_run_identity'] = deepcopy(ID)
    assert p.battle_menu_step(menu(), mem) == [p.pad('a')]
    assert p.battle_menu_step(menu(), mem) == [p.pad('b')]
    assert p.okunote_step(choices(), mem) == [p.pad('a')]
    for _ in range(20):
        mem = json.loads(json.dumps(mem))
        assert p.okunote_step(choices(), mem) != [p.pad('a')]
    assert p.battle_menu_step(menu(), mem) != [p.pad('a')]


@pytest.mark.parametrize('case', ['no_castle', 'attack', 'unknown_own', 'foreign_run', 'foreign_actor',
                                  'foreign_enemy', 'missing_binding', 'missing_panel', 'own_monster', 'unknown_monster'])
def test_summon_low_hp_exception_requires_current_defending_general_and_live_enemy(case):
    from docich.hanjuku_screen import EggRow
    mem = summon_memory(); s = parse(summon_frame())
    cur = mem['battle']
    if case == 'no_castle': cur['castle'] = None
    elif case == 'attack': cur['side'] = 'attack'
    elif case == 'unknown_own': s.egg_rows[0] = EggRow('アルテミス', None, 'ally', 183)
    elif case == 'foreign_run': mem['_run_identity']['generation'] = 2
    elif case == 'foreign_actor': cur['summon_general_reading']['ally'] = 'ヴィーナス'
    elif case == 'foreign_enemy': cur['summon_general_reading']['enemy'] = 'リーキ'
    elif case == 'missing_binding': cur.pop('summon_general_reading')
    elif case == 'missing_panel': s.egg_rows.pop()
    elif case == 'own_monster': s.egg_rows[0] = EggRow('エッグマン', 49, 'ally', 183)
    elif case == 'unknown_monster': s.egg_rows[1] = EggRow('未知', 240, 'enemy', 207)
    assert p.battle_menu_step(s, mem) == [p.pad('b')]


def test_next_use_rechecks_self_damage_instead_of_reusing_first_safe_hp():
    mem = memory(); mem['_run_identity'] = deepcopy(ID)
    cur = mem['battle']; cur.update(ally='どうし', ally_hp=90, start_ally_hp=90, side='attack')
    assert p.battle_menu_step(menu(), mem) == [p.pad('a')]
    assert p.okunote_step(choices(), mem) == [p.pad('a')]
    p.battle_step(Screen([], None, '', kind='battle', battle=Battle('シャルドネ', 56, 'どうし', 88)), mem)
    assert p.battle_menu_step(menu(), mem) == [p.pad('b')]


def test_actual_summon_command_return_allows_second_current_okunote_menu():
    mem = summon_memory()
    assert p.battle_menu_step(parse(summon_frame()), mem) == [p.pad('a')]
    assert p.okunote_step(choices(), mem) == [p.pad('a')]
    mem = json.loads(json.dumps(mem))
    # Native panel/command geometry; this post-choice ordering is synthetic.
    normal = parse(read_png(Path(__file__).parent / 'fixtures/hanjuku/g684-artemis-dark-elf.png'))
    p.egg_battle_step(normal, mem)
    assert p.battle_menu_step(parse(summon_frame()), mem) == [p.pad('a')]
    assert any(r['decision'] == 'battle_okunote_combat_return' for r in mem['_records'])


@pytest.mark.parametrize('case', ['parent_only', 'partial', 'other_actor', 'other_enemy', 'foreign_run'])
def test_unfinished_or_foreign_combat_cannot_restore_another_selection(case):
    mem = memory(); mem['_run_identity'] = deepcopy(ID)
    assert p.battle_menu_step(menu(), mem) == [p.pad('a')]
    if case != 'parent_only': assert p.okunote_step(choices(), mem) == [p.pad('a')]
    cur = mem['battle']
    s = panel(mem)
    if case == 'partial': s.battle = Battle(cur['enemy'], None, cur['ally'], cur['ally_hp'])
    elif case == 'other_actor': s.battle = Battle(cur['enemy'], cur['enemy_hp'], 'ゼウス', 22)
    elif case == 'other_enemy': s.battle = Battle('リーキ', 30, cur['ally'], 22)
    elif case == 'foreign_run': mem['_run_identity']['generation'] = 2
    p.battle_step(s, mem)
    assert p.battle_menu_step(menu(), mem) != [p.pad('a')]
    assert not any(r['decision'] == 'battle_okunote_combat_return' for r in mem['_records'])


def test_resumed_hero_observed_maximum_matches_existing_general_reading_contract():
    from docich.hanjuku_screen import EggRow
    mem = summon_memory(); mem['hero_max_hp'] = 89
    cur = mem['battle']; cur.update(ally='どうし', ally_hp=89, start_ally_hp=89, side='attack')
    cur.pop('summon_general_reading')
    p._remember_general_for_summon(mem, cur, 'どうし', 89)
    assert cur['summon_general_reading']['max_hp'] == 89
    s = parse(summon_frame()); s.egg_rows[0] = EggRow('どうし', 89, 'ally', 183)
    assert p._egg_general_reading(s, mem, cur) == (89, 89)
    assert p.battle_menu_step(s, mem) == [p.pad('a')]

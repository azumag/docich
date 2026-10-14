"""Observed losing defense can take one irreversible chance, never the hero."""
from copy import deepcopy
import pytest
from docich import hanjuku_policy as p
from docich.hanjuku_screen import Screen, Battle
from docich.hanjuku_font import TextLine


def memory():
    return {'chapter': 1, 'battle': {'ally': 'トレビス', 'enemy': 'シャルドネ',
        'castle': 'ゴーメン', 'side': 'defense', 'start_ally_hp': 66,
        'start_enemy_hp': 56, 'ally_hp': 22, 'enemy_hp': 56, 'cards_used': [],
        'card_evidence_version': 1, 'planned_cards': [], 'step': None}}


def menu(hidden=False):
    return Screen([TextLine(208, tuple((176+8*i,ch) for i,ch in enumerate('おくのて')))],
                  None, 'おくのて', kind='battle_menu', menu_cursor=208,
                  hidden_battle_commands=hidden)


def test_g510_trevis_losing_no_resource_defense_selects_last_chance_once():
    mem = memory()
    assert p.okunote_step(menu(), mem) == [p.pad('a')]
    cur = mem['battle']
    assert cur['okunote_last_resort_selected']
    assert any(r['decision'] == 'battle_okunote_last_resort' for r in mem['_records'])
    # A repeat/fading parent cannot enter another irreversible lottery.
    assert p.okunote_step(menu(), mem) == [p.pad('b')]
    assert sum(r['decision'] == 'battle_okunote_last_resort' for r in mem['_records']) == 1


def test_gray_rows_are_scrolled_before_confirming_last_resort():
    mem = memory()
    assert p.okunote_step(menu(True), mem) == [p.pad('down')]
    assert not mem['battle'].get('okunote_last_resort_selected')
    assert p.okunote_step(menu(), mem) == [p.pad('a')]


@pytest.mark.parametrize('changes', [
    {'ally': 'どうし'}, {'ally': 'しゅじんこう'}, {'ally': 'だいじん'},
    {'ally': '不明'}, {'enemy': '不明'}, {'enemy': 'クイーン'},
    {'side': None}, {'side': 'attack'}, {'castle': None}, {'castle': '前章の城'},
    {'ally_hp': 34}, {'ally_hp': 0}, {'ally_hp': None}, {'ally_hp': True},
    {'enemy_hp': 44}, {'enemy_hp': 0}, {'start_enemy_hp': None},
    {'start_ally_hp': 999}, {'enemy_hp': 999},
    {'planned_cards': ['クースカン']}, {'card_flow': {'stage': 'menu'}}, {'egg_battle': True},
])
def test_hero_uncertainty_healthy_fight_and_chart_plan_keep_risk_decline(changes):
    mem = memory(); mem['battle'].update(changes)
    assert p.okunote_step(menu(), mem) == [p.pad('b')]
    assert not any(r['decision'] == 'battle_okunote_last_resort' for r in mem['_records'])


def test_no_current_menu_receipt_never_authorizes_last_chance():
    mem = memory()
    assert not p._defender_last_resort(mem, mem['battle'])
    mem['battle']['okunote_only_observed'] = True
    assert p._defender_last_resort(mem, mem['battle'])


def test_exhausted_healthy_rejection_reopens_once_on_later_losing_panel():
    mem = memory(); cur = mem['battle']; cur['ally_hp'] = 49
    assert p.okunote_step(menu(), mem) == [p.pad('b')]
    cur['ally_hp'] = 22
    panel = Screen([], None, '', kind='battle', battle=Battle('シャルドネ', 56, 'トレビス', 22))
    # Initial battle_start stabilization is not part of this existing fight.
    assert p.battle_step(panel, mem) == [p.pad('b')]
    assert cur['okunote_last_resort_reopened'] and not cur['survival']['exhausted']
    assert any(r['decision'] == 'battle_okunote_recheck' for r in mem['_records'])
    assert p.okunote_step(menu(), mem) == [p.pad('a')]
    p.battle_step(panel, mem)
    assert sum(r['decision'] == 'battle_okunote_recheck' for r in mem['_records']) == 1


@pytest.mark.parametrize("hidden", [False, True])
def test_a_new_live_resource_invalidates_old_gray_menu_evidence(hidden):
    mem = memory(); mem['battle']['okunote_only_observed'] = True
    screen = menu(hidden); screen.lines.append(TextLine(192, tuple((176+8*i,ch) for i,ch in enumerate('きりふだ'))))
    assert p.okunote_step(screen, mem) == [p.pad('b')]
    assert not mem['battle']['okunote_only_observed']

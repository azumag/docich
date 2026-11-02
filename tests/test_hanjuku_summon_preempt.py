"""Owner 2026-11-02: 敵の召喚獣が出たら味方もおくのてで対抗する.

An observed enemy summon with our egg spent and no usable cards opens the
command menu boundedly and selects おくのて. Synthetic state, not evidence of
live inputs.
"""
import pytest

from docich import hanjuku_policy as p
from docich.hanjuku_egg_reference import general_max_hp
from docich.hanjuku_font import TextLine
from docich.hanjuku_screen import Battle, Screen


def memory(*, chapter=1, enemy='リーキ', enemy_hp=30, ally='トレビス', ally_hp=66,
           side='defense', castle=None, egg_empty=False):
    mem = {'chapter': chapter, 'battle': {
        'enemy': enemy, 'ally': ally, 'side': side,
        'castle': castle if castle is not None else p.chart.home_castle(chapter),
        'start_enemy_hp': general_max_hp(enemy), 'enemy_hp': enemy_hp,
        'start_ally_hp': ally_hp, 'ally_hp': ally_hp, 'step': None,
        'cards_used': [], 'cards_selected': [], 'cards_unclassified': [],
        'card_evidence_version': 1, 'planned_cards': [],
        'card_summon_observed': True, 'enemy_summon_observed': True,
        'survival': {'opens': 1, 'menu_ticks': 0, 'cards_checked': 0,
                     'cards_attempted': [], 'egg_attempted': False, 'exhausted': True}}}
    if egg_empty:
        mem['egg_zero_owners'] = [ally]
    return mem


def panel(mem, *, enemy_hp=None, ally_hp=None):
    cur = mem['battle']
    return Screen([], None, '', kind='battle', battle=Battle(
        cur['enemy'], cur['enemy_hp'] if enemy_hp is None else enemy_hp,
        cur['ally'], cur['ally_hp'] if ally_hp is None else ally_hp))


def menu(labels=('おくのて',), cursor=208, *, hidden=False):
    first_y = 208 - 16 * (len(labels) - 1)
    lines = [TextLine(first_y + 16*i, tuple((176+8*j, ch) for j, ch in enumerate(label)))
             for i, label in enumerate(labels)]
    return Screen(lines, None, ''.join(labels), kind='battle_menu', menu_cursor=cursor,
                  hidden_battle_commands=hidden)


def test_egg_exhausted_defense_opens_okunote_against_enemy_summon():
    mem = memory(egg_empty=True)
    assert p.battle_step(panel(mem), mem) == [p.pad('b')]
    guard = mem['battle']['okunote_summon_preempt']
    assert guard['opens'] == 1 and guard['reason'] == 'enemy_summon_present'
    # With the real menu open the preempt records and selects おくのて.
    mem['battle']['okunote_only_observed'] = True
    assert p.battle_menu_step(menu(), mem) == [p.pad('a')]
    assert guard['selected'] and guard['stage'] == 'selected'
    assert any(r['decision'] == 'battle_okunote_summon_preempt' for r in mem['_records'])


def test_menu_observed_okunote_only_also_preempts_without_egg_flag():
    mem = memory()
    mem['battle']['okunote_only_observed'] = True
    assert p._defender_summon_preempt(mem, mem['battle'])['due']


def test_bounded_opens_exhaust_and_return_to_melee():
    mem = memory(egg_empty=True)
    opens = 0
    for _ in range(40):
        if p.battle_step(panel(mem), mem) == [p.pad('b')]:
            opens += 1
        if mem['battle']['okunote_summon_preempt'].get('exhausted'):
            break
    assert opens == p.DEFENDER_SUMMON_OPEN_LIMIT
    assert mem['battle']['okunote_summon_preempt']['exhausted']
    assert any(r['decision'] == 'battle_okunote_summon_open_failed' for r in mem['_records'])
    assert p.battle_step(panel(mem), mem) != [p.pad('b')]


@pytest.mark.parametrize('changes', [
    {'card_summon_observed': False, 'enemy_summon_observed': False},  # no summon seen
    {'egg_battle': True},                     # our own egg is fighting instead
    {'ally': 'だいじん'}, {'ally': None},
    {'enemy': 'クイーン'},                    # boss kit keeps its own rules
    {'side': 'attack'},                       # attack needs observed own-egg-empty
    {'card_flow': {'stage': 'list', 'card': 'グリンボー'}},
])
def test_exclusions_keep_old_policy(changes):
    mem = memory(egg_empty=changes.get('side') != 'attack')
    mem['battle'].update(changes)
    assert p._defender_summon_preempt(mem, mem['battle']) is None


def test_observed_okunote_only_menu_overrides_a_stale_card_plan():
    # The real menu already proved egg/cards unusable; a stale plan must not
    # reopen a card path during the enemy summon.
    mem = memory()
    mem['battle'].update(okunote_only_observed=True, planned_cards=['クースカン'])
    assert p._defender_summon_preempt(mem, mem['battle'])['due']


def test_attack_side_without_observed_empty_egg_does_not_preempt():
    mem = memory(side='attack', castle='けっかい')
    assert p._defender_summon_preempt(mem, mem['battle']) is None


def test_attack_side_preempts_once_own_egg_is_observed_empty():
    mem = memory(side='attack', castle='けっかい', egg_empty=True)
    window = p._defender_summon_preempt(mem, mem['battle'])
    assert window['due'] and window['resources_basis'] == 'own_egg_empty_no_cards'

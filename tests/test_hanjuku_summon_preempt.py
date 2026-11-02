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


# ---- owner 2026-11-02: 城防衛で対召喚獣戦、卵もない時はおくのて ----
# g1097 18:03-18:06 ジュメルズ: ペコリーノ(卵なし)は敵の召喚獣ローラーキラーに
# 卵戦で応戦し、卵が使えなくなった後も もうこうげき で HP0 まで戦った。
def egg_menu_screen(mem, *, egg_row=False):
    from dataclasses import replace
    from test_hanjuku_naruto_damage import native
    s = native(8549)
    s.kind = 'egg_battle_menu'
    s.text = 'こうげきもうこうげき' + ('たまごをつかう' if egg_row else '')
    s.hand = None
    s.egg_rows = [replace(r, name=mem['battle']['ally'], hp=40) if r.side == 'ally'
                  else replace(r, name='ローラーキラー', hp=308) for r in s.egg_rows]
    return s


def egg_fight_memory(**battle):
    mem = memory(chapter=5, enemy='ビシソワーズ', ally='ペコリーノ', ally_hp=40,
                 castle='ジュメルズ')
    mem['battle'].update(egg_battle=True, **battle)
    mem['egg_battle'] = True
    return mem


def test_spent_egg_in_a_castle_defense_summon_fight_opens_the_okunote_menu():
    mem = egg_fight_memory()
    actions = p.egg_battle_step(egg_menu_screen(mem), mem)
    cur = mem['battle']
    assert actions == [p.pad('b')]
    assert cur['summon_egg_unavailable'] is True
    assert cur['okunote_summon_preempt']['opens'] == 1
    assert any(r['decision'] == 'battle_okunote_summon_open' for r in mem['_records'])
    assert p._defender_summon_preempt(mem, cur)['resources_basis'] == 'summon_menu_egg_unavailable'


def test_the_open_is_bounded_and_then_the_old_attack_resumes():
    mem = egg_fight_memory()
    opens = 0
    for _ in range(10):
        actions = p.egg_battle_step(egg_menu_screen(mem), mem)
        if actions == [p.pad('b')] and mem['battle']['okunote_summon_preempt']['opens'] > opens:
            opens = mem['battle']['okunote_summon_preempt']['opens']
        elif mem['battle']['okunote_summon_preempt'].get('exhausted'):
            break
    assert opens == p.DEFENDER_SUMMON_OPEN_LIMIT
    # Exhausted: no further B is planned for the okunote from this panel.
    assert mem['battle']['okunote_summon_preempt']['exhausted']
    assert any(r['decision'] == 'battle_okunote_summon_open_failed' for r in mem['_records'])


def test_a_usable_egg_row_keeps_the_egg_fight_and_never_opens_the_okunote():
    mem = egg_fight_memory()
    p.egg_battle_step(egg_menu_screen(mem, egg_row=True), mem)
    assert not mem['battle'].get('summon_egg_unavailable')
    assert 'okunote_summon_preempt' not in mem['battle']


@pytest.mark.parametrize('changes', [
    {'side': 'attack'},                  # only castle defense
    {'enemy': 'クイーン'},               # boss kit keeps its own rules
    {'ally': 'だいじん'},
])
def test_other_fights_keep_the_old_egg_battle_policy(changes):
    mem = egg_fight_memory(**changes)
    p.egg_battle_step(egg_menu_screen(mem), mem)
    assert not mem['battle'].get('okunote_summon_preempt')


def test_a_planned_card_still_to_play_comes_before_the_okunote():
    mem = egg_fight_memory(planned_cards=['クースカン'])
    p.egg_battle_step(egg_menu_screen(mem), mem)
    assert not mem['battle'].get('okunote_summon_preempt')

"""An enemy one push from defeat needs no okunote, egg or card.

Owner 2026-11-02: 相手のHPが1で一回ぶつかれば勝てる場合、おくのても卵も切り札もいらない。
Synthetic panels and persisted state, not evidence of live input delivery.
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from docich import hanjuku_policy as p
from test_hanjuku_survival import battle, memory, menu

A_BURST = p._power_mash({}, {'power_mash': True})


def danger(enemy_hp):
    # Critical own HP (the survival path would open the menu) with a threatening enemy.
    return memory(hp=5, enemy=enemy_hp)


def test_battle_step_melees_instead_of_opening_the_menu_at_one_hp():
    mem = danger(1)
    actions = p.battle_step(battle(mem), mem)
    assert actions == A_BURST and p.pad('b') not in actions
    assert any(r['decision'] == 'battle_one_hit_finish' for r in mem['_records'])
    assert not mem['battle'].get('survival')


def test_one_hit_record_is_written_once_per_battle():
    mem = danger(1)
    for _ in range(3):
        p.battle_step(battle(mem), mem)
    assert sum(r['decision'] == 'battle_one_hit_finish' for r in mem['_records']) == 1


def test_just_above_the_bound_still_uses_the_existing_rescue():
    mem = danger(p.EGG_WALL_DAMAGE + 1)
    assert p.battle_step(battle(mem), mem) == [p.pad('b')]
    assert not any(r['decision'] == 'battle_one_hit_finish' for r in mem['_records'])


def test_bound_itself_is_one_hit_lethal():
    mem = danger(p.EGG_WALL_DAMAGE)
    assert p.battle_step(battle(mem), mem) == A_BURST


def test_open_menu_is_closed_without_choosing_egg_card_or_okunote():
    mem = danger(1)
    mem['battle']['survival'] = {'opens': 1, 'menu_ticks': 0, 'cards_checked': 0,
                                 'cards_attempted': [], 'egg_attempted': False, 'exhausted': False}
    p.battle_step(battle(mem), mem)  # current panel
    assert p.battle_menu_step(menu(), mem) == [p.pad('b')]
    mem['battle']['hidden'] = True
    assert p.battle_menu_step(menu(('おくのて',)), mem) == [p.pad('b')]
    assert not mem['battle'].get('egg_attempted')
    assert not mem['battle'].get('cards_selected')


def test_planned_card_is_dropped_not_spent_when_enemy_is_one_hit_from_death():
    mem = memory(hp=60, enemy=1)
    mem['battle']['planned_cards'] = ['ゼンマイン']
    mem['battle']['card_flow'] = {'card': 'ゼンマイン', 'stage': 'menu', 'tactic_id': 't'}
    assert p.battle_step(battle(mem), mem) == A_BURST
    assert mem['battle']['card_flow'] is None
    assert not mem['battle'].get('cards_selected')


def test_card_list_closes_unselected():
    mem = memory(hp=60, enemy=1)
    mem['battle']['card_flow'] = {'card': 'ゼンマイン', 'stage': 'list', 'tactic_id': 't'}
    p.battle_step(battle(mem), mem)
    mem['battle']['card_flow'] = {'card': 'ゼンマイン', 'stage': 'list', 'tactic_id': 't'}
    assert p.card_list_step(menu(('ゼンマイン',), kind='text'), mem) == [p.pad('b')]
    assert not mem['battle'].get('cards_selected')


@pytest.mark.parametrize('flag', ['card_hp_unread', 'card_context_unclassified',
                                  'egg_battle', 'card_summon_observed'])
def test_unread_or_summon_context_is_not_trusted(flag):
    cur = {'enemy_hp': 1, 'ally_hp': 30, flag: True}
    assert not p._one_hit_lethal(cur)


@pytest.mark.parametrize('enemy_hp,ally_hp', [(0, 30), (None, 30), (1, 0), (1, None)])
def test_missing_or_finished_hp_is_not_one_hit_lethal(enemy_hp, ally_hp):
    assert not p._one_hit_lethal({'enemy_hp': enemy_hp, 'ally_hp': ally_hp})

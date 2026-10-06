"""Current critical HP does not depend on a readable historical starting HP."""
import pytest

from docich import hanjuku_policy as p
from test_hanjuku_egg_timing import memory
from test_hanjuku_survival import menu


@pytest.mark.parametrize('start', [None, 0, -1, False, '90', 'missing'])
def test_critical_defense_can_use_a_current_egg_even_with_unknown_start(start):
    mem = memory(3, 2)
    cur = mem['battle']
    if start == 'missing':
        cur.pop('start_ally_hp')
    else:
        cur['start_ally_hp'] = start
    assert p._survival_needed(cur) is True
    assert p.battle_menu_step(menu(), mem) == [p.pad('a')]
    assert cur['survival']['egg_attempted'] is True


@pytest.mark.parametrize('start', [None, 'missing'])
def test_attack_retreat_still_precedes_an_egg_with_unknown_start(start):
    mem = memory(3, 2)
    cur = mem['battle']
    cur['side'] = 'attack'
    if start == 'missing':
        cur.pop('start_ally_hp')
    else:
        cur['start_ally_hp'] = start
    assert p._hero_retreat_open(mem, cur) == [p.pad('b')]
    assert p.battle_menu_step(menu(selected=2), mem) == [p.pad('a')]
    assert cur['hero_retreat']['selected'] == 1 and 'egg_recheck' not in mem


def test_known_zero_egg_is_unavailable_even_when_start_hp_is_unknown():
    mem = memory(3, 2, uses=0)
    mem['battle'].pop('start_ally_hp')
    assert p.battle_menu_step(menu(), mem) == [p.pad('down')]
    assert not mem['battle']['survival']['egg_attempted']


def test_unknown_current_hp_is_not_replaced_by_the_critical_threshold():
    mem = memory(3, 2)
    mem['battle'].update(start_ally_hp=None, ally_hp=None)
    assert p._survival_needed(mem['battle']) is False
    assert p._hero_retreat_needed(mem['battle']) is False


def test_known_sword_practice_stays_outside_resource_rescue():
    mem = memory(3, 2)
    mem['battle'].update(enemy='だいじん', start_enemy_hp=90, start_ally_hp=90, step=None)
    assert p._survival_needed(mem['battle']) is False
    assert p._hero_retreat_needed(mem['battle']) is False

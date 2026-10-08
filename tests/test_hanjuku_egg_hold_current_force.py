"""Current-army egg decisions; synthetic panels, not a live win-rate claim."""
from copy import deepcopy

import pytest

from docich import hanjuku_policy as p
from test_hanjuku_egg_hold import egg_memory, egg_need, egg_screen
from test_hanjuku_card_damage_gate import panel


def force_memory(ours=6, theirs=1, *, enemy_hp=90):
    # Existing fixture goes through battle_step and the real card panel reader.
    return egg_memory(60, enemy_hp, (ours, theirs))


def assert_hp_only(mem, *, needed=True, enemy_hp=90):
    result, evidence = egg_need(mem, 60)
    assert result is needed
    assert evidence['rule'] == 'hp_only'
    assert evidence['ally_soldiers'] is evidence['enemy_soldiers'] is None
    assert (evidence['ally_force'], evidence['enemy_force']) == (60, enemy_hp)


@pytest.mark.parametrize('marker', [None, False, 0, 1, 'true', [], {}])
def test_unproven_current_marker_cannot_borrow_six_old_soldiers(marker):
    mem = force_memory()
    if marker is None:
        mem['battle'].pop('card_soldiers_current')
    else:
        mem['battle']['card_soldiers_current'] = marker
    assert_hp_only(mem)
    # Discarding their evidential value must not invent a new observation.
    assert (mem['battle']['ally_soldiers'], mem['battle']['enemy_soldiers']) == (6, 1)


@pytest.mark.parametrize('flag', ['card_hp_unread', 'card_context_unclassified'])
def test_invalidated_panel_cannot_supply_force_even_with_true_marker(flag):
    mem = force_memory()
    mem['battle'][flag] = True
    assert_hp_only(mem)


@pytest.mark.parametrize('field', ['ally_soldiers', 'enemy_soldiers'])
@pytest.mark.parametrize('value', [None, True, False, -1, 7, 999, '6', 6.0, [], {}])
def test_one_invalid_army_disables_both_soldier_additions(field, value):
    mem = force_memory()
    mem['battle'][field] = value
    assert_hp_only(mem)


@pytest.mark.parametrize('ours', range(7))
@pytest.mark.parametrize('theirs', range(7))
def test_all_49_current_armies_keep_the_existing_seven_tenths_policy(ours, theirs):
    mem = force_memory(ours, theirs)
    assert mem['battle']['card_soldiers_current'] is True
    needed, evidence = egg_need(mem, 60)
    our_force, their_force = 60 + 10 * ours, 90 + 10 * theirs
    assert needed is (our_force * 10 <= their_force * 7)
    assert evidence['rule'] == 'soldier_force'
    assert evidence['ally_soldiers'] == ours and evidence['enemy_soldiers'] == theirs
    assert (evidence['ally_force'], evidence['enemy_force']) == (our_force, their_force)


def test_stale_enemy_army_also_cannot_force_an_unnecessary_egg():
    mem = force_memory(0, 6, enemy_hp=60)
    assert egg_need(mem, 60)[0] is True
    mem['battle']['card_soldiers_current'] = False
    assert_hp_only(mem, needed=False, enemy_hp=60)
    assert p.egg_battle_step(egg_screen(60), mem) == [p.pad('a')]
    assert mem['egg_action'] == 'attack'


@pytest.mark.parametrize('invalid', ['enemy_hp', 'ally_name', 'enemy_name', 'missing_panel'])
def test_real_reader_invalidates_then_restores_the_same_battles_force(invalid):
    mem = force_memory()
    cur = mem['battle']
    assert egg_need(mem, 60)[0] is False
    screen = panel(mem, soldiers=(6, 1))
    if invalid == 'enemy_hp':
        screen.battle.enemy_hp = None
    elif invalid == 'ally_name':
        screen.battle.ally = '別の将軍'
    elif invalid == 'enemy_name':
        screen.battle.enemy = '別の敵'
    else:
        screen.battle = None
    p._card_battle_reading(screen, cur)
    assert cur['card_soldiers_current'] is False
    assert (cur['ally_soldiers'], cur['enemy_soldiers']) == (6, 1)
    assert_hp_only(mem)
    p._card_battle_reading(panel(mem, soldiers=(6, 1)), cur)
    needed, evidence = egg_need(mem, 60)
    assert needed is False and evidence['rule'] == 'soldier_force'
    assert cur['cards_used'] == []


def test_invalidated_force_escalates_a_saved_melee_choice_without_oscillation():
    mem = force_memory()
    assert p.egg_battle_step(egg_screen(60), mem) == [p.pad('a')]
    assert mem['egg_action'] == 'attack' and mem['egg_needed'] is False
    mem['battle']['card_soldiers_current'] = False
    assert p.egg_battle_step(egg_screen(60), mem) == [p.pad('down')]
    assert mem['egg_action'] == 'use_egg' and mem['egg_needed'] is True
    record = next(r for r in reversed(mem['_records'])
                  if r.get('strategy_variant') == 'egg_battle_use_egg')
    assert record['observed_metric']['egg_need']['rule'] == 'hp_only'
    # A subsequent valid count can justify conservation again, but the already
    # escalated menu episode must not switch back and forth between commands.
    mem['battle']['card_soldiers_current'] = True
    p.egg_battle_step(egg_screen(60), mem)
    assert mem['egg_action'] == 'use_egg'


def test_uncertain_force_never_unlocks_a_known_empty_egg():
    mem = force_memory()
    ally = mem['battle']['ally']
    mem.setdefault('egg_uses', {})[ally] = 0
    mem['battle']['card_soldiers_current'] = False
    assert_hp_only(mem)
    p.egg_battle_step(egg_screen(60), mem)
    assert mem['egg_action'] == 'attack'
    assert mem['egg_uses'][ally] == 0
    assert not any(r.get('strategy_variant') == 'egg_battle_use_egg'
                   for r in mem.get('_records', []))


@pytest.mark.parametrize('current', [True, False])
def test_wounded_generals_and_bosses_keep_the_existing_egg_priority(current):
    mem = force_memory()
    mem['battle']['card_soldiers_current'] = current
    assert egg_need(mem, 30)[0] is True
    mem = force_memory()
    mem['battle'].update(enemy='クイーン', enemy_hp=1, card_soldiers_current=current)
    needed, evidence = egg_need(mem, 60)
    assert needed is True and evidence['strong_general'] is True


def test_force_decision_does_not_rewrite_memory_or_consume_resources():
    mem = force_memory()
    mem['battle']['card_soldiers_current'] = False
    before = deepcopy(mem)
    needed, evidence = p._own_egg_needed(mem, mem['battle'], (60, 82))
    assert needed is True and evidence['rule'] == 'hp_only'
    assert mem == before

"""Synthetic HP3 emergencies and measured advantage; no production-frame claim."""
from copy import deepcopy

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku_screen import Battle, Screen
from test_hanjuku_survival import menu


def memory(hp=90, enemy_hp=20, *, soldiers=(6, 0), uses=2):
    cur = {'ally': p.NAME, 'enemy': 'バジル', 'ally_hp': hp, 'enemy_hp': enemy_hp,
           'start_ally_hp': 90, 'ref_ally_hp': 90, 'start_enemy_hp': enemy_hp,
           'ally_soldiers': soldiers[0], 'enemy_soldiers': soldiers[1],
           'card_soldiers_current': True, 'side': 'defense', 'step': None,
           'cards_used': [], 'cards_selected': [], 'cards_unclassified': [],
           'planned_cards': [], 'card_evidence_version': 1}
    return {'chapter': 1, 'egg_uses': {p.NAME: uses}, 'battle': cur}


def panel(mem):
    cur = mem['battle']
    return Screen([], None, '', kind='battle', battle=Battle(
        cur['enemy'], cur['enemy_hp'], cur['ally'], cur['ally_hp']),
        field_soldiers=(cur['ally_soldiers'], cur['enemy_soldiers']))


@pytest.mark.parametrize('saved', [False, True])
def test_measured_advantage_and_a_real_card_row_overrule_learned_egg(monkeypatch, saved):
    mem = memory()
    monkeypatch.setattr(p.experience, 'preferred', lambda *a, **kw: 'use_egg')
    if saved:
        mem.update(indep_menu=True, indep_menu_action='use_egg')
    assert p.battle_menu_step(menu(), mem) == [p.pad('b')]
    assert 'egg_recheck' not in mem


def test_a_stale_cards_exhausted_flag_cannot_spend_an_egg_when_healthy_and_ahead():
    mem = memory()
    p._survival_state(mem, mem['battle']).update(cards_exhausted=True, cards_checked=3)
    assert p.battle_menu_step(menu(), mem) == [p.pad('b')]
    assert mem['battle']['survival']['egg_attempted'] is False


@pytest.mark.parametrize('soldiers', [(None, None), (6, None), (None, 0), (True, 0), (-1, 0)])
def test_unknown_or_invalid_soldiers_are_not_a_measured_advantage(soldiers):
    mem = memory(soldiers=soldiers)
    assert p._hold_melee_egg(mem, mem['battle'], {'きりふだ'}) is False


@pytest.mark.parametrize('change', ['summon', 'wounded', 'no_card', 'stale_counts'])
def test_preservation_requires_all_current_safe_conditions(change):
    mem = memory()
    labels = {'きりふだ'}
    if change == 'summon':
        mem['battle']['egg_battle'] = True
    elif change == 'wounded':
        mem['battle']['ally_hp'] = 30
    elif change == 'no_card':
        labels.clear()
    else:
        mem['battle']['card_soldiers_current'] = False
    assert p._hold_melee_egg(mem, mem['battle'], labels) is False


@pytest.mark.parametrize('stale', ['none', 'exhausted', 'menu_limit'])
def test_hp3_defense_uses_a_real_egg_without_an_unmeasured_card_scout(stale):
    mem = memory(3, 2)
    rescue = p._survival_state(mem, mem['battle'])
    if stale == 'exhausted':
        rescue['exhausted'] = True
    if stale == 'menu_limit':
        rescue['menu_ticks'] = 12
    assert p.battle_menu_step(menu(), mem) == [p.pad('a')]
    assert rescue['egg_attempted'] is True and mem['egg_uses'][p.NAME] == 2
    for _ in range(20):
        p.battle_menu_step(menu(), mem)
    assert rescue['egg_attempted'] is True
    assert rescue['exhausted'] is True  # no reset loop or second egg selection


@pytest.mark.parametrize('card', ['エンジェリン', 'ゼンマイン'])
def test_a_previously_observed_safe_untried_card_keeps_priority_at_hp3(card):
    mem = memory(3, 2)
    rescue = p._survival_state(mem, mem['battle'])
    rescue['listed_cards'] = [card]
    assert p.battle_menu_step(menu(selected=1), mem) == [p.pad('a')]
    assert mem['battle']['card_flow']['survival'] is True
    assert rescue['egg_attempted'] is False
    assert p.card_list_step(menu((card,), kind='text'), mem) == [p.pad('a')]
    assert mem['battle']['cards_selected'] == [card] and mem['battle']['cards_used'] == []


def test_already_selected_unconfirmed_card_does_not_block_emergency_egg():
    mem = memory(3, 2)
    rescue = p._survival_state(mem, mem['battle'])
    rescue['listed_cards'] = ['エンジェリン']
    rescue['cards_attempted'] = ['エンジェリン']
    mem['battle']['cards_selected'] = ['エンジェリン']
    assert p.battle_menu_step(menu(), mem) == [p.pad('a')]
    assert rescue['egg_attempted'] is True


@pytest.mark.parametrize('uses', [0, 2])
def test_critical_menu_never_blindly_confirms_unknown_cursor_or_zero_egg(uses):
    mem = memory(3, 2, uses=uses)
    assert p.battle_menu_step(menu(selected=None), mem) != [p.pad('a')]
    assert not mem['battle']['survival']['egg_attempted']
    if uses == 0:
        p.battle_menu_step(menu(), mem)
        assert not mem['battle']['survival']['egg_attempted']
        assert 'egg_recheck' not in mem


def test_hp3_defense_preempts_only_unconfirmed_announce_without_claiming_use():
    mem = memory(3, 2)
    cur = mem['battle']
    cur['cards_selected'] = ['エンジェリン']
    cur['card_consumption_complete'] = False
    cur['card_flow'] = {'card': 'エンジェリン', 'stage': 'announce', 'survival': True,
                        'selection_planned': True}
    assert p.battle_step(panel(mem), mem) == [p.pad('b')]
    assert cur['cards_used'] == [] and cur['cards_unclassified'] == ['エンジェリン']
    assert cur['card_flow'] is None


def test_a_previous_exhaustion_opens_one_final_emergency_menu_only():
    mem = memory(3, 2)
    rescue = p._survival_state(mem, mem['battle'])
    rescue.update(exhausted=True, opens=12, pending_opens=3)
    assert p.battle_step(panel(mem), mem) == [p.pad('b')]
    rescue['exhausted'] = True
    before = deepcopy(rescue)
    p.battle_step(panel(mem), mem)
    assert rescue['opens'] == before['opens']


def test_critical_attack_retreat_stays_before_egg_use():
    mem = memory(3, 2)
    mem['battle']['side'] = 'attack'
    assert p.battle_menu_step(menu(selected=2), mem) == [p.pad('a')]
    assert mem['battle']['hero_retreat']['selected'] == 1
    assert 'egg_recheck' not in mem

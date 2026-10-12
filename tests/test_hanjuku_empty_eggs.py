"""An observed zero is unavailable; an unread quantity stays unknown."""
from copy import deepcopy

import pytest

from docich import hanjuku_policy as policy
from docich.hanjuku_font import TextLine
from docich.hanjuku_screen import Screen
from test_hanjuku_chart_bot import sortie_canvas


def memory(uses=0):
    return {'chapter': 1, 'egg_uses': {'ココット': uses},
            'battle': {'ally': 'ココット', 'enemy': 'ミント', 'ally_hp': 20,
                       'enemy_hp': 40, 'side': 'defense', 'cards_used': []}}


def menu(kind='battle_menu', cursor=176, cards=True):
    labels = ([(176, 'たまごをつかう'), (192, 'きりふだ')] if cards else
              [(176, 'たまごをつかう')]) if kind == 'battle_menu' else [
                  (176, 'こうげき'), (192, 'もうこうげき'), (208, 'たまごをつかう')]
    rows = [TextLine(y, ((176, label),)) for y, label in labels]
    return Screen(rows, None, ''.join(word for _, word in labels), kind=kind,
                  menu_rows=rows, menu_cursor=cursor)


@pytest.mark.parametrize('uses', [None, False, True, '0', -1, 1, 4])
def test_only_a_real_integer_zero_is_empty(uses):
    mem = memory(uses)
    assert policy._own_egg_empty(mem, mem['battle']) is False
    mem['egg_uses'] = {'ゼウス': 0}
    assert policy._own_egg_empty(mem, mem['battle']) is False
    assert policy._own_egg_empty(mem, {}) is False


def test_sortie_zero_is_used_by_the_battle_gate_and_a_new_count_reopens_it():
    mem = memory(4)
    policy.observe_events(sortie_canvas('ココット', 0), mem)
    assert policy._own_egg_empty(mem, mem['battle']) is True
    policy.observe_events(sortie_canvas('ココット', 1), mem)
    assert policy._own_egg_empty(mem, mem['battle']) is False


@pytest.mark.parametrize('learned', [False, True])
def test_independent_melee_does_not_choose_an_empty_egg(monkeypatch, learned):
    mem = memory()
    if learned:
        monkeypatch.setattr(policy.experience, 'preferred', lambda *a, **kw: 'use_egg')
    assert policy.battle_menu_step(menu(), mem) == [policy.pad('b')]
    assert mem['indep_menu_action'] == 'pass'
    assert 'egg_recheck' not in mem


def test_saved_use_intention_cannot_bypass_a_newly_observed_zero():
    mem = memory()
    mem.update(indep_menu=True, indep_menu_action='use_egg')
    assert policy.battle_menu_step(menu(), mem) == [policy.pad('b')]
    assert 'egg_recheck' not in mem


def test_survival_keeps_cards_first_but_never_attempts_the_empty_egg():
    mem = memory()
    assert policy._survival_menu(menu(), mem, mem['battle']) == [policy.pad('down')]
    assert mem['battle']['survival']['egg_attempted'] is False
    mem['battle']['card_flow'] = None
    mem['battle']['survival']['cards_checked'] = 3
    assert policy._survival_menu(menu(), mem, mem['battle']) == [policy.pad('b')]
    assert mem['battle']['survival']['exhausted'] is True
    assert mem['battle']['survival']['egg_attempted'] is False
    assert 'egg_recheck' not in mem


@pytest.mark.parametrize('uses', [None, 1, 4])
def test_unknown_or_positive_egg_still_can_answer_a_survival_need(uses):
    mem = memory(uses)
    assert policy._survival_menu(menu(cards=False), mem, mem['battle']) == [policy.pad('a')]
    assert mem['battle']['survival']['egg_attempted'] is True
    assert mem['egg_uses']['ココット'] == uses  # selection is not a consumption receipt


@pytest.mark.parametrize('saved', [False, True])
def test_summon_answer_rejects_zero_even_with_a_saved_egg_intention(monkeypatch, saved):
    mem = memory()
    monkeypatch.setattr(policy.experience, 'preferred', lambda *a, **kw: 'use_egg')
    if saved:
        mem.update(egg_battle=True, egg_action='use_egg', egg_needed=False)
    screen = menu('egg_battle_menu', cursor=208)
    assert policy.egg_battle_step(screen, mem) == [policy.pad('up')]
    assert mem['egg_action'] == 'attack'
    assert mem['egg_needed'] is True  # unavailable does not mean healthy
    assert 'egg_recheck' not in mem
    screen.menu_cursor = 176
    assert policy.egg_battle_step(screen, mem) == [policy.pad('a')]


def test_no_blind_confirmation_on_an_unread_cursor_when_the_egg_is_zero():
    mem = memory()
    assert policy.egg_battle_step(menu('egg_battle_menu', cursor=None), mem) == [policy.pad('b')]
    assert 'egg_recheck' not in mem


def test_empty_egg_keeps_the_existing_attack_side_retreat_route():
    mem = memory()
    mem['battle']['side'] = 'attack'
    assert policy.egg_battle_step(menu('egg_battle_menu'), mem) == [policy.pad('b')]
    assert mem['battle']['egg_retreat_needed'] is True


def test_already_offered_summon_candidates_do_not_confirm_an_empty_egg():
    mem = memory()
    assert policy.egg_choice_step(menu('egg_choice_menu'), mem) == [policy.pad('b')]
    assert 'egg_choice' not in mem


def test_egg_recheck_never_guesses_that_the_last_use_was_consumed():
    mem = memory(1)
    before = deepcopy(mem['egg_uses'])
    policy._egg_recheck(mem)
    assert mem['egg_uses'] == before
    assert mem['egg_recheck'] == ['ココット']

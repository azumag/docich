"""A verified camp status is a quantity observation, never a use/recovery receipt."""
from copy import deepcopy

import pytest

from docich import hanjuku_house as house, hanjuku_policy as p
from docich.hanjuku_font import TextLine, UNKNOWN
from docich.hanjuku_screen import Screen, parse
from test_hanjuku_retreat_recheck import memory, focus, MAP, FRAME
from test_hanjuku_chart_bot import camp_menu
from test_hanjuku_house import status
from test_hanjuku_empty_eggs import menu as egg_menu
from test_hanjuku_egg_timing import memory as battle_memory


def open_status(mem, mode):
    if mode == 'named':
        focus(mem)
    else:
        mem['retreat_rechecks']['ゼウス'].update(attempts=3)
        assert p.camp_recall_step(MAP, mem, FRAME) == [p.pad('a')]
        assert p.camp_recall_step(camp_menu(1), mem, FRAME) == [p.pad('a')]


def critical_contact(mem, actor):
    mem['battle'] = battle_memory(3, 35)['battle']
    mem['battle']['ally'] = actor
    assert p.camp_recall_step(Screen([], None, '', kind='battle'), mem, FRAME) is None
    assert p.battle_menu_step(egg_menu(cards=False), mem) == [p.pad('b')]
    assert p._own_egg_empty(mem, mem['battle'])
    assert not mem['battle']['survival']['egg_attempted']
    assert actor not in (mem.get('egg_recheck') or [])


@pytest.mark.parametrize('mode,actor', [('named', 'ゼウス'), ('anonymous', 'ヴィーナス'), ('anonymous_blocked', 'ゼウス')])
def test_verified_zero_replaces_cached_two_before_a_critical_enemy_contact(mode, actor):
    mem = memory(); mem['egg_uses'] = {'ゼウス': 2, 'ヴィーナス': 2}
    mem['egg_types'] = {'ゼウス': 'エッグ', 'ヴィーナス': 'エッグ'}
    mem['egg_recheck'] = [actor]
    before = deepcopy(mem['sorties'])
    open_status(mem, mode)
    screen = parse(status(actor, 'エラベルエッグ0', main=False, hp=6))
    assert p.camp_recall_step(screen, mem, FRAME) == [p.pad('b')]
    assert mem['egg_uses'][actor] == 0
    assert mem['egg_types'][actor] == 'エラベルエッグ'
    assert mem['sorties'] == before
    assert not mem.get('garrison') and not mem.get('house_eggs')
    critical_contact(mem, actor)


@pytest.mark.parametrize('uses', [0, 1, 2, 3, 4, 5])
def test_actual_positive_and_zero_quantities_are_observed_without_guessing_consumption(uses):
    mem = memory(); mem['egg_uses'] = {'ゼウス': 2}
    focus(mem)
    assert p.camp_recall_step(parse(status('ゼウス', f'エラベルエッグ{uses}', main=False, hp=6)), mem, FRAME) == [p.pad('b')]
    assert mem['egg_uses']['ゼウス'] == uses
    assert mem['egg_types']['ゼウス'] == 'エラベルエッグ'
    assert mem['retreat_rechecks']['ゼウス']['status'] == 'needs_observation'
    assert not mem.get('garrison') and not mem.get('house_eggs')


def test_another_named_actor_does_not_update_the_intended_actors_quantity():
    mem = memory(); mem['egg_uses'] = {'ゼウス': 2, 'ヴィーナス': 4}
    before = deepcopy(mem['egg_uses']); focus(mem)
    assert p.camp_recall_step(parse(status('ヴィーナス', 'エラベルエッグ0', main=False, hp=6)), mem, FRAME) == [p.pad('b')]
    assert mem['egg_uses'] == before and not mem.get('egg_types')


@pytest.mark.parametrize('egg', ['こわれている', 'なし'])
def test_unknown_count_broken_and_eggless_never_restore_a_default_zero(egg):
    mem = memory(); mem['egg_uses'] = {'ゼウス': 2}; focus(mem)
    p.camp_recall_step(parse(status('ゼウス', egg, main=False, hp=6)), mem, FRAME)
    assert mem['egg_uses'] == {'ゼウス': 2} and not mem.get('egg_types')


def test_an_unknown_egg_glyph_does_not_update_the_quantity():
    mem = memory(); mem['egg_uses'] = {'ゼウス': 2}; focus(mem)
    screen = parse(status('ゼウス', 'エラベルエッグ0', main=False, hp=6))
    egg = next(r for r in screen.lines if r.y == 111)
    screen.lines[screen.lines.index(egg)] = TextLine(111, tuple((x, UNKNOWN if x == 48 else ch) for x, ch in egg.cells))
    assert house.general_status(screen) is None
    for _ in range(3): p.camp_recall_step(screen, mem, FRAME)
    assert mem['egg_uses'] == {'ゼウス': 2} and not mem.get('egg_types')


def test_main_status_and_a_missing_current_tent_do_not_update_quantities():
    mem = memory(); mem['egg_uses'] = {'ゼウス': 2}; focus(mem)
    p.camp_recall_step(parse(status('ゼウス', 'エラベルエッグ0', main=True, hp=6)), mem, FRAME)
    assert mem['egg_uses'] == {'ゼウス': 2}
    mem = memory(); mem['egg_uses'] = {'ゼウス': 2}
    mem['recall'] = {'stage': 'menu', 'steps': 0, 'target': [26, 66]}
    p.camp_recall_step(camp_menu(1), mem, FRAME)
    p.camp_recall_step(parse(status('ゼウス', 'エラベルエッグ0', main=False, hp=6)), mem, FRAME)
    assert mem['egg_uses'] == {'ゼウス': 2}


@pytest.mark.parametrize('uses', [None, True, False, '0', -1, 6])
def test_only_strict_observed_zero_to_five_updates_the_cache(monkeypatch, uses):
    mem = memory(); mem['egg_uses'] = {'ゼウス': 2}; focus(mem)
    screen = parse(status('ゼウス', 'エラベルエッグ0', main=False, hp=6))
    info = house.general_status(screen); info['uses'] = uses
    monkeypatch.setattr(house, 'general_status', lambda _screen: info)
    p.camp_recall_step(screen, mem, FRAME)
    assert mem['egg_uses'] == {'ゼウス': 2} and not mem.get('egg_types')


@pytest.mark.parametrize('hp', [None, 0])
def test_actual_zero_is_kept_even_when_hp_prevents_return(hp):
    mem = memory(); mem['egg_uses'] = {'ゼウス': 2}; focus(mem)
    screen = parse(status('ゼウス', 'エラベルエッグ0', main=False, hp=0 if hp == 0 else 6))
    if hp is None: screen.lines = [line for line in screen.lines if line.y != 47]
    assert p.camp_recall_step(screen, mem, FRAME) == [p.pad('b')]
    assert not mem.get('recall') and mem['egg_uses']['ゼウス'] == 0
    if hp == 0:
        assert 'ゼウス' not in (mem.get('retreat_rechecks') or {})
    else:
        assert mem['retreat_rechecks']['ゼウス']['status'] == 'needs_observation'
    assert not mem.get('garrison') and not mem.get('house_eggs')
    critical_contact(mem, 'ゼウス')


def test_quantity_observation_dirties_only_the_old_roster_egg_fact():
    from test_hanjuku_survey_cache import completed
    from docich import hanjuku_camp_recheck as recheck
    mem = completed(); mem['egg_uses']['ゼウス'] = 2
    recheck.capture(mem, {'ally': 'ゼウス', 'side': 'attack', 'ally_hp': 6, 'hero_retreat': {'selected': 1}})
    before_rows = deepcopy(mem['roster_survey']['statuses'])
    before_locations = deepcopy(mem['garrison'])
    focus(mem)
    assert p.camp_recall_step(parse(status('ゼウス', 'エラベルエッグ0', main=False, hp=6)), mem, FRAME) == [p.pad('b')]
    assert mem['egg_uses']['ゼウス'] == 0
    assert mem['roster_survey']['dirty'] == {'ゼウス': ['egg']}
    assert mem['roster_survey']['statuses'] == before_rows
    assert mem['garrison'] == before_locations

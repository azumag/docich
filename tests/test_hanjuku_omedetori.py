"""Omedetori: one ミカンをおす, then the self-destruct あたためる (#2945)."""
from types import SimpleNamespace

import pytest

from docich.hanjuku.flows import battle_menus as menus
from docich.hanjuku.judgment.omedetori import (
    omedetori_assessment, omedetori_command_selected,
)


def panel(name='おめでとり', hp=40):
    return SimpleNamespace(name=name, hp=hp)


def mem(enemy='ハデス'):
    return {'battle': {'ally': 'ヴィーナス', 'enemy': enemy}}


def view(*, cursor=160, enemy='ハデス', skills=None):
    skills = ['ミカンをおす', 'あたためる'] if skills is None else skills
    ally = SimpleNamespace(name='おめでとり', hp=40, side='ally')
    other = SimpleNamespace(name=enemy, hp=500, side='enemy')
    return SimpleNamespace(
        menu_rows=[SimpleNamespace(known=s, y=160 + 16 * i)
                   for i, s in enumerate([*skills, 'たまごに もどれ'])],
        egg_rows=[ally, other], menu_cursor=cursor,
    )


@pytest.fixture
def events(monkeypatch):
    recorded = []
    monkeypatch.setattr(menus, '_record', lambda state, kind, **kw: recorded.append((kind, kw)))
    monkeypatch.setattr(menus, '_summoner_return_risk', lambda *args: {})
    monkeypatch.setattr(menus.experience, 'situation_key', lambda *args: 'omedetori-test')
    # A learned first skill must never override the one-shot ordering.
    monkeypatch.setattr(menus.experience, 'preferred', lambda *args, default, **kw: 'skill1')
    return recorded


def test_mikan_once_then_self_destruct_warming(events):
    state = mem()
    assert menus.monster_menu_step(view(cursor=160), state) == [menus.pad('a')]
    assert state['battle']['omedetori_cycle']['mikan_submitted'] is True
    assert state['monster_menu_choice'] == 'skill1'
    # The next decision moves to あたためる without any other command in between.
    assert menus.monster_menu_step(view(cursor=160), state) == [menus.pad('down')]
    assert state['monster_menu_choice'] == 'skill2'
    assert events[-1][1]['observed_metric']['omedetori_cycle']['rule'] == 'warm_after_mikan'
    assert menus.monster_menu_step(view(cursor=176), state) == [menus.pad('a')]
    for _ in range(3):
        assert menus.monster_menu_step(view(cursor=176), state) == [menus.pad('a')]
        assert state['monster_menu_choice'] == 'skill2'


def test_unreadable_cursor_bounds_mikan_to_one_unverified_attempt(events):
    state = mem()
    assert menus.monster_menu_step(view(cursor=None), state) == [menus.pad('a')]
    assert state['battle']['omedetori_cycle']['mikan_unverified_attempt'] is True
    assert state['battle']['omedetori_cycle']['mikan_submitted'] is False
    assert menus.monster_menu_step(view(cursor=None), state) == [menus.pad('down')]
    assert state['monster_menu_choice'] == 'skill2'


def test_unscoped_battle_holds_mikan_and_never_self_destructs(events):
    state = {'battle': {}}
    for _ in range(3):
        menus.monster_menu_step(view(cursor=160), state)
        assert state['monster_menu_choice'] == 'skill1'
    assert 'omedetori_cycle' not in state['battle']


def test_enemy_change_resets_the_mikan_count(events):
    state = mem('ハデス')
    menus.monster_menu_step(view(cursor=160, enemy='ハデス'), state)
    assert state['battle']['omedetori_cycle']['mikan_submitted'] is True
    state['battle']['enemy'] = 'クイーン'
    assert menus.monster_menu_step(view(cursor=160, enemy='クイーン'), state) == [menus.pad('a')]
    # The old scope's submitted press must not make the new enemy self-destruct.
    assert state['monster_menu_choice'] == 'skill1'
    assert events[-1][1]['observed_metric']['omedetori_cycle']['rule'] == 'mikan_once'
    assert state['battle']['omedetori_cycle']['scope'][1] == 'クイーン'
    assert state['battle']['omedetori_cycle']['mikan_submitted'] is True


def test_neither_named_skill_on_menu_does_not_engage_the_rule(events):
    state = mem()
    menus.monster_menu_step(view(cursor=160, skills=['ふくらむ', 'かぜ']), state)
    assert 'omedetori_cycle' not in state.get('battle', {})


def test_assessment_and_record_helpers_are_pure_to_their_scope():
    state = mem()
    ally, enemy = panel(), panel('ハデス', 500)
    assert omedetori_assessment(state, ally, enemy)['rule'] == 'mikan_once'
    omedetori_command_selected(state, ally, enemy, 'skill2')
    assert omedetori_assessment(state, ally, enemy)['rule'] == 'mikan_once'
    omedetori_command_selected(state, ally, enemy, 'skill1')
    assert omedetori_assessment(state, ally, enemy)['action'] == 'skill2'

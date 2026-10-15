"""Akematsu's one greeting followed by other-monster hospitality."""
import json
from types import SimpleNamespace

import pytest

from docich.hanjuku.flows import battle_menus as menus
from docich.hanjuku.judgment.akematsu import (
    akematsu_assessment, akematsu_command_selected,
)


def panel(name='あけまつ', hp=256):
    return SimpleNamespace(name=name, hp=hp)


def mem():
    return {'battle': {'ally': 'ヴィーナス', 'enemy': 'クイーン'}}


def view(*, cursor=160, enemy='ハデス', swapped=False, skills=None):
    skills = ['ごあいさつ', 'おもてなし'] if skills is None else skills
    ally = SimpleNamespace(name='あけまつ', hp=256, side='ally')
    other = SimpleNamespace(name=enemy, hp=500, side='enemy')
    if swapped:
        ally.side, other.side = 'enemy', 'ally'
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
    monkeypatch.setattr(menus.experience, 'situation_key', lambda *args: 'akematsu-test')
    # A previously learned first skill must never override the new one-shot tactic.
    monkeypatch.setattr(menus.experience, 'preferred', lambda *args, default, **kw: 'skill1')
    return recorded


def test_first_greeting_then_hospitality_even_when_hp_unchanged(events):
    state = mem()
    assert menus.monster_menu_step(view(cursor=160), state) == [menus.pad('a')]
    assert state['battle']['akematsu_exchange']['greeting_submitted'] is True
    assert state['monster_menu_choice'] == 'skill1'
    assert menus.monster_menu_step(view(cursor=160), state) == [menus.pad('down')]
    assert state['monster_menu_choice'] == 'skill2'
    assert events[-1][1]['observed_metric']['akematsu_exchange']['rule'] == 'hospitality_after_greeting'
    assert menus.monster_menu_step(view(cursor=176), state) == [menus.pad('a')]
    for _ in range(3):
        assert menus.monster_menu_step(view(cursor=176), state) == [menus.pad('a')]
        assert state['monster_menu_choice'] == 'skill2'


def test_repeated_predicted_cursor_does_not_count_greeting_as_sent(events):
    state = mem()
    for _ in range(3):
        assert menus.monster_menu_step(view(cursor=None), state) == [menus.pad('a')]
        assert state['battle']['akematsu_exchange']['greeting_submitted'] is False
    assert menus.monster_menu_step(view(cursor=160), state) == [menus.pad('a')]
    assert state['battle']['akematsu_exchange']['greeting_submitted'] is True
    assert menus.monster_menu_step(view(cursor=160), state) == [menus.pad('down')]


def test_cached_predeploy_greeting_is_replaced_after_first_new_policy_tick(events):
    state = mem()
    state.update(monster_menu_choice='skill1',
                 monster_menu_choice_hp=(256, 500),
                 monster_menu_choice_scope=['ally', 'あけまつ', 'ハデス'],
                 monster_menu_key=['ごあいさつ', 'おもてなし', 'たまごにもどれ'])
    state['battle']['akematsu_exchange'] = {
        'scope': ['ヴィーナス', 'クイーン', 'あけまつ', 'ハデス'],
        'greeting_submitted': True,
    }
    assert menus.monster_menu_step(view(cursor=160), state) == [menus.pad('down')]
    assert state['monster_menu_choice'] == 'skill2'
    assert state['monster_menu_choice_policy_epoch'] == '20261016-balloon-akematsu-v2'


def test_enemy_owned_akematsu_menu_is_not_assumed_ours(events):
    state = mem()
    assert menus.monster_menu_step(view(swapped=True), state) == []
    assert 'akematsu_exchange' not in state['battle']
    assert events[-1][0] == 'monster_menu_wait'


def test_opponent_and_battle_scope_reset_single_greeting(events):
    state = mem()
    assert menus.monster_menu_step(view(cursor=160), state) == [menus.pad('a')]
    assert menus.monster_menu_step(view(cursor=176, enemy='ダークエルフ'), state) == [menus.pad('up')]
    assert state['battle']['akematsu_exchange']['greeting_submitted'] is False
    assert menus.monster_menu_step(view(cursor=160, enemy='ダークエルフ'), state) == [menus.pad('a')]
    state['battle'] = {'ally': 'ヴィーナス', 'enemy': 'ピスタチオ'}
    assert menus.monster_menu_step(view(cursor=176, enemy='ダークエルフ'), state) == [menus.pad('up')]


def test_weakly_scoped_battle_does_not_loop_on_greeting(events):
    state = {}
    assert menus.monster_menu_step(view(cursor=160), state) == [menus.pad('down')]
    assert state['monster_menu_choice'] == 'skill2'


def test_akematsu_state_json_survives_real_observations():
    state = mem()
    assert akematsu_assessment(state, panel(), panel('ハデス', 500))['action'] == 'skill1'
    akematsu_command_selected(state, panel(), panel('ハデス', 500), 'skill1')
    saved = json.loads(json.dumps(state))
    assessment = akematsu_assessment(saved, panel(), panel('ハデス', 500))
    assert assessment['action'] == 'skill2'
    assert assessment['greeting_submitted'] is True
    assert akematsu_assessment(saved, panel(), panel('ヘーゼル', 500))['action'] == 'skill1'

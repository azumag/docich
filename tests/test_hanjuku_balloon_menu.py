"""Exercise the real monster-menu controller with synthetic parsed panels."""
from types import SimpleNamespace

import pytest

from docich.hanjuku.flows import battle_menus as menus


@pytest.fixture
def records(monkeypatch):
    events = []
    monkeypatch.setattr(menus, '_record', lambda mem, event, **kw: events.append((event, kw)))
    # Keep unrelated general-exposure and experience bookkeeping out of these
    # deterministic menu tests. Skill ownership/navigation remain real.
    monkeypatch.setattr(menus, '_summoner_return_risk', lambda *args: {})
    monkeypatch.setattr(menus.experience, 'situation_key', lambda *args: 'balloon-test')
    monkeypatch.setattr(menus.experience, 'preferred', lambda *args, default, **kw: default)
    return events


def memory():
    return {'battle': {'ally': 'ヴィーナス', 'enemy': 'クイーン'}}


def screen(hp, cursor=160, *, enemy='ヒュドラ', swapped=False, skills=None):
    skills = skills if skills is not None else ['ふくらむ', 'シャウト']
    ally = SimpleNamespace(name='バルーンフィンチ', hp=hp, side='ally')
    other = SimpleNamespace(name=enemy, hp=1000, side='enemy')
    if swapped:
        ally.side, other.side = 'enemy', 'ally'
    return SimpleNamespace(
        menu_rows=[SimpleNamespace(known=s, y=160 + i * 16)
                   for i, s in enumerate([*skills, 'たまごに もどれ'])],
        egg_rows=[ally, other], menu_cursor=cursor,
    )


def test_menu_recovery_shout_and_recovery_cycle(records):
    mem = memory()
    assert menus.monster_menu_step(screen(1000), mem) == [menus.pad('a')]
    assert mem['battle']['balloon_finch_survival']['pending']['action'] == 'skill1'
    assert menus.monster_menu_step(screen(1800), mem) == [menus.pad('down')]
    assert mem['monster_menu_choice'] == 'skill2'
    proof = records[-1][1]['observed_metric']['balloon_survival']
    assert proof['incoming_damage'] == 200
    assert proof['target_hp'] == 1502
    assert proof['rule'] == 'survival_buffer'
    # Moving the cursor is not yet selecting Shout.
    assert 'pending' not in mem['battle']['balloon_finch_survival']
    assert menus.monster_menu_step(screen(1800, 176), mem) == [menus.pad('a')]
    assert mem['battle']['balloon_finch_survival']['pending']['action'] == 'skill2'
    assert menus.monster_menu_step(screen(700), mem) == [menus.pad('a')]
    assert mem['monster_menu_choice'] == 'skill1'
    assert mem['battle']['balloon_finch_survival']['damage'] == 200
    assert mem['battle']['balloon_finch_survival']['samples'] == 1


def test_predicted_cursor_does_not_establish_damage_evidence(records):
    mem = memory()
    menus.monster_menu_step(screen(1000, None), mem)
    assert 'pending' not in mem['battle']['balloon_finch_survival']
    menus.monster_menu_step(screen(1800), mem)
    assert mem['monster_menu_choice'] == 'skill1'
    assert mem['battle']['balloon_finch_survival']['damage'] == 0


def test_enemy_owned_menu_does_not_learn_or_inject_balloon_policy(records):
    mem = memory()
    assert menus.monster_menu_step(screen(1000, swapped=True), mem) == []
    assert 'balloon_finch_survival' not in mem['battle']
    assert records[-1][0] == 'monster_menu_wait'


def test_new_monster_invalidates_early_shout_choice(records):
    mem = memory()
    menus.monster_menu_step(screen(1000), mem)
    menus.monster_menu_step(screen(1800), mem)
    assert mem['monster_menu_choice'] == 'skill2'
    menus.monster_menu_step(screen(1800, enemy='ダークエルフ'), mem)
    assert mem['monster_menu_choice'] == 'skill1'
    assert mem['battle']['balloon_finch_survival']['damage'] == 0


def test_unknown_damage_still_inflates_then_shouts_at_cap(records):
    mem = memory()
    menus.monster_menu_step(screen(9998), mem)
    assert mem['monster_menu_choice'] == 'skill1'
    menus.monster_menu_step(screen(9999), mem)
    assert mem['monster_menu_choice'] == 'skill2'
    assert records[-1][1]['observed_metric']['balloon_survival']['rule'] == 'hp_cap'


def test_ascii_spaces_in_skill_labels_preserve_balloon_recognition(records):
    mem = memory()
    spaced = ['ふく らむ', 'シャ ウト']
    menus.monster_menu_step(screen(1000, skills=spaced), mem)
    menus.monster_menu_step(screen(1800, skills=spaced), mem)
    assert mem['monster_menu_choice'] == 'skill2'
    assert mem['battle']['balloon_finch_survival']['damage'] == 200

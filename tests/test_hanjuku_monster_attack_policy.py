"""Regression contracts for issue #2835 opponent-aware summon attacks."""
from types import SimpleNamespace

from docich.hanjuku.judgment.events import _monster_attack_policy_default


def monster(name, hp=100):
    return SimpleNamespace(name=name, hp=hp)


def choose(ally, enemy, first, second, *, enemy_hp=100, battle=None):
    return _monster_attack_policy_default(
        'ally', monster(ally), monster(enemy, enemy_hp), [first, second],
        first, second, 100, enemy_hp, {'battle': battle or {}},
    )


def test_warawara_reverses_choice_for_monster_and_general_opponents():
    skills = ('９９かいパンチ', '９９かいキック')
    assert choose('ワラワラ', 'エッグスライム', *skills) == 'skill2'
    assert choose('ワラワラ', 'あたし', *skills) == 'skill1'


def test_warawara_uses_monster_damage_column_for_boss():
    skills = ('９９かいパンチ', '９９かいキック')
    assert choose('ワラワラ', 'クイーン', *skills) == 'skill2'


def test_self_damage_skill_is_excluded_from_normal_selection():
    assert choose('ウッドボール', 'エッグスライム', 'たいあたり', 'ばくはつする') == 'skill1'


def test_unregistered_monster_and_unverified_menu_fail_closed():
    assert choose('エッグスライム', 'クイーン', 'たいあたり', 'ころがる') is None
    assert choose('ワラワラ', 'クイーン', '９９かいパンチ', '読めない技') is None

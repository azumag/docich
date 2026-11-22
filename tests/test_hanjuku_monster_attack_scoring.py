"""Focused scoring contracts for issue #2835 summon attack policy."""

from docich.hanjuku.judgment.events import _score_monster_attack


def test_general_group_prefers_all_target_attack():
    single = ('single', 20, 40, 1, False, False)
    all_target = ('all', 10, 10, 1, True, False)

    assert _score_monster_attack(all_target, 'general', True) > _score_monster_attack(
        single, 'general', True
    )


def test_single_general_does_not_get_all_target_bonus():
    single = ('single', 20, 40, 1, False, False)
    all_target = ('all', 10, 10, 1, True, False)

    assert _score_monster_attack(single, 'general', False) > _score_monster_attack(
        all_target, 'general', False
    )


def test_single_general_counts_attack_hits():
    """Owner policy: a general battle prefers the skill with more attacks."""
    multi = ('multi', 6, 6, 4, False, False)
    single = ('single', 8, 11, 1, False, False)

    assert _score_monster_attack(multi, 'general', False) == 24
    assert _score_monster_attack(single, 'general', False) == 11
    assert _score_monster_attack(multi, 'general', False) > _score_monster_attack(
        single, 'general', False
    )


def test_monster_target_ranks_power_times_hits():
    multi = ('multi', 7, 2, 4, False, False)
    single = ('single', 14, 20, 1, False, False)

    assert _score_monster_attack(multi, 'monster', False) == 28
    assert _score_monster_attack(multi, 'monster', False) > _score_monster_attack(
        single, 'monster', False
    )


def test_instant_death_is_guaranteed_only_against_a_lone_general():
    death = ('エクスカリバる', 0, 0, 1, False, False)
    hits = ('マサムネる', 4, 3, 4, False, False)

    assert _score_monster_attack(death, 'general', False) > _score_monster_attack(
        hits, 'general', False
    )
    # A monster/boss target treats 即死 as an unquantified chance: the damage
    # total must win, matching the Excalibur/Unicorn rule.
    assert _score_monster_attack(death, 'monster', False) < _score_monster_attack(
        hits, 'monster', False
    )
    # With soldiers present the existing ALL/damage ranking decides.
    assert _score_monster_attack(death, 'general', True) < _score_monster_attack(
        hits, 'general', True
    )


def test_a_monster_opponent_prefers_the_all_target_attack():
    """Owner 2026-11-09: 相手もモンスターなら「全体攻撃」を選ぶ。

    An ALL row clears the enemy monster's escort as well as the monster, so it
    outranks a bigger single-target number in a summon battle.
    """
    single = ('single', 20, 40, 1, False, False)
    all_target = ('all', 5, 5, 1, True, False)

    assert _score_monster_attack(all_target, 'monster', False) > _score_monster_attack(
        single, 'monster', False
    )
    # Among ALL rows the damage columns still order them.
    stronger_all = ('all2', 9, 9, 1, True, False)
    assert _score_monster_attack(stronger_all, 'monster', False) > _score_monster_attack(
        all_target, 'monster', False
    )

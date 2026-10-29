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

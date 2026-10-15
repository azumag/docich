"""Regressions for the owner-requested early-shout recovery threshold."""
import json
from types import SimpleNamespace

import pytest

from docich.hanjuku.judgment.balloon import (
    BALLOON_SURVIVAL_HITS, balloon_assessment, balloon_command_selected,
)

CAP = 9999


def panel(hp, name='バルーンフィンチ'):
    return SimpleNamespace(hp=hp, name=name)


def state():
    return {'battle': {'ally': 'ヴィーナス', 'enemy': 'クイーン'}}


def assess(mem, hp, enemy='ヒュドラ'):
    return balloon_assessment(mem, panel(hp), panel(1000, enemy), CAP)


def selected(mem, hp, action='skill1', enemy='ヒュドラ'):
    balloon_command_selected(mem, panel(hp), panel(1000, enemy), action, CAP)


def learned():
    mem = state()
    assert assess(mem, 1000)['action'] == 'skill1'
    selected(mem, 1000)
    proof = assess(mem, 1800)
    assert proof['incoming_damage'] == 200
    return mem


@pytest.mark.parametrize('hp', [None, True, -1, 0, 192, 384, 768, 10000])
def test_invalid_or_low_hp_never_enables_early_shout(hp):
    assert assess(state(), hp)['action'] == 'skill1'


def test_full_cap_retains_existing_unknown_damage_fallback():
    proof = assess(state(), CAP)
    assert proof['action'] == 'skill2'
    assert proof['rule'] == 'hp_cap'
    assert proof['target_hp'] == CAP


@pytest.mark.parametrize('hp, action', [
    (4096, 'skill1'), (6143, 'skill1'),
    (6144, 'skill2'), (9998, 'skill2'),
])
def test_unmeasured_but_well_resourced_monster_shouts_before_cap(hp, action):
    proof = assess(state(), hp)
    assert proof['action'] == action
    assert proof['target_hp'] == 6144
    assert proof['rule'] == ('unmeasured_reserve' if action == 'skill2'
                             else 'recover_unmeasured')
    # No fabricated sample or claim that three enemy hits were measured.
    assert proof['incoming_damage'] is None
    assert proof['damage_samples'] == proof['zero_loss_samples'] == 0


def test_two_confirmed_zero_loss_inflations_lower_unmeasured_reserve():
    mem = state()
    selected(mem, 1024)
    one = assess(mem, 2048)
    assert one['zero_loss_samples'] == 1
    assert one['target_hp'] == 6144
    selected(mem, 2048)
    two = assess(mem, 4096)
    assert two['zero_loss_samples'] == 2
    assert two['action'] == 'skill2'
    assert two['rule'] == 'observed_zero_reserve'
    assert two['target_hp'] == 4096
    assert two['incoming_damage'] is None


def test_scope_change_invalidates_old_zero_loss_evidence():
    mem = state()
    selected(mem, 1024)
    assess(mem, 2048)
    selected(mem, 2048)
    assert assess(mem, 4096)['action'] == 'skill2'
    assert assess(mem, 4096, enemy='ダークエルフ')['action'] == 'skill1'
    assert mem['battle']['balloon_finch_survival']['zero_samples'] == 0


def test_unscoped_unknown_damage_never_uses_cautious_early_shout():
    assert assess({}, 9000)['action'] == 'skill1'



def test_confirmed_inflation_enables_shout_well_before_cap():
    proof = assess(learned(), 1800)
    assert proof['action'] == 'skill2'
    assert proof['rule'] == 'survival_buffer'
    assert proof['buffered_damage'] == 250
    assert proof['survival_hits'] == BALLOON_SURVIVAL_HITS == 3
    assert proof['target_hp'] == 1502


@pytest.mark.parametrize('hp,action', [(1499, 'skill1'), (1500, 'skill1'),
                                      (1501, 'skill1'), (1502, 'skill2')])
def test_three_hits_must_leave_positive_hp_after_shout(hp, action):
    assert assess(learned(), hp)['action'] == action


def test_shout_residual_conservatively_estimates_enemy_hit_not_self_halving():
    mem = learned()
    selected(mem, 1800, 'skill2')
    proof = assess(mem, 700)  # 900 after own half, minus 200 hit
    assert proof['incoming_damage'] == 400  # double residual covers attack-before-shout
    assert proof['damage_samples'] == 2
    assert proof['target_hp'] == 3002
    assert proof['action'] == 'skill1'


def test_shout_exact_half_is_zero_loss_not_a_false_large_hit():
    mem = state()
    selected(mem, 4096, 'skill2')
    proof = assess(mem, 2048)
    assert proof['incoming_damage'] is None
    assert proof['zero_loss_samples'] == 1
    assert proof['damage_samples'] == 0


def test_unobserved_shout_does_not_invent_a_residual_hit():
    mem = state()
    selected(mem, 4096, 'skill2')
    proof = assess(mem, 3000)  # above the own-HP half: shout unconfirmed
    assert proof['incoming_damage'] is None
    assert proof['zero_loss_samples'] == 0


def test_repeated_frames_and_selection_do_not_invent_samples():
    mem = state()
    selected(mem, 1000)
    for _ in range(5):
        assert assess(mem, 1000)['incoming_damage'] is None
        selected(mem, 1000)
    assert assess(mem, 1800)['damage_samples'] == 1
    assert assess(mem, 1800)['damage_samples'] == 1


def test_planning_without_selection_does_not_count_as_inflation():
    mem = state()
    assess(mem, 1000)
    assert assess(mem, 1800)['incoming_damage'] is None


@pytest.mark.parametrize('change', ['monster', 'general', 'new_battle'])
def test_damage_does_not_leak_to_another_opponent_or_battle(change):
    mem = learned()
    enemy = 'ヒュドラ'
    if change == 'monster':
        enemy = 'ダークエルフ'
    elif change == 'general':
        mem['battle']['enemy'] = 'ヘーゼル'
    else:
        mem['battle'] = {'ally': 'ヴィーナス', 'enemy': 'クイーン'}
    proof = assess(mem, 1800, enemy)
    assert proof['action'] == 'skill1'
    assert proof['incoming_damage'] is None


def test_missing_hp_invalidates_previous_estimate():
    mem = learned()
    assess(mem, None)
    assert assess(mem, 1800)['incoming_damage'] is None


def test_smaller_later_hit_does_not_lower_the_battle_maximum():
    mem = learned()
    selected(mem, 1000)
    proof = assess(mem, 1850)
    assert proof['incoming_damage'] == 200
    assert proof['damage_samples'] == 2


def test_larger_hit_immediately_raises_recovery_threshold():
    mem = learned()
    selected(mem, 1800)
    proof = assess(mem, 3100)
    assert proof['incoming_damage'] == 500
    assert proof['target_hp'] == 3752
    assert proof['action'] == 'skill1'


def test_capped_inflation_is_not_a_zero_loss_sample():
    mem = state()
    selected(mem, 6000)
    proof = assess(mem, 9900)
    assert proof['incoming_damage'] is None
    assert proof['zero_loss_samples'] == 0


def test_zero_loss_is_observed_but_not_reported_as_known_positive_damage():
    mem = state()
    selected(mem, 1000)
    proof = assess(mem, 2000)
    assert proof['incoming_damage'] is None
    assert proof['zero_loss_samples'] == 1


def test_tiny_measured_damage_keeps_a_minimum_post_shout_reserve():
    mem = state()
    selected(mem, 1000)
    proof = assess(mem, 1999)
    assert proof['incoming_damage'] == 1
    assert proof['buffered_damage'] == 2
    assert proof['target_hp'] == 1026
    assert assess(mem, 1025)['action'] == 'skill1'
    assert assess(mem, 1026)['action'] == 'skill2'


def test_unconfirmed_heal_or_large_loss_invalidates_small_old_estimate():
    mem = learned()
    selected(mem, 1000)
    proof = assess(mem, 950)
    assert proof['incoming_damage'] is None
    assert proof['action'] == 'skill1'


def test_json_round_trip_keeps_scope_and_pending_sample():
    mem = state()
    selected(mem, 1000)
    mem = json.loads(json.dumps(mem))
    assert assess(mem, 1800)['action'] == 'skill2'


def test_missing_battle_or_enemy_keeps_cap_fallback():
    assert assess({}, 1800)['action'] == 'skill1'
    mem = learned()
    proof = balloon_assessment(mem, panel(1800), None, CAP)
    assert proof['incoming_damage'] is None
    assert proof['action'] == 'skill1'


def test_fractional_damage_buffer_rounds_up():
    mem = state()
    selected(mem, 1000)
    proof = assess(mem, 1799)
    assert proof['incoming_damage'] == 201
    assert proof['buffered_damage'] == 252
    assert proof['target_hp'] == 1514

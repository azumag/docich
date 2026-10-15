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


@pytest.mark.parametrize('hp', [None, True, -1, 0, 192, 384, 768, 9000, 9998, 10000])
def test_unknown_or_invalid_hp_does_not_enable_early_shout(hp):
    assert assess(state(), hp)['action'] == 'skill1'


def test_full_cap_retains_existing_unknown_damage_fallback():
    proof = assess(state(), CAP)
    assert proof['action'] == 'skill2'
    assert proof['rule'] == 'hp_cap'


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


def test_shout_self_halving_is_not_an_enemy_damage_sample():
    mem = learned()
    selected(mem, 1800, 'skill2')
    proof = assess(mem, 700)  # 1800/2 - 200
    assert proof['incoming_damage'] == 200
    assert proof['damage_samples'] == 1
    assert proof['action'] == 'skill1'


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


def test_capped_inflation_and_zero_loss_are_not_safe_damage_evidence():
    mem = state()
    selected(mem, 6000)
    assert assess(mem, 9900)['incoming_damage'] is None
    mem = state()
    selected(mem, 1000)
    assert assess(mem, 2000)['incoming_damage'] is None


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

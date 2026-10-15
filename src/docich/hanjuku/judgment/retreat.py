"""Value-aware retreat policy for a currently observed ordinary melee.

Owner 2026-10-15: keep clearly favourable fights, protect strong generals
sooner, and accept more risk for weak or *observed* eggless generals.
The thresholds below are policy choices, not a damage formula or win odds.
Resource selection, the hero, summons, bosses and unread panels retain the
existing safety path. This module never reads a frame or sends pad input.
"""
from __future__ import annotations

from ... import hanjuku_chart as chart, hanjuku_reference as reference
from ..os_layer.world import NAME


STRONG_COMBAT = 10
STRONG_MAX_HP = 75
WEAK_COMBAT = 6
WEAK_MAX_HP = 40
STRONG_RETREAT_HP = 18
STRONG_RETREAT_PERCENT = 50
EXPENDABLE_RETREAT_HP = 4
EXPENDABLE_RETREAT_PERCENT = 20


def _strength(name):
    if not isinstance(name, str) or not name:
        return None
    value = reference.general_strength('しゅじんこう' if name == NAME else name)
    if (value is None or len(value) < 2
            or type(value[0]) is not int or value[0] <= 0
            or type(value[1]) is not int or not 1 <= value[1] <= 15):
        return None
    return value[0], value[1]


def retreat_profile(cur):
    """Strong status wins over egglessness; missing eggs never mean zero."""
    name = cur.get('ally')
    stats = _strength(name)
    if name in (NAME, 'しゅじんこう'):
        kind, basis = 'hero', 'game_over_protection'
    elif stats is None:
        kind, basis = 'unknown', 'unread_general_strength'
    elif stats[1] >= STRONG_COMBAT or stats[0] >= STRONG_MAX_HP:
        kind, basis = 'strong', 'general_combat_or_max_hp'
    elif cur.get('own_egg_zero_observed') is True:
        kind, basis = 'expendable', 'observed_egg_zero'
    elif stats[1] <= WEAK_COMBAT or stats[0] <= WEAK_MAX_HP:
        kind, basis = 'expendable', 'low_general_combat_or_max_hp'
    else:
        kind, basis = 'ordinary', 'ordinary_general'
    return {'class': kind, 'basis': basis,
            'max_hp': stats[0] if stats else None,
            'combat': stats[1] if stats else None}


def _live_melee(cur):
    """Use current bounded HP/troops, never a saved monster or unknown army."""
    if (cur.get('side') != 'attack'
            or (cur.get('hero_retreat') or {}).get('selected')
            or cur.get('ally') in (None, NAME, 'しゅじんこう')
            or cur.get('enemy') in chart.BOSSES.values()
            or cur.get('enemy') in reference.BOSS_GENERALS
            or cur.get('egg_battle') or cur.get('card_summon_observed')
            or cur.get('card_hp_unread') or cur.get('card_context_unclassified')
            or cur.get('card_soldiers_current') is not True
            or cur.get('card_flow') or cur.get('event_duel')):
        return False
    ally, enemy = _strength(cur.get('ally')), _strength(cur.get('enemy'))
    if ally is None or enemy is None:
        return False
    hp, enemy_hp = cur.get('ally_hp'), cur.get('enemy_hp')
    return (type(hp) is int and 0 < hp <= ally[0]
            and type(enemy_hp) is int and 0 < enemy_hp <= enemy[0]
            and all(type(cur.get(key)) is int and 0 <= cur[key] <= 6
                    for key in ('ally_soldiers', 'enemy_soldiers')))


def _resources_exhausted(cur):
    """Risk tolerance must not skip an untried egg or an active card plan."""
    rescue = cur.get('survival')
    return (isinstance(rescue, dict) and rescue.get('exhausted') is True
            and not rescue.get('cards_uncertain')
            and not cur.get('planned_cards') and not cur.get('card_flow')
            and not (cur.get('own_egg_positive_observed') is True
                     and not rescue.get('egg_attempted')))


def _dominant_melee(cur, balance, hp_floor):
    """Remaining force, not percentage lost since an arbitrary opening."""
    hp, enemy = cur['ally_hp'], cur['enemy_hp']
    values = [balance.get(key) for key in
              ('ally_force', 'enemy_force', 'ally_melee_score', 'enemy_melee_score')]
    return (balance.get('soldiers_current') is True
            and all(type(value) is int and value > 0 for value in values)
            and hp > hp_floor and hp > 2 * enemy
            and values[0] > 2 * values[1] and values[2] > 2 * values[3])


def _result(cur, profile, needed, reason, *, active=False, hp_floor=None, percent=None):
    # A fresh assessment is diagnostic state, never a cached permission.
    # All values are bounded policy labels/numbers; no new input/log channel.
    cur['retreat_assessment'] = {**profile, 'needed': bool(needed), 'reason': reason,
                               'threshold_active': active, 'critical_hp': hp_floor,
                               'retreat_percent': percent}
    return bool(needed)


def retreat_needed(cur):
    """Preserve resource-first safety, then compare value and current forces."""
    # Late import avoids a cycle and keeps the compatibility facade's test
    # rebinding pointed at the owning rules module.
    from . import battle as rules

    profile = retreat_profile(cur)
    baseline = rules._default_retreat_needed(cur)
    if not _live_melee(cur):
        return _result(cur, profile, baseline, 'existing_guard_unclassified_or_special')

    exhausted = _resources_exhausted(cur)
    risk_tolerant = profile['class'] == 'expendable' and exhausted
    floor = EXPENDABLE_RETREAT_HP if risk_tolerant else rules.GENERAL_CRITICAL_RETREAT_HP
    balance = rules._melee_balance(cur)
    if _dominant_melee(cur, balance, floor):
        return _result(cur, profile, False, 'measured_remaining_force_advantage',
                       active=True, hp_floor=floor)

    # Keep the ordinary rescue/retreat sequence until real resources have
    # been checked. A chart plan, unknown cards or an untried positive egg
    # must not be thrown away to implement a preservation preference.
    start, hp, enemy = (cur.get(key) for key in ('start_ally_hp', 'ally_hp', 'enemy_hp'))
    if (not exhausted or type(start) is not int
            or not hp <= start <= profile['max_hp']):
        return _result(cur, profile, baseline, 'existing_resource_first_guard')

    if profile['class'] == 'strong':
        behind = hp < enemy or rules._combat_behind(cur)
        threshold = (hp <= STRONG_RETREAT_HP
                     or (behind and hp * 100 <= profile['max_hp'] * STRONG_RETREAT_PERCENT))
        return _result(cur, profile, baseline or threshold, 'protect_strong_general',
                       active=True, hp_floor=STRONG_RETREAT_HP, percent=STRONG_RETREAT_PERCENT)
    if risk_tolerant:
        threshold = (hp <= EXPENDABLE_RETREAT_HP
                     or (hp < enemy and hp * 100 <= start * EXPENDABLE_RETREAT_PERCENT))
        return _result(cur, profile, threshold, 'accept_risk_after_resources_exhausted',
                       active=True, hp_floor=EXPENDABLE_RETREAT_HP,
                       percent=EXPENDABLE_RETREAT_PERCENT)
    return _result(cur, profile, baseline, 'existing_ordinary_guard')

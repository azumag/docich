"""Balloon Finch's measured recovery/shout policy (owner 2026-10-16).

Only positive losses following an observed, uncapped inflation contribute
an estimate. Shout's own HP halving is never enemy-damage evidence. State
belongs to the current battle, not a global monster-name cache.
"""
from __future__ import annotations


BALLOON_SURVIVAL_HITS = 3
_STATE_KEY = 'balloon_finch_survival'


def _hp(value, max_hp):
    return type(value) is int and 0 < value <= max_hp


def _state(mem, ally, enemy, max_hp):
    battle = mem.get('battle')
    if not isinstance(battle, dict):
        return None
    if (ally is None or enemy is None or ally.name != 'バルーンフィンチ'
            or not _hp(ally.hp, max_hp) or not _hp(enemy.hp, max_hp)):
        battle.pop(_STATE_KEY, None)
        return None
    scope = [battle.get('ally'), battle.get('enemy'), ally.name, enemy.name]
    state = battle.get(_STATE_KEY)
    if (not isinstance(state, dict) or state.get('scope') != scope
            or type(state.get('damage')) is not int
            or not 0 <= state['damage'] < max_hp
            or type(state.get('samples')) is not int or state['samples'] < 0):
        state = battle[_STATE_KEY] = {'scope': scope, 'damage': 0, 'samples': 0}
    return state


def balloon_assessment(mem, ally, enemy, max_hp):
    """Return a decision with auditable evidence; unknown damage keeps the cap.

    A visible own menu after a selected inflation must show an HP increase
    before we infer that inflation occurred. Below the cap, 2*old - new is
    a conservative *turn-loss estimate*: damage before inflation is doubled,
    not underestimated. Capped inflation can hide damage and is not sampled.
    This is not a guarantee against an unseen stronger attack or instant death.
    """
    state = _state(mem, ally, enemy, max_hp)
    hp = ally.hp if ally is not None else None
    if state is not None:
        pending = state.get('pending')
        if isinstance(pending, dict):
            old = pending.get('hp')
            if not _hp(old, max_hp) or pending.get('action') not in ('skill1', 'skill2'):
                state.update(damage=0, samples=0)
                state.pop('pending', None)
            elif hp != old:
                state.pop('pending', None)
                if pending['action'] == 'skill1':
                    if not old < hp <= min(2 * old, max_hp):
                        # A failed/unobserved heal or unexpectedly large loss
                        # must not reuse an earlier small damage estimate.
                        state.update(damage=0, samples=0)
                    elif 2 * old <= max_hp:
                        loss = 2 * old - hp
                        if loss > 0:
                            state['damage'] = max(state['damage'], loss)
                            state['samples'] += 1
                # A skill2 transition includes our own halving: never sample it.
    damage = state['damage'] if state is not None else 0
    # Round the 25% buffer up, then require positive HP after all three hits.
    buffered = (5 * damage + 3) // 4 if damage else None
    required = (BALLOON_SURVIVAL_HITS * buffered if buffered is not None else None)
    target = min(max_hp, 2 * (required + 1)) if required is not None else max_hp
    safe = (_hp(hp, max_hp) and required is not None and hp // 2 > required)
    capped = _hp(hp, max_hp) and hp == max_hp
    return {'action': 'skill2' if safe or capped else 'skill1',
            'rule': 'survival_buffer' if safe else 'hp_cap' if capped else
                    'recover_buffer' if damage else 'damage_unknown',
            'target_hp': target, 'incoming_damage': damage or None,
            'buffered_damage': buffered, 'survival_hits': BALLOON_SURVIVAL_HITS,
            'post_shout_hp': hp // 2 if _hp(hp, max_hp) else None,
            'damage_samples': state['samples'] if state is not None else 0}


def balloon_command_selected(mem, ally, enemy, action, max_hp):
    """Remember a cursor-confirmed command, not merely a planned choice."""
    state = _state(mem, ally, enemy, max_hp)
    if state is None or action not in ('skill1', 'skill2'):
        return
    pending = state.get('pending')
    if isinstance(pending, dict) and pending.get('hp') == ally.hp:
        return  # a repeated unchanged menu is not another resolved turn
    state['pending'] = {'action': action, 'hp': ally.hp}

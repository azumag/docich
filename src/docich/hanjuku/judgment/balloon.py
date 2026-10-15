"""Balloon Finch: measured survival and bounded no-damage-evidence fallback.

Positive, observed turn losses set the three-hit budget. With no positive
sample, use a generous, explicitly *unproven* reserve instead of waiting for
HP 9999 forever. Shout's own halving is never treated as enemy damage.
"""
from __future__ import annotations

BALLOON_SURVIVAL_HITS = 3
BALLOON_UNMEASURED_SHOUT_HP = 6144
BALLOON_ZERO_LOSS_SHOUT_HP = 4096
BALLOON_ZERO_LOSS_REQUIRED_SAMPLES = 2
BALLOON_MIN_POST_SHOUT_HP = 512
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
            or type(state.get('samples')) is not int or state['samples'] < 0
            or type(state.get('zero_samples', 0)) is not int
            or state.get('zero_samples', 0) < 0):
        state = battle[_STATE_KEY] = {
            'scope': scope, 'damage': 0, 'samples': 0, 'zero_samples': 0,
        }
    else:
        # Preserve positive-damage evidence from the previous deployment.
        state.setdefault('zero_samples', 0)
    return state


def _sample(state, loss):
    if loss == 0:
        state['zero_samples'] += 1
    elif loss > 0:
        state['damage'] = max(state['damage'], loss)
        state['samples'] += 1


def balloon_assessment(mem, ally, enemy, max_hp):
    """Choose a bounded early shout; distinguish proven and unproven reserves.

    An observed uncapped inflate yields 2*old-new: a conservative turn-loss
    estimate even if the enemy attacks before inflation. An observed shout
    yields at most old/2 HP, so double the residual loss to cover attack-before-
    shout ordering. Neither formula covers unobserved stronger attacks.
    """
    state = _state(mem, ally, enemy, max_hp)
    hp = ally.hp if ally is not None else None
    if state is not None:
        pending = state.get('pending')
        if isinstance(pending, dict):
            old = pending.get('hp')
            if not _hp(old, max_hp) or pending.get('action') not in ('skill1', 'skill2'):
                state.update(damage=0, samples=0, zero_samples=0)
                state.pop('pending', None)
            elif hp != old:
                state.pop('pending', None)
                if pending['action'] == 'skill1':
                    if not old < hp <= min(2 * old, max_hp):
                        # Failed/unobserved inflation: old estimates may be
                        # too small, so clear them rather than guessing.
                        state.update(damage=0, samples=0, zero_samples=0)
                    elif 2 * old <= max_hp:
                        # Verified zero loss is a valid observation, not a
                        # guarantee that the next enemy attack also misses.
                        _sample(state, 2 * old - hp)
                else:
                    # The own-HP half is an expected part of シャウト, not
                    # enemy damage. Residual loss gives a conservative bound.
                    expected = (old + 1) // 2
                    if _hp(hp, max_hp) and hp <= expected:
                        _sample(state, 2 * (expected - hp))
    damage = state['damage'] if state is not None else 0
    zeros = state['zero_samples'] if state is not None else 0
    buffered = (5 * damage + 3) // 4 if damage else None
    required = BALLOON_SURVIVAL_HITS * buffered if buffered is not None else None
    if required is not None:
        reserve = max(required, BALLOON_MIN_POST_SHOUT_HP)
        target = min(max_hp, 2 * (reserve + 1))
        early = _hp(hp, max_hp) and hp // 2 > reserve
    elif state is not None:
        # Explicitly cautious heuristics, not proof of surviving three hits.
        # The unknown route also handles missed/read-unavailable cursors.
        target = min(max_hp, (BALLOON_ZERO_LOSS_SHOUT_HP
                              if zeros >= BALLOON_ZERO_LOSS_REQUIRED_SAMPLES
                              else BALLOON_UNMEASURED_SHOUT_HP))
        early = _hp(hp, max_hp) and hp >= target
    else:
        target, early = max_hp, False
    capped = _hp(hp, max_hp) and hp == max_hp
    if capped:
        rule = 'hp_cap'
    elif early and damage:
        rule = 'survival_buffer'
    elif early:
        rule = ('observed_zero_reserve' if zeros >= BALLOON_ZERO_LOSS_REQUIRED_SAMPLES
                else 'unmeasured_reserve')
    else:
        rule = ('recover_buffer' if damage else
                'recover_unmeasured' if state is not None else 'damage_unknown')
    return {'action': 'skill2' if early or capped else 'skill1',
            'rule': rule, 'target_hp': max_hp if capped else target,
            'incoming_damage': damage or None, 'buffered_damage': buffered,
            'survival_hits': BALLOON_SURVIVAL_HITS,
            'post_shout_hp': hp // 2 if _hp(hp, max_hp) else None,
            'damage_samples': state['samples'] if state is not None else 0,
            'zero_loss_samples': zeros}


def balloon_command_selected(mem, ally, enemy, action, max_hp):
    """Remember a cursor-confirmed command, not merely a planned choice."""
    state = _state(mem, ally, enemy, max_hp)
    if state is None or action not in ('skill1', 'skill2'):
        return
    pending = state.get('pending')
    if isinstance(pending, dict) and pending.get('hp') == ally.hp:
        return  # a repeated unchanged menu is not another resolved turn
    state['pending'] = {'action': action, 'hp': ally.hp}

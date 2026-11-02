"""Omedetori: one Mikan press, then self-destruct warming (owner 2026-10-29, #2945).

おめでとり offers ミカンをおす (skill1) and あたためる (skill2, self-destruct).
Record *one* cursor-confirmed ミカンをおす per battle/monster scope, then
prefer あたためる at the next menu decision. Without a trustworthy battle
scope the self-destruct is never chosen. This is a command receipt, not proof
of the effect.
"""
from __future__ import annotations

_STATE_KEY = 'omedetori_cycle'


def _state(mem, ally, enemy):
    battle = mem.get('battle')
    if not isinstance(battle, dict):
        return None
    if (ally is None or enemy is None or ally.name != 'おめでとり'
            or not isinstance(battle.get('ally'), str)
            or not isinstance(battle.get('enemy'), str)):
        battle.pop(_STATE_KEY, None)
        return None
    scope = [battle['ally'], battle['enemy'], ally.name, enemy.name]
    value = battle.get(_STATE_KEY)
    if (not isinstance(value, dict) or value.get('scope') != scope
            or type(value.get('mikan_submitted')) is not bool
            or type(value.get('mikan_unverified_attempt', False)) is not bool):
        value = battle[_STATE_KEY] = {'scope': scope, 'mikan_submitted': False,
                                      'mikan_unverified_attempt': False}
    else:
        value.setdefault('mikan_unverified_attempt', False)
    return value


def omedetori_assessment(mem, ally, enemy):
    """Untrusted scope never self-destructs; it holds on ミカンをおす instead."""
    value = _state(mem, ally, enemy)
    if value is None:
        return {'action': 'skill1', 'rule': 'unscoped_mikan_hold',
                'mikan_submitted': False, 'mikan_unverified_attempt': False}
    sent = value['mikan_submitted']
    attempted = value['mikan_unverified_attempt']
    return {'action': 'skill2' if sent or attempted else 'skill1',
            'rule': ('warm_after_mikan' if sent else
                     'warm_after_unverified_attempt' if attempted else
                     'mikan_once'),
            'mikan_submitted': sent,
            'mikan_unverified_attempt': attempted}


def omedetori_command_selected(mem, ally, enemy, action, *, cursor_confirmed=True):
    """Record exactly one ミカンをおす attempt, distinguishing measured cursor.

    A predicted-cursor press is only an *unverified attempt*; never claim the
    press landed. Do not repeat it forever on a permanently unread cursor.
    """
    if action != 'skill1':
        return
    state = _state(mem, ally, enemy)
    if state is None:
        return
    if cursor_confirmed:
        state['mikan_submitted'] = True
    else:
        state['mikan_unverified_attempt'] = True

"""One-shot Akematsu greeting followed by hospitality (owner 2026-10-16).

ごあいさつ swaps abilities and does not directly damage the opponent.
Repeated greetings can undo the swap or make no progress. Record *one*
cursor-confirmed A command per battle/monster scope, then prefer the summon
skill おもてなし. This is a command receipt, not proof of the effect.
"""
from __future__ import annotations

_STATE_KEY = 'akematsu_exchange'


def _state(mem, ally, enemy):
    battle = mem.get('battle')
    if not isinstance(battle, dict):
        return None
    if (ally is None or enemy is None or ally.name != 'あけまつ'
            or not isinstance(battle.get('ally'), str)
            or not isinstance(battle.get('enemy'), str)):
        battle.pop(_STATE_KEY, None)
        return None
    scope = [battle['ally'], battle['enemy'], ally.name, enemy.name]
    value = battle.get(_STATE_KEY)
    if (not isinstance(value, dict) or value.get('scope') != scope
            or type(value.get('greeting_submitted')) is not bool
            or type(value.get('greeting_unverified_attempt', False)) is not bool):
        value = battle[_STATE_KEY] = {'scope': scope, 'greeting_submitted': False,
                                      'greeting_unverified_attempt': False}
    else:
        value.setdefault('greeting_unverified_attempt', False)
    return value


def akematsu_assessment(mem, ally, enemy):
    """Without a trustworthy battle scope choose the useful second skill."""
    value = _state(mem, ally, enemy)
    if value is None:
        return {'action': 'skill2', 'rule': 'unscoped_hospitality',
                'greeting_submitted': False}
    sent = value['greeting_submitted']
    attempted = value['greeting_unverified_attempt']
    return {'action': 'skill2' if sent or attempted else 'skill1',
            'rule': ('hospitality_after_greeting' if sent else
                     'hospitality_after_unverified_attempt' if attempted else
                     'greeting_once'),
            'greeting_submitted': sent,
            'greeting_unverified_attempt': attempted}


def akematsu_command_selected(mem, ally, enemy, action, *, cursor_confirmed=True):
    """Record exactly one command attempt, distinguishing measured cursor.

    The input gateway's acceptance is not observable here. A predicted-cursor
    command is only an *unverified attempt*; never claim the swap happened.
    But do not send greeting forever on a permanently unread cursor either.
    """
    if action != 'skill1':
        return
    state = _state(mem, ally, enemy)
    if state is None:
        return
    if cursor_confirmed:
        state['greeting_submitted'] = True
    else:
        state['greeting_unverified_attempt'] = True

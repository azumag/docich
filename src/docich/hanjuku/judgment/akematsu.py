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
            or type(value.get('greeting_submitted')) is not bool):
        value = battle[_STATE_KEY] = {'scope': scope, 'greeting_submitted': False}
    return value


def akematsu_assessment(mem, ally, enemy):
    """Without a trustworthy battle scope choose the useful second skill."""
    value = _state(mem, ally, enemy)
    if value is None:
        return {'action': 'skill2', 'rule': 'unscoped_hospitality',
                'greeting_submitted': False}
    sent = value['greeting_submitted']
    return {'action': 'skill2' if sent else 'skill1',
            'rule': 'hospitality_after_greeting' if sent else 'greeting_once',
            'greeting_submitted': sent}


def akematsu_command_selected(mem, ally, enemy, action):
    """Never count a cursor prediction or a planned-but-unconfirmed action."""
    if action != 'skill1':
        return
    state = _state(mem, ally, enemy)
    if state is not None:
        state['greeting_submitted'] = True

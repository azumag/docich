"""Issue #2573 post-merge review: the unaccepted close must be a real action.

`_battle_menu_move` returns a *single* pad dict (or ``'here'``/``None``/``[]``),
exactly like :func:`_battle_menu_to`. The first merged version returned
``[pad('b')]`` on the unaccepted-close path, and every caller wraps a dict with
``[move] if move else []``, so the action list became ``[[pad('b')]]``. The
production driver rejects a list item

    ActionError: 各アクションは object である必要があります: [...]

which means the bounded escape could not close the menu at all. These tests pin
the shape end to end: helper contract, every command-box owner, the JSON the
bot prints, and the CommandBrain that parses it.
"""
from pathlib import Path
import importlib.util
import json
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import pytest  # noqa: E402

from docich import hanjuku_policy as p  # noqa: E402
from docich.actions import ActionError, parse_actions  # noqa: E402
from docich.agent import brains  # noqa: E402
from docich.hanjuku.flows.battle_menus import (BATTLE_MENU_STALL_RETRIES,  # noqa: E402
                                               BATTLE_MENU_STALL_WAIT, _battle_menu_move)
from docich.hanjuku_font import TextLine  # noqa: E402
from docich.hanjuku_screen import Screen  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SHA = 'ec79c498bbc98f6c1c8401d4d581872d5cb52d3919f4c646e4a9ac1f932ad330'

# One press, then per retry WAIT holds + one press, then WAIT holds before the
# close. Driving ``EPISODE + 1`` observations always reaches the bounded close.
EPISODE = 1 + BATTLE_MENU_STALL_RETRIES * (BATTLE_MENU_STALL_WAIT + 1) + BATTLE_MENU_STALL_WAIT


def survival_state(**extra):
    """The keys the survival owner reads; incomplete state raises KeyError."""
    state = {'opens': 1, 'pending_opens': 0, 'menu_ticks': 0, 'cards_attempted': [],
             'cards_checked': 0, 'egg_attempted': False, 'egg_pending': False,
             'listed_cards': []}
    state.update(extra)
    return state


def command_box(cursor=176, *, sha=SHA):
    rows = [TextLine(176, ((176, 'たまごをつかう'),)),
            TextLine(192, ((176, 'きりふだ'),)),
            TextLine(208, ((176, 'たいきゃく'),))]
    return Screen(rows, None, 'たまごをつかうきりふだたいきゃく',
                  kind='battle_menu', menu_rows=rows, menu_cursor=cursor,
                  frame_sha256=sha)


def battle_memory(**extra):
    cur = {'ally': 'ゼウス', 'enemy': 'クイーン', 'ally_hp': 85, 'enemy_hp': 70,
           'start_ally_hp': 85, 'start_enemy_hp': 70, 'side': 'defense',
           'cards_used': [], 'cards_selected': [], 'cards_missing': [],
           'card_evidence_version': 1, 'card_consumption_complete': True}
    cur.update(extra)
    return {'chapter': 1, '_records': [], 'battle': cur}


def drive_to_close(mem, label='きりふだ', *, box=None):
    """Return the first unaccepted-close value the helper emits."""
    for _ in range(EPISODE + 1):
        move = _battle_menu_move(box or command_box(), mem, label)
        if isinstance(move, dict) and move.get('buttons') == ['b']:
            return move
    raise AssertionError('the episode never reached its bounded close')


# ------------------------------------------------------- helper return contract


def test_unaccepted_close_is_a_single_pad_dict_not_a_list():
    mem = battle_memory()
    close = drive_to_close(mem)
    assert isinstance(close, dict), close
    assert close['buttons'] == ['b']
    # The episode holds, retries the same press, and only then closes.
    decisions = [r['decision'] for r in mem['_records']]
    assert decisions.count('battle_menu_nav_retry') == BATTLE_MENU_STALL_RETRIES
    assert decisions[-1] == 'battle_menu_nav_unaccepted'


def test_every_helper_shape_is_usable_by_the_callers():
    # 'here' selects, a direction dict is wrapped once, None waits on an unread
    # cursor, and [] holds. Only the dict shapes may become a one-item list.
    mem = battle_memory()
    assert _battle_menu_move(command_box(192), mem, 'きりふだ') == 'here'
    assert _battle_menu_move(command_box(176), mem, 'きりふだ')['buttons'] == ['down']
    assert _battle_menu_move(command_box(None), mem, 'きりふだ') is None
    assert _battle_menu_move(command_box(sha=None), mem, 'きりふだ')['buttons'] == ['down']
    # A hold needs one observed press first: the identical frame that follows it
    # plans no input instead of re-pressing.
    hold_mem = battle_memory(card_flow={'stage': 'down'})
    assert _battle_menu_move(command_box(176), hold_mem, 'きりふだ')['buttons'] == ['down']
    assert _battle_menu_move(command_box(176), hold_mem, 'きりふだ') == []


def test_wrapping_the_close_once_yields_a_parsable_action_list():
    close = drive_to_close(battle_memory())
    wrapped = [close]  # what every caller builds from a dict move
    parsed = parse_actions(wrapped)
    assert [(a.type, a.buttons) for a in parsed] == [('pad', ['b'])]
    # The pre-fix nested shape is the exact defect this test guards.
    with pytest.raises(ActionError):
        parse_actions([wrapped])


# ------------------------------------------------- every command-box owner path


def test_retreat_owner_close_parses():
    mem = battle_memory(survival=survival_state(), side='attack')
    for _ in range(EPISODE + 1):
        actions = p.battle_menu_step(command_box(), mem)
        parse_actions(actions)
        if [a.get('buttons') for a in actions] == [['b']]:
            return
    raise AssertionError('the retreat owner never closed')


def test_survival_owner_close_parses():
    mem = battle_memory(survival=survival_state())
    for _ in range(EPISODE + 1):
        actions = p.battle_menu_step(command_box(), mem)
        parse_actions(actions)
        if [a.get('buttons') for a in actions] == [['b']]:
            return
    raise AssertionError('the survival owner never closed')


def test_charted_card_owner_close_parses_and_does_not_claim_progress():
    """The owner's bounded budget closes first; either way nothing is confirmed."""
    mem = battle_memory(card_flow={'card': 'クースカン', 'stage': 'down'})
    closed = False
    for _ in range(EPISODE + 1):
        actions = p.battle_menu_step(command_box(), mem)
        parse_actions(actions)
        assert all(isinstance(a, dict) for a in actions), actions
        if [a.get('buttons') for a in actions] == [['b']]:
            closed = True
            break
    assert closed, 'the charted-card owner never closed'
    # No selection and no use is claimed; the flow is released either way.
    assert mem['battle']['cards_selected'] == [] and mem['battle']['cards_used'] == []
    assert mem['battle'].get('card_flow') is None
    decisions = [r['decision'] for r in mem['_records']]
    assert 'battle_card_selected' not in decisions
    assert 'battle_card_open_unclassified' in decisions or 'battle_menu_nav_unaccepted' in decisions


def test_egg_battle_owner_close_parses_without_opening_a_card_flow():
    mem = battle_memory(egg_battle=True)
    for _ in range(EPISODE + 1):
        actions = p.battle_menu_step(command_box(), mem)
        parse_actions(actions)
        if [a.get('buttons') for a in actions] == [['b']]:
            break
    else:
        raise AssertionError('the egg-battle owner never closed')
    assert not mem['battle'].get('card_flow')


@pytest.mark.parametrize('label', ['たまごをつかう', 'きりふだ', 'たいきゃく'])
def test_no_owner_ever_emits_a_nested_list(label):
    mem = battle_memory(card_flow={'card': 'クースカン', 'stage': 'down'})
    for _ in range(EPISODE + 1):
        actions = p.battle_menu_step(command_box(), mem)
        assert all(isinstance(a, dict) for a in actions), actions
        parse_actions(actions)


# ------------------------------------------- production driver: bot -> parse_actions


def _bot_entry():
    spec = importlib.util.spec_from_file_location('hanjuku_bot_shape_entry',
                                                  ROOT / 'brains/hanjuku/bot.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_printed_bot_json_parses_for_the_close_episode():
    """What the agent loop actually consumes: {'actions': [...]} on stdout."""
    mem = battle_memory(card_flow={'card': 'クースカン', 'stage': 'down'})
    emitted = []
    for _ in range(EPISODE + 1):
        actions = p.battle_menu_step(command_box(), mem)
        emitted.extend(actions)
        payload = json.dumps({'actions': actions, 'observation_interval_ms': 1500})
        parsed = parse_actions(json.loads(payload)['actions'])
        assert all(a.type in {'pad', 'wait', 'key', 'text', 'special'} for a in parsed)
    assert any(a.get('buttons') == ['b'] for a in emitted)
    assert all(isinstance(a, dict) for a in emitted)


def test_command_brain_accepts_the_close_action_from_the_bot_stdout():
    """CommandBrain is the production parse boundary for this JSON."""
    action = drive_to_close(battle_memory())
    stdout = json.dumps({'actions': [action], 'observation_interval_ms': 1500})
    game = SimpleNamespace(name='hanjuku-hero', raw={'hanjuku': {'script_bot': True}},
                           agent=SimpleNamespace(command=['unused']))
    global_config = SimpleNamespace(repo_root=ROOT, agent=SimpleNamespace(brain_timeout_s=5))
    brain = brains.CommandBrain(global_config, game)
    obs = SimpleNamespace(to_json=lambda: '{}')
    result = SimpleNamespace(returncode=0, stdout=stdout, stderr='')
    with patch.object(brains.procs, 'run', return_value=result):
        actions = brain.decide(obs)
    assert [(a.type, a.buttons) for a in actions] == [('pad', ['b'])]


def test_command_brain_still_rejects_the_pre_fix_nested_shape():
    """Guard the defect itself: a list item is not a valid action object."""
    close = drive_to_close(battle_memory())
    stdout = json.dumps({'actions': [[close]], 'observation_interval_ms': 1500})
    game = SimpleNamespace(name='hanjuku-hero', raw={'hanjuku': {'script_bot': True}},
                           agent=SimpleNamespace(command=['unused']))
    global_config = SimpleNamespace(repo_root=ROOT, agent=SimpleNamespace(brain_timeout_s=5))
    brain = brains.CommandBrain(global_config, game)
    obs = SimpleNamespace(to_json=lambda: '{}')
    result = SimpleNamespace(returncode=0, stdout=stdout, stderr='')
    with patch.object(brains.procs, 'run', return_value=result):
        assert brain.decide(obs) == []  # no guess, no input
    assert brain.observation_interval_ms is None

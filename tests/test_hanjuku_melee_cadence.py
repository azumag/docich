"""Melee timing through the actual command protocol and fenced delivery loop."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from docich.actions import parse_actions
from docich.agent import brains, loop
from docich.agent.fence import AgentFence, FenceLost
from docich.hanjuku_policy import battle_step
from docich.hanjuku_screen import Battle, Screen
from docich.xkit import XKit

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('hanjuku_cadence_entry', ROOT / 'brains/hanjuku/bot.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


def melee_state():
    return {'screen_kind': 'battle', 'policy': {'battle': {'ally_hp': 90, 'enemy_hp': 90}}}


def test_short_feedback_only_for_living_human_melee():
    state = melee_state()
    assert entry.observation_interval_ms(state) == 500
    for kind in ('unknown', 'text', 'battle_menu', 'egg_battle_menu', 'monster_menu', 'name_entry', 'map'):
        assert entry.observation_interval_ms({**state, 'screen_kind': kind}) == 1500
    for flag, value in [('egg_battle', True), ('battle', {'ally_hp': 0, 'enemy_hp': 15}),
                        ('battle', {'ally_hp': None, 'enemy_hp': 15})]:
        changed = melee_state()
        changed['policy'][flag] = value
        assert entry.observation_interval_ms(changed) == 1500


@pytest.mark.parametrize('stage', ['menu', 'cards', 'announce'])
@pytest.mark.parametrize('kind', ['battle', 'unknown', 'text', 'battle_menu'])
def test_card_chain_keeps_short_feedback_across_command_fades(stage, kind):
    state = melee_state()
    state['screen_kind'] = kind
    state['policy']['battle']['card_flow'] = {'stage': stage, 'card': 'クースカン'}
    assert entry.observation_interval_ms(state) == 500
    state['policy']['egg_battle'] = True
    assert entry.observation_interval_ms(state) == 1500


def test_card_flow_does_not_speed_up_field_or_resolved_battle():
    state = melee_state()
    state['policy']['battle']['card_flow'] = {'stage': 'menu'}
    assert entry.observation_interval_ms({**state, 'screen_kind': 'map'}) == 1500
    state['policy']['battle']['ally_hp'] = 0
    assert entry.observation_interval_ms(state) == 1500


@pytest.mark.parametrize('stage', ['opening', 'menu', 'selected'])
@pytest.mark.parametrize('kind', ['unknown', 'text', 'battle_menu', 'battle_menu_pending', 'okunote_menu'])
def test_defender_egg_preemption_keeps_short_feedback_through_its_actual_menus(stage, kind):
    state = melee_state()
    state['screen_kind'] = kind
    state['policy']['battle']['okunote_egg_preempt'] = {'stage': stage}
    assert entry.observation_interval_ms(state) == 500
    state['policy']['battle']['okunote_egg_preempt']['exhausted'] = True
    assert entry.observation_interval_ms(state) == 1500


@pytest.mark.parametrize('kind', ['map', 'egg_battle_menu', 'monster_menu'])
def test_defender_preemption_does_not_change_other_screen_cadence(kind):
    state = melee_state()
    state['screen_kind'] = kind
    state['policy']['battle']['okunote_egg_preempt'] = {'stage': 'opening'}
    assert entry.observation_interval_ms(state) == 1500


def test_an_unacknowledged_menu_close_keeps_short_feedback_only_while_alive():
    state = melee_state()
    state['screen_kind'] = 'unknown'
    state['policy']['battle']['okunote_egg_preempt'] = {
        'stage': 'unavailable', 'exhausted': True, 'close_pending': True}
    assert entry.observation_interval_ms(state) == 500
    state['policy']['battle']['ally_hp'] = 0
    assert entry.observation_interval_ms(state) == 1500


def test_completed_preemption_does_not_keep_later_text_on_fast_feedback():
    state = melee_state()
    state['screen_kind'] = 'text'
    state['policy']['battle']['okunote_egg_preempt'] = {'stage': 'completed', 'selected': True}
    assert entry.observation_interval_ms(state) == 1500


@pytest.mark.parametrize('name,scripted,interval,expected', [
    ('hanjuku-hero', True, 500, 500), ('hanjuku-hero', True, 1500, 1500),
    ('hanjuku-hero', True, 0, None), ('hanjuku-hero', True, True, None),
    ('hanjuku-hero', True, 501, None), ('hanjuku-hero', False, 500, None),
    ('other-game', True, 500, None)])
def test_command_protocol_limits_cadence_and_resets_after_error(name, scripted, interval, expected):
    game = SimpleNamespace(name=name, raw={'hanjuku': {'script_bot': scripted}},
                           agent=SimpleNamespace(command=['unused']))
    global_config = SimpleNamespace(repo_root=ROOT, agent=SimpleNamespace(brain_timeout_s=5))
    brain = brains.CommandBrain(global_config, game)
    obs = SimpleNamespace(to_json=lambda: '{}')
    result = SimpleNamespace(returncode=0, stdout=json.dumps({
        'actions': [{'type': 'pad', 'buttons': ['a'], 'hold_ms': 50}, {'type': 'wait', 'ms': 50}],
        'observation_interval_ms': interval}), stderr='')
    with patch.object(brains.procs, 'run', return_value=result):
        actions = brain.decide(obs)
    assert [a.type for a in actions] == ['pad', 'wait']
    assert brain.observation_interval_ms == expected
    with patch.object(brains.procs, 'run', side_effect=OSError('unavailable')):
        assert brain.decide(obs) == []
    assert brain.observation_interval_ms is None


def melee_actions():
    screen = Screen(lines=[], hand=None, text='', kind='battle', battle=Battle('だいじん', 90, 'どうし', 90))
    memory = {}
    battle_step(screen, memory)
    return parse_actions(battle_step(screen, memory))


def test_loop_delivers_separate_press_edges_and_reobserves_after_bounded_burst():
    now = 0
    edges = []
    def sleep(seconds):
        nonlocal now
        now += seconds
    kit = XKit(':test')
    kit.keydown = lambda keys: edges.append((now, 'down'))
    kit.keyup = lambda keys: edges.append((now, 'up'))
    adapter = SimpleNamespace(observe=lambda: None, act=lambda a: kit.tap(['x'], a.hold_ms))
    brain = SimpleNamespace(decide=lambda obs: melee_actions())
    with patch.object(loop.time, 'sleep', side_effect=sleep):
        loop._run_iteration(adapter, brain, 500)
    assert len([e for e in edges if e[1] == 'down']) == 4
    for i in range(0, len(edges), 2):
        assert edges[i+1][0] - edges[i][0] >= .049
        if i+2 < len(edges):
            assert edges[i+2][0] - edges[i+1][0] >= .049
    assert now <= .5   # No long autonomous batch before the next observation.


def test_lease_loss_during_release_cancels_remaining_taps(tmp_path):
    sent = []
    lost = False
    def check(*args):
        if lost:
            raise FenceLost('lease changed')
    def sleep(seconds):
        nonlocal lost
        lost = True
    adapter = SimpleNamespace(observe=lambda: None, act=sent.append)
    brain = SimpleNamespace(decide=lambda obs: melee_actions())
    fence = AgentFence('hanjuku-hero', 'g1-abcdef', 1, '11111111-1111-1111-1111-111111111111')
    with patch.object(loop, 'read_canonical', return_value={}), \
         patch.object(loop, 'resolve_fence', return_value='run'), \
         patch.object(loop, '_check_loop_fence', side_effect=check), \
         patch.object(loop, 'shared_section', side_effect=lambda state, fn: fn()), \
         patch.object(loop.time, 'sleep', side_effect=sleep):
        with pytest.raises(FenceLost):
            loop._run_iteration(adapter, brain, 500, fence=fence, state_dir=tmp_path)
    assert len(sent) == 1


@pytest.mark.parametrize('low_latency', [False, True])
def test_capture_probing_opt_in_keeps_window_dimensions(tmp_path, low_latency):
    def capture(argv, **kwargs):
        Path(argv[-1]).write_bytes(b'captured')
    with patch('docich.xkit.procs.run', side_effect=capture) as run:
        result = XKit(':test').screenshot(tmp_path/'frame.png', 299, 224, window_id='42', low_latency=low_latency)
    argv = run.call_args.args[0]
    assert ('-probesize' in argv) == low_latency
    assert argv[argv.index('-video_size')+1] == '299x224'
    assert argv[argv.index('-window_id')+1] == '42'
    assert result.read_bytes() == b'captured'


@pytest.mark.parametrize('interval,error,expected_sleep', [(500, False, .3), (1500, False, 1.3),
                                                         (None, False, 1.3), (500, True, 1.3)])
def test_run_loop_applies_current_response_cadence_but_not_after_errors(interval, error, expected_sleep):
    game = SimpleNamespace(agent=SimpleNamespace(interval_ms=1500, brain='command'), name='hanjuku-hero')
    config = SimpleNamespace(agent=SimpleNamespace(default_interval_ms=1500), state_dir=ROOT/'run')
    brain = SimpleNamespace(observation_interval_ms=interval)
    with patch.object(loop, 'load_game', return_value=game), \
         patch.object(loop, 'make_adapter'), \
         patch.object(loop.brains, 'build_brain', return_value=brain), \
         patch.object(loop, '_run_iteration', side_effect=RuntimeError('capture failed') if error else None, return_value=0), \
         patch.object(loop.time, 'monotonic', side_effect=[10., 10.2]), \
         patch.object(loop.time, 'sleep', side_effect=KeyboardInterrupt) as sleep:
        with pytest.raises(KeyboardInterrupt):
            loop.run_agent(config, 'hanjuku-hero')
    assert sleep.call_args.args[0] == pytest.approx(expected_sleep)

"""The fade after a target jump we closed keeps the generic A back.

g1163-42bfb85e 2026-11-11 JST (chapter 9): the target jump for ハヤク ended
8 moves off (residual [16.5, -34]) and was closed with Y at 07:59:45. The next
frame was unreadable and the generic A landed on it (07:59:47). That decided
the sortie at the marker's stopping point: ビシソワーズ left the castle list,
the order became launched_unconfirmed, and from 08:00:06 the only chapter-9
route had no order to issue (``chart_interim_hold``) and no input was sent.
"""
from pathlib import Path
from unittest.mock import patch

from docich.hanjuku.orchestrator import Y_JUMP_TARGET_SETTLE_TICKS, decide, pad
from docich.hanjuku.os_layer import navigation
from docich.hanjuku.os_layer.pixels import read_png

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'hanjuku'
FADE = FIXTURES / 'g1075-world-map-survey-fade.png'


def _jump(mode, moves=navigation.Y_JUMP_MOVES):
    return {'goal': 'ハヤク', 'mode': mode, 'step': 'A:abcb659f:Mc1e894',
            'moves': moves, 'wait': 0, 'tick': 26480, 'seen': True}


def _residual_close(mode):
    mem = {'chapter': 9, 'tick': 26490, 'y_jump': _jump(mode)}
    goal = navigation.boss_probe_cell(mem, 'ハヤク', mem['y_jump']['step'])
    ox, oy = navigation.Y_JUMP_OFFSET[9]
    far = (goal[0] / 8 + ox - 16.5, goal[1] / 8 + oy + 34.0)
    with patch.object(navigation, 'world_cursor', return_value=far), \
            patch.object(navigation, 'world_flags', return_value={}):
        actions = navigation._y_jump_step(mem, object())
    return mem, actions


def test_a_residual_target_close_marks_the_fade():
    mem, actions = _residual_close('target')
    assert actions == [pad('y')]
    assert 'y_jump' not in mem
    assert mem['y_jump_target_closed'] == {'step': 'A:abcb659f:Mc1e894', 'tick': 26490}
    assert mem['y_jump_last']['why'] == 'residual'


def test_a_map_jump_close_leaves_the_default_alone():
    mem, actions = _residual_close('map')
    assert actions == [pad('y')]
    assert 'y_jump_target_closed' not in mem


def holds(state):
    return [r for r in state.get('_records', []) if r.get('decision') == 'y_jump_close_settle_hold']


def test_the_fade_after_the_close_holds_the_default_a_boundedly():
    state = {'screen_kind': 'world_map',
             'policy': {'chapter': 9, 'tick': 100,
                        'y_jump_target_closed': {'step': 'A:abcb659f:Mc1e894', 'tick': 100}}}
    fade = read_png(FADE)
    for after in range(1, Y_JUMP_TARGET_SETTLE_TICKS + 1):
        actions, state = decide(fade, state)
        assert pad('a') not in actions
        assert [h['observed_metric']['after_close'] for h in holds(state)] == [after]
    actions, state = decide(fade, state)
    assert actions == [pad('a')]
    assert not holds(state) and 'y_jump_target_closed' not in state['policy']

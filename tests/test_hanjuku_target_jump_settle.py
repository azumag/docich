"""The fade right after a target jump's confirm keeps the generic A back.

g1075-1d5f65e9 2026-10-30 JST: at 05:43:07 I:353377:1 confirmed its target
jump, the next frame was unreadable and the generic A landed on it (05:43:09);
the order saw the map again without having seen the target picker and was
recorded launched_unconfirmed, then re-read from its source list. Four of the
five interim sorties from 04:30 to 06:19 went that way. A sortie recorded as
launched (04:59:05, A:a84eda09:REC3) saw map_target and pressed A there.
"""
from pathlib import Path

from docich.hanjuku.orchestrator import Y_JUMP_TARGET_SETTLE_TICKS, decide, pad
from docich.hanjuku.os_layer.pixels import read_png

FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'hanjuku'
ON_AIM = FIXTURES / 'g1075-ch3-y-view-g-on-boss-aim.png'
FADE = FIXTURES / 'g1075-world-map-survey-fade.png'


def jump_state(mode):
    return {'screen_kind': 'world_map',
            'policy': {'chapter': 3, 'tick': 100,
                       'y_jump': {'goal': 'ボス', 'mode': mode, 'step': '3-B1',
                                  'moves': 3, 'wait': 0, 'tick': 99, 'seen': True}}}


def holds(state):
    return [r for r in state.get('_records', []) if r.get('decision') == 'y_jump_target_settle_hold']


def test_the_fade_after_a_target_confirm_holds_the_default_a_boundedly():
    actions, state = decide(read_png(ON_AIM), jump_state('target'))
    assert actions == [pad('a')]
    assert state['policy']['y_jump_target_confirmed']['step'] == '3-B1'
    fade = read_png(FADE)
    for after in range(1, Y_JUMP_TARGET_SETTLE_TICKS + 1):
        actions, state = decide(fade, state)
        assert pad('a') not in actions
        assert [h['observed_metric']['after_confirm'] for h in holds(state)] == [after]
    actions, state = decide(fade, state)
    assert actions == [pad('a')]
    assert not holds(state) and 'y_jump_target_confirmed' not in state['policy']


def test_a_map_jump_confirm_keeps_the_default_on_the_fade():
    actions, state = decide(read_png(ON_AIM), jump_state('map'))
    assert actions == [pad('a')]
    assert 'y_jump_target_confirmed' not in state['policy']
    actions, state = decide(read_png(FADE), state)
    assert not holds(state)

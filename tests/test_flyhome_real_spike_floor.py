"""Static controller decisions from actual stage 49 records; no motion simulation."""
import json
import pytest
from pathlib import Path
from docich.flyhome.mpc import MpcConfig, MpcController
from docich.flyhome.control import Physics

FIXTURES = Path(__file__).parent / 'fixtures'

def test_recorded_upper_coast_brakes_before_feet_reach_a_spike_when_enabled():
    r = json.loads((FIXTURES / 'flyhome-level49-upper-foot-brake.json').read_text())
    actions = []
    for enabled in (False, True):
        ctl = MpcController(Physics(**r['physics']), cfg=MpcConfig(spike_floor_guard=enabled))
        ctl.hazards = r['hazards']
        ctl.path = r['path']
        ctl._hist.extend(tuple(q) for q in r['history'])
        ctl._acts.extend(tuple(q) for q in r['acts'])
        # Recorded reference velocity follows from the recorded want_vy and position error.
        want_vy = r['precision'][3]
        rvy = want_vy - .8 * (r['ref0'][1] - r['y'])
        ctl._ref = [(r['ref0'][0], r['ref0'][1], 4, rvy)] * ctl.cfg.horizon
        a = ctl._precision_action(r['x'], r['y'], r['precision'][0], r['precision'][1], r['angle'], r['estimated'][-1])
        actions.append(a)
        for index in (*range(7), 9, 10, 11):
            assert ctl._precision_debug[index] == pytest.approx(r['precision'][index], abs=.004)
        if enabled:
            assert ctl._precision_debug[-1] == r['raw_foot_limit'] - 1
    assert actions == [0, 1]

def test_a_spike_above_the_player_does_not_become_a_falling_boundary():
    ctl = MpcController(cfg=MpcConfig(spike_floor_guard=True))
    ctl.hazards = [(131, 39, 141, 49)]
    ctl._hist.append((10, 135, 65, 0))
    ctl._ref = [(135, 85, 0, 12)] * ctl.cfg.horizon
    ctl._precision_action(135, 65, 0, 8, 0, 0)
    assert ctl._precision_debug[-1] == 160


def test_recorded_fast_ascent_does_not_retrigger_falling_brake_at_current_boundary():
    r = json.loads((FIXTURES / 'flyhome-level49-rising-foot-boundary.json').read_text())
    assert r['left'] and r['right']  # Previously sent both jets despite the fast measured ascent.
    ctl = MpcController(Physics(**r['physics']), cfg=MpcConfig(spike_floor_guard=True))
    ctl.hazards = r['hazards']
    ctl.path = r['path']
    ctl._hist.extend(tuple(q) for q in r['history'])
    ctl._acts.extend(tuple(q) for q in r['acts'])
    rvy = r['precision'][3] - .8 * (r['ref0'][1] - r['y'])
    ctl._ref = [(r['ref0'][0], r['ref0'][1], 4, rvy)] * ctl.cfg.horizon
    action = ctl._precision_action(r['x'], r['y'], r['precision'][0], r['precision'][1], r['angle'], r['estimated'][-1])
    for index in (*range(7), 9, 10, 11, 12):
        assert ctl._precision_debug[index] == pytest.approx(r['precision'][index], abs=.004)
    assert action == 0

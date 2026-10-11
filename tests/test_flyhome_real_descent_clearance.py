"""Static control decision from actual stage 46 observations; no motion simulator."""
import json
from pathlib import Path
from docich.flyhome import vision
from docich.flyhome.image import read_png
from docich.flyhome.mpc import MpcController, MpcConfig

FIXTURES = Path(__file__).parent / 'fixtures'

def test_actual_level46_side_clearance_can_relax_descent_wait_locally():
    r = json.loads((FIXTURES / 'flyhome-level46-descent-wait.json').read_text())
    obs = vision.analyze(read_png(FIXTURES / r['hazards_source']))
    x, y, ang = r['x'], r['y'], r['angle']
    vx, vy = r['precision'][:2]
    om = r['estimated'][-1]
    results = []
    for clearance in (24, 16):
        ctl = MpcController(cfg=MpcConfig(descent_wide_clearance=clearance))
        ctl.hazards = obs.hazards
        ctl.path = r['path']
        ctl._hist.append((r['t'], x, y, ang))
        ctl._ref = [(r['ref0'][0], r['ref0'][1], 0, 12)] * ctl.cfg.horizon
        # A completed hold interval alone must not bypass the stricter angular-speed condition.
        ctl._descent_ready_since = r['t'] - .13
        ctl._precision_action(x, y, vx, vy, ang, om)
        results.append(ctl._precision_debug[7])
    assert results == [True, False]

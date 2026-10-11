"""Static control checks from actual Windows stage 39 observations (no movement simulation)."""
import json
from pathlib import Path
from docich.flyhome.control import Physics
from docich.flyhome.mpc import MpcConfig, MpcController


def test_actual_upper_passage_can_brake_tilt_below_the_usual_ceiling_boundary():
    data = json.loads((Path(__file__).parent / 'fixtures' / 'flyhome-level39-upper-passage.json').read_text())
    r = data['state']
    actions = []
    for ceiling in (24, 16):
        ctl = MpcController(Physics(**data['physics']), MpcConfig(ceiling_y=ceiling))
        ctl.path = r['path']
        hist = data['frames']
        ctl._hist.extend((v['t'], v['x'], v['y'], v['angle']) for v in hist)
        ctl._acts.extend((v['input_sent_at'], int(v['left']) + 2*int(v['right']))
                         for v in hist[:-1] if v.get('input_sent_at'))
        rx, ry = r['ref0']
        rvx = r['precision'][2] - .8*(rx-r['x'])
        rvy = r['precision'][3] - .8*(ry-r['y'])
        ctl._ref = [(rx, ry, rvx, rvy)] * ctl.cfg.horizon
        actions.append(ctl._precision_action(r['x'], r['y'], *r['estimated']))
    assert MpcConfig().ceiling_y == 24  # Other levels retain the verified ordinary boundary.
    assert actions == [0, 2]  # At +.772rad, the lower boundary allows the opposing right jet.

"""Recorded actual level 44: house facade and an airborne home wait."""
import json
from pathlib import Path
from docich.flyhome import vision, planner
from docich.flyhome.image import read_png
from docich.flyhome.mpc import MpcController, MpcConfig
from docich.flyhome.tracker import State, PLAYING

FIXTURES = Path(__file__).parent / 'fixtures'

def test_actual_house_facade_is_not_a_soil_wall():
    im = read_png(FIXTURES / 'flyhome-level44-initial.png')
    obs = vision.analyze(im)
    assert im.get(248, 145) == (61, 29, 14)
    assert not obs.solid[145 * 320 + 248]
    assert not obs.solid[147 * 320 + 248]  # The brown bottom trim is also part of the facade.
    assert obs.solid[170 * 320 + 48]   # The actual starting platform remains solid.

def test_recorded_level44_descent_does_not_latch_home_wait():
    rows = json.loads((FIXTURES / 'flyhome-level44-false-home-contact.json').read_text())['observations']
    row = rows[-1]
    obs = vision.analyze(read_png(FIXTURES / 'flyhome-level44-initial.png'))
    ctl = MpcController(cfg=MpcConfig(takeoff_s=0))
    ctl._attempt = 1
    ctl.set_world(planner.build_grid(obs), obs, row['path'])
    ctl._hist.extend((r['t'], r['x'], r['y'], r['angle']) for r in rows)
    ctl._estimate = lambda st: (st.x, st.y, *row['estimated'])
    st = State(t=row['t'], phase=PLAYING, attempt=1, x=row['x'], y=row['y'], angle=row['angle'], home=tuple(row['home']), home_bbox=tuple(row['home_bbox']))
    ctl.decide(st, st.home)
    assert not ctl._home_settling  # The recorded player was moving vertically, even though near a floor-colored edge.

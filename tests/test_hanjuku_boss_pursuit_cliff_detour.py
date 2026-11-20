"""A cliff-pocket unit is re-aimed at the north corridor first (#4861).

Live g1190 46-9..46-12: every ``boss_pursuit_redirected`` confirmed, yet the
generals camping in the pocket north-east of イデ！ (キール [564,368],
バゲット [588,516]) never moved a pixel - the black cliff line between the
west pocket and the east boss-orbit area stops a straight march, so the
pursuit now walks a measured west-side unit to the corridor in two stages.
"""
from types import SimpleNamespace

from docich import hanjuku_chart as chart
from docich.hanjuku.flows import boss_pursuit as bp
from docich.hanjuku.os_layer import navigation as nav

POCKET = [564, 368]        # キール's pocket, north-east of イデ！
CORRIDOR = [568, 212]      # on the north corridor itself
EAST = [674, 400]          # already inside the boss-orbit area
AIM_EAST = (810, 645)      # a boss-orbit cell, south-east
AIM_NE = (700, 220)        # east of the line but inside the corridor band
MOUTH = (674, 212)         # the east area's north entrance


def memory(**extra):
    mem = {
        'chapter': 9,
        'tick': 10000,
        'world': {'sorties': {}, 'garrison': {}},
        bp.LIST_KEY: {'ミント': {'step': 'A', 'launched': 9500}},
        'boss_cell': {'chapter': 9, 'cell': [810, 645], 'tick': 9990},
        'plan': {'active': None},
    }
    mem.update(extra)
    return mem


def test_a_pocket_unit_walks_to_the_north_waypoint_first():
    assert bp._ch9_cliff_detour(POCKET, AIM_EAST) == chart.CH9_BOSS_WAYPOINT
    assert bp._ch9_cliff_detour([588, 516], AIM_EAST) == chart.CH9_BOSS_WAYPOINT


def test_a_corridor_unit_walks_due_east_to_the_mouth():
    assert bp._ch9_cliff_detour(CORRIDOR, AIM_EAST) == MOUTH


def test_a_corridor_aim_inside_the_band_needs_no_detour():
    assert bp._ch9_cliff_detour(CORRIDOR, AIM_NE) is None


def test_an_east_or_unmeasured_unit_keeps_the_aim():
    assert bp._ch9_cliff_detour(EAST, AIM_EAST) is None
    assert bp._ch9_cliff_detour(None, AIM_EAST) is None
    assert bp._ch9_cliff_detour(POCKET, (400, 500)) is None  # aim is also west
    assert bp._ch9_cliff_detour(POCKET, None) is None


def _probe(**extra):
    probe = {'general': 'ミント', 'step': 'A', 'phase': 'dest',
             'reads': 5, 'seeks': 0, 'x_retries': 0,
             'boss_cell0': {'cell': [810, 645], 'tick': 9990}}
    probe.update(extra)
    return probe


def _orbit_track():
    # Three-plus distinct cells with real extent so _ambush arms an aim.
    return [{'cell': [700, 300]}, {'cell': [800, 500]},
            {'cell': [900, 700]}, {'cell': [750, 850]}]


def _dest_step(mem, monkeypatch):
    monkeypatch.setattr(bp.chart, 'moving_boss_cell', lambda ch: (810, 645))
    import docich.hanjuku.flows.map as m
    monkeypatch.setattr(m, '_target_roof_under_marker', lambda *a, **k: False)
    monkeypatch.setattr(nav, '_start_y_jump', lambda *a, **k: ['jump'])
    picker = SimpleNamespace(kind='map_target', marker=(132, 113), cursor=None,
                             text='', hand=None, selected=None)
    assert bp.step(picker, mem, object()) == ['jump']


def test_the_dest_phase_detours_the_ambush_aim_for_a_pocket_unit(monkeypatch):
    mem = memory(boss_pursuit=_probe(unit_world=list(POCKET)),
                 boss_cell_track=_orbit_track())
    _dest_step(mem, monkeypatch)
    assert mem['pursuit_aim'] == list(chart.CH9_BOSS_WAYPOINT)
    assert mem['boss_pursuit']['aim_cell'] == list(chart.CH9_BOSS_WAYPOINT)
    assert 'pursuit_lead' not in mem
    assert nav.boss_probe_cell(mem, 'ボス', 'PURSUIT') == chart.CH9_BOSS_WAYPOINT


def test_the_dest_phase_detours_a_corridor_unit_to_the_east_mouth(monkeypatch):
    mem = memory(boss_pursuit=_probe(unit_world=list(CORRIDOR)),
                 boss_cell_track=_orbit_track())
    _dest_step(mem, monkeypatch)
    assert mem['pursuit_aim'] == list(MOUTH)
    assert 'pursuit_lead' not in mem


def test_an_east_unit_keeps_the_ambush_aim(monkeypatch):
    mem = memory(boss_pursuit=_probe(unit_world=list(EAST)),
                 boss_cell_track=_orbit_track())
    _dest_step(mem, monkeypatch)
    assert mem['pursuit_aim'] == [700, 300]


def test_the_lead_aim_also_detours_for_a_pocket_unit(monkeypatch):
    # No boss_cell_track -> _ambush is None; a displacement arms a lead.
    mem = memory(boss_pursuit=_probe(unit_world=list(POCKET),
                                     boss_cell0={'cell': [1000, 1000], 'tick': 100}),
                 boss_cell={'cell': [1100, 940], 'tick': 200})
    _dest_step(mem, monkeypatch)
    assert mem['pursuit_aim'] == list(chart.CH9_BOSS_WAYPOINT)
    assert 'pursuit_lead' not in mem


def test_an_unmeasured_unit_keeps_the_lead(monkeypatch):
    mem = memory(boss_pursuit=_probe(boss_cell0={'cell': [1000, 1000], 'tick': 100}),
                 boss_cell={'cell': [1100, 940], 'tick': 200})
    _dest_step(mem, monkeypatch)
    assert 'pursuit_aim' not in mem
    assert mem['pursuit_lead'] == [150, -90]

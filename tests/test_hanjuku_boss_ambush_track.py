"""Place a boss-wave general on the measured orbit instead of chasing it.

Owner 2026-11-20: the moving boss looks random but runs a fixed track, so a
general standing on that track meets it as it comes round; chasing the current
cell only follows it. The Y view keeps the measured track and the pursuit aims
its Y jump at the tracked cell farthest from the current reading.
"""
from types import SimpleNamespace

from docich import hanjuku_chart as chart
from docich.hanjuku.flows import boss_pursuit as bp
from docich.hanjuku.os_layer import navigation as nav

TRACK = [{'cell': [800, 180]}, {'cell': [900, 420]}, {'cell': [820, 640]},
         {'cell': [700, 500]}]


def memory(**extra):
    mem = {
        'chapter': 9,
        'tick': 10000,
        'world': {'sorties': {}, 'garrison': {}},
        bp.LIST_KEY: {'ミント': {'step': 'A', 'launched': 9500}},
        'boss_cell': {'chapter': 9, 'cell': [900, 420], 'tick': 9990, 'readings': 2},
        'boss_cell_track': [dict(e) for e in TRACK],
        'plan': {'active': None},
    }
    mem.update(extra)
    return mem


def test_the_ambush_is_the_farthest_tracked_cell():
    # Farthest from (900,420): (820,640) distance 300, (800,180) 340 -> far
    # in Manhattan terms: (800,180)=340, (820,640)=300, (700,500)=280.
    assert bp._ambush(memory()) == (800, 180)


def test_a_short_or_thin_track_does_not_ambush():
    assert bp._ambush(memory(boss_cell_track=[])) is None
    assert bp._ambush(memory(boss_cell_track=[{'cell': [800, 180]},
                                              {'cell': [900, 420]}])) is None
    near = [{'cell': [900, 420]}, {'cell': [920, 430]}, {'cell': [880, 410]}]
    assert bp._ambush(memory(boss_cell_track=near)) is None


def test_the_aim_overrides_the_aim_cell_for_the_pursuit_only():
    chart.set_boss_cell(9, (800, 600))
    try:
        mem = memory(pursuit_aim=[810, 180], pursuit_lead=[100, -50])
        assert nav.boss_probe_cell(mem, 'ボス', 'PURSUIT') == (810, 180)
        assert nav.boss_probe_cell(mem, 'ボス', 'A') == chart.CH9_BOSS_WAYPOINT
    finally:
        chart.set_boss_cell(9, None)


def test_the_dest_phase_prefers_the_track_over_the_lead(monkeypatch):
    mem = memory(boss_pursuit={'general': 'ミント', 'step': 'A', 'phase': 'dest',
                               'reads': 5, 'seeks': 0, 'x_retries': 0,
                               'boss_cell0': {'cell': [700, 500], 'tick': 9000}})
    monkeypatch.setattr(bp.chart, 'moving_boss_cell', lambda ch: (900, 420))
    import docich.hanjuku.flows.map as m
    monkeypatch.setattr(m, '_target_roof_under_marker', lambda *a, **k: False)
    monkeypatch.setattr(nav, '_start_y_jump', lambda *a, **k: ['jump'])
    picker = SimpleNamespace(kind='map_target', marker=(132, 113), cursor=None,
                             text='', hand=None, selected=None)
    assert bp.step(picker, mem, object()) == ['jump']
    # ミント is the only wave general -> index 0 -> the first sorted track cell
    assert mem['pursuit_aim'] == [700, 500]
    assert 'pursuit_lead' not in mem


def test_generals_are_spread_over_the_track():
    mem = memory()
    mem[bp.LIST_KEY] = {'ミント': {'step': 'A', 'launched': 9500},
                        'カシス': {'step': 'B', 'launched': 9500},
                        'ギー': {'step': 'C', 'launched': 9500}}
    points = {bp._ambush(mem, g) for g in ('カシス', 'ギー', 'ミント')}
    assert len(points) == 3


def test_an_ambush_placement_keeps_the_general_waiting(monkeypatch):
    mem = memory()
    entry = mem[bp.LIST_KEY]['ミント']
    entry['ambush_until'] = 10000 + bp.AMBUSH_WAIT_TICKS
    monkeypatch.setattr(bp.chart, 'moving_boss_cell', lambda ch: (900, 420))
    assert bp.step(SimpleNamespace(kind='map', marker=None, cursor=None, text='',
                                   hand=None, selected=None), mem, None) is None
    entry.pop('ambush_until', None)
    assert bp.step(SimpleNamespace(kind='map', marker=None, cursor=None, text='',
                                   hand=None, selected=None), mem, None) == []

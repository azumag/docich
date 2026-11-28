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
    assert bp._ambush(memory()) == (800 + bp.CH9_AMBUSH_X_OFFSET, 180)


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
    assert mem['pursuit_aim'] == [700 + bp.CH9_AMBUSH_X_OFFSET, 500]
    assert 'pursuit_lead' not in mem


def test_generals_are_spread_over_the_track():
    mem = memory()
    mem[bp.LIST_KEY] = {'ミント': {'step': 'A', 'launched': 9500},
                        'カシス': {'step': 'B', 'launched': 9500},
                        'ギー': {'step': 'C', 'launched': 9500}}
    points = {bp._ambush(mem, g) for g in ('カシス', 'ギー', 'ミント')}
    assert len(points) == 3


def test_a_placement_no_longer_parks_the_general(monkeypatch):
    # Owner 2026-11-21: 波状攻撃でも削れるので将軍待ちはしない. A placement
    # must not block the next wave: the pursuit re-aims on its normal cooldown.
    mem = memory()
    entry = mem[bp.LIST_KEY]['ミント']
    entry['ambush_until'] = 10000 + bp.AMBUSH_WAIT_TICKS
    monkeypatch.setattr(bp.chart, 'moving_boss_cell', lambda ch: (900, 420))
    assert bp.step(SimpleNamespace(kind='map', marker=None, cursor=None, text='',
                                   hand=None, selected=None), mem, None) == []


def test_a_boss_reading_on_the_ambush_cell_is_recorded_once():
    mem = memory()
    mem[bp.LIST_KEY]['ミント']['ambush_cell'] = [900, 420]
    mem['boss_cell'] = {'cell': [908, 420], 'tick': 10000}
    bp.ambush_contact(mem)
    hits = [r for r in mem.get('_records', []) if r['decision'] == 'boss_ambush_contact']
    assert len(hits) == 1
    assert hits[0]['general'] == 'ミント'
    bp.ambush_contact(mem)
    assert len([r for r in mem.get('_records', [])
                if r['decision'] == 'boss_ambush_contact']) == 1


def test_a_far_reading_is_not_a_contact():
    mem = memory()
    mem[bp.LIST_KEY]['ミント']['ambush_cell'] = [900, 420]
    mem['boss_cell'] = {'cell': [700, 300], 'tick': 10000}
    bp.ambush_contact(mem)
    assert not [r for r in mem.get('_records', [])
                if r['decision'] == 'boss_ambush_contact']


def test_the_redirect_keeps_the_ambush_cell_and_logs_the_aim(monkeypatch):
    mem = memory(boss_pursuit={'general': 'ミント', 'step': 'A', 'phase': 'dest',
                               'reads': 5, 'seeks': 0, 'x_retries': 0},
                 pursuit_aim=[800, 180])
    monkeypatch.setattr(bp.chart, 'moving_boss_cell', lambda ch: (900, 420))
    import docich.hanjuku.flows.map as m
    monkeypatch.setattr(m, '_target_roof_under_marker', lambda *a, **k: True)
    picker = SimpleNamespace(kind='map_target', marker=(132, 113), cursor=None,
                             text='', hand=None, selected=None)
    assert bp.step(picker, mem, object()) == [bp.pad('a')]
    entry = mem[bp.LIST_KEY]['ミント']
    assert entry['ambush_cell'] == [800, 180]
    assert entry['ambush_until'] == 10000 + bp.AMBUSH_WAIT_TICKS
    record = [r for r in mem.get('_records', [])
              if r['decision'] == 'boss_pursuit_redirected'][-1]
    assert record['observed_metric']['aim'] == [800, 180]


def test_a_contact_records_where_the_general_actually_is():
    mem = memory()
    entry = mem[bp.LIST_KEY]['ミント']
    entry['ambush_cell'] = [900, 420]
    entry['unit_world'] = [880, 400]
    mem['boss_cell'] = {'cell': [908, 420], 'tick': 10000}
    bp.ambush_contact(mem)
    rec = [r for r in mem.get('_records', [])
           if r['decision'] == 'boss_ambush_contact'][-1]
    assert rec['observed_metric']['unit_world'] == [880, 400]
    assert rec['observed_metric']['unit_dist'] == [28, 20]


def test_a_true_touch_requires_the_general_and_boss_on_the_same_cell():
    # Boss on the aim but the general far away: aim-reached only.
    mem = memory()
    mem[bp.LIST_KEY]['ミント']['ambush_cell'] = [900, 420]
    mem[bp.LIST_KEY]['ミント']['unit_world'] = [700, 300]
    mem['boss_cell'] = {'cell': [902, 421], 'tick': 10000}
    bp.ambush_contact(mem)
    kinds = [r['decision'] for r in mem.get('_records', [])]
    assert 'boss_ambush_contact' in kinds
    assert 'boss_unit_contact' not in kinds
    # Boss on the general's measured cell: a real touch.
    mem[bp.LIST_KEY]['ミント']['unit_world'] = [900, 420]
    mem['boss_cell'] = {'cell': [902, 421], 'tick': 11000}
    bp.ambush_contact(mem)
    kinds = [r['decision'] for r in mem.get('_records', [])]
    assert 'boss_unit_contact' in kinds

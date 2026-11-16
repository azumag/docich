"""The boss pursuit commits a redirect only while the boss is in the north.

ch9 2026-11-17 (local driver, v988/v989): ~75 redirects over two seedless runs,
aimed at whatever the Y view showed, never produced an attack; every boss
battle so far followed a reading in the northern band (y <= 229). The boss
sweeps y 157 <-> 853 in ~330 s while a camped general walks ~1.3 px/s.
"""
from types import SimpleNamespace

from docich.hanjuku.flows import boss_pursuit as bp


class FakeFrame:
    pass


def picker():
    return SimpleNamespace(kind='map_target', hand=None, selected=None, text='',
                           marker=(136, 118))


def memory(cell, read_tick, tick=5000):
    return {'chapter': 9, 'tick': tick,
            bp.LIST_KEY: {'ミント': {'step': 'A', 'launched': 3000}},
            'boss_cell': {'chapter': 9, 'cell': list(cell), 'tick': read_tick, 'readings': 2},
            'boss_pursuit': {'general': 'ミント', 'step': 'A', 'phase': 'dest',
                             'reads': 10, 'seeks': 3, 'x_retries': 0}}


def arm(monkeypatch, roof=True):
    monkeypatch.setattr(bp.chart, 'moving_boss_cell', lambda ch: (1, 2))
    import docich.hanjuku.flows.map as m
    monkeypatch.setattr(m, '_target_roof_under_marker', lambda *a, **k: roof)


def test_a_southern_reading_defers_and_cancels_the_picker(monkeypatch):
    arm(monkeypatch)
    mem = memory((850, 853), read_tick=4990)
    assert bp.step(picker(), mem, FakeFrame()) == [bp.pad('b')]
    assert mem['boss_pursuit']['outcome'] == 'boss_not_north'
    assert mem['boss_pursuit']['phase'] == 'close'
    records = [r for r in mem.get('_records', []) if r['decision'] == 'boss_pursuit_deferred']
    assert records and records[0]['observed_metric']['boss_cell'] == [850, 853]
    # the same general is looked at again after the short retry, not 240 ticks
    entry = mem[bp.LIST_KEY]['ミント']
    assert mem['tick'] - entry['pursuit_tick'] == bp.PURSUIT_COOLDOWN - bp.PURSUIT_NORTH_RETRY
    assert bp._candidate({**mem, 'tick': mem['tick'] + bp.PURSUIT_NORTH_RETRY}) is not None
    assert bp._candidate({**mem, 'tick': mem['tick'] + bp.PURSUIT_NORTH_RETRY - 1}) is None


def test_a_northern_reading_redirects(monkeypatch):
    arm(monkeypatch)
    mem = memory((690, 181), read_tick=4990)
    assert bp.step(picker(), mem, FakeFrame()) == [bp.pad('a')]
    assert mem['boss_pursuit']['outcome'] == 'redirected'
    assert mem[bp.LIST_KEY]['ミント']['pursuit_tick'] == 5000


def test_the_band_edge_is_inclusive(monkeypatch):
    arm(monkeypatch)
    mem = memory((674, bp.PURSUIT_NORTH_MAX_Y), read_tick=4990)
    assert bp.step(picker(), mem, FakeFrame()) == [bp.pad('a')]


def test_an_old_reading_is_not_judged_here(monkeypatch):
    # A reading that this Y view did not take falls through to the usual
    # jump-and-verify path, which measures a fresh one first.
    arm(monkeypatch, roof=False)
    mem = memory((850, 853), read_tick=4000)
    import docich.hanjuku.os_layer.navigation as nav
    monkeypatch.setattr(nav, '_start_y_jump', lambda *a, **k: ['jump'])
    assert bp.step(picker(), mem, FakeFrame()) == ['jump']
    assert mem['boss_pursuit']['phase'] == 'dest'


def test_the_pursuit_reads_the_skull_on_its_own_y_view(monkeypatch):
    # The jump the dest phase runs is its own: the northern-band check needs
    # the skull of this very view, not the reading of an earlier one.
    mem = memory((850, 853), read_tick=1000)
    mem['boss_pursuit']['reads'] = 10
    mem['y_jump'] = {'goal': 'ボス', 'mode': 'target', 'step': bp.PURSUIT_STEP,
                     'moves': 0, 'wait': 0}
    seen = []
    import docich.hanjuku.os_layer.navigation as nav
    import docich.hanjuku.os_layer.world as world
    monkeypatch.setattr(world, '_apply_skull_boss', lambda m, f: seen.append(f))
    monkeypatch.setattr(nav, '_y_jump_step', lambda m, f: ['jump-step'])
    frame = FakeFrame()
    on_world_map = SimpleNamespace(kind='world_map', hand=None, selected=None, text='')
    assert bp.step(on_world_map, mem, frame) == ['jump-step']
    assert seen == [frame]

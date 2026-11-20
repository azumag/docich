"""The boss pursuit keeps its own Y jump and never strands the screen.

ch9 2026-11-17 (local driver, v985): the dest phase pressed y once and then b
51 times, undoing its own jump before _y_jump_step ran once; the probe then
sat in close on the world map (reads 95 -> 2168) because close returned an
empty answer there, which kept the map flow from closing the view.
"""
from types import SimpleNamespace

from docich.hanjuku.flows import boss_pursuit as bp


class FakeFrame:
    pass


def memory(phase, **extra):
    mem = {'chapter': 9, 'tick': 5000,
           bp.LIST_KEY: {'ミント': {'step': 'A', 'launched': 3000}},
           'boss_pursuit': {'general': 'ミント', 'step': 'A', 'phase': phase,
                            'reads': 10, 'seeks': 3, 'x_retries': 0}}
    mem.update(extra)
    return mem


def world_map():
    return SimpleNamespace(kind='world_map', hand=None, selected=None, text='')


def test_dest_on_world_map_continues_its_own_y_jump(monkeypatch):
    mem = memory('dest', y_jump={'goal': 'ボス', 'mode': 'target', 'step': bp.PURSUIT_STEP,
                                 'moves': 0, 'wait': 0})
    calls = []
    import docich.hanjuku.os_layer.navigation as nav
    monkeypatch.setattr(nav, '_y_jump_step', lambda m, f: calls.append(f) or ['jump-step'])
    frame = FakeFrame()
    assert bp.step(world_map(), mem, frame) == ['jump-step']
    assert calls == [frame]
    assert mem['boss_pursuit']['phase'] == 'dest'


def test_dest_on_world_map_without_its_jump_still_closes_with_b():
    mem = memory('dest')
    actions = bp.step(world_map(), mem, FakeFrame())
    assert actions == [bp.pad('b')]


def test_dest_on_world_map_without_a_frame_waits():
    mem = memory('dest', y_jump={'goal': 'ボス', 'mode': 'target', 'step': bp.PURSUIT_STEP,
                                 'moves': 0, 'wait': 0})
    assert bp.step(world_map(), mem, None) == []


def test_close_on_world_map_drops_probe_and_stale_jump():
    mem = memory('close', y_jump={'goal': 'ボス', 'mode': 'target', 'step': bp.PURSUIT_STEP,
                                  'moves': 0, 'wait': 0},
                 y_jumped={bp.PURSUIT_STEP: 'ボス'}, y_jumped_res={bp.PURSUIT_STEP: [0, 0]},
                 y_jump_return='target')
    mem['boss_pursuit']['reads'] = 400
    assert bp.step(world_map(), mem, FakeFrame()) is None
    assert 'boss_pursuit' not in mem
    assert 'y_jump' not in mem and 'y_jump_return' not in mem
    assert bp.PURSUIT_STEP not in mem['y_jumped']


def test_close_clears_only_its_own_near_goal():
    mem = memory('close', near_goal={'step': bp.PURSUIT_STEP, 'goal': 'ボス', 'mode': 'target'})
    bp.step(world_map(), mem, FakeFrame())
    assert 'near_goal' not in mem
    other = {'step': 'A:other', 'goal': 'X', 'mode': 'target'}
    mem = memory('close', near_goal=other)
    bp.step(world_map(), mem, FakeFrame())
    assert mem['near_goal'] is other


def test_close_keeps_another_orders_y_jump():
    other = {'goal': 'X', 'mode': 'target', 'step': 'A:other', 'moves': 0, 'wait': 0}
    mem = memory('close', y_jump=other)
    assert bp.step(world_map(), mem, FakeFrame()) is None
    assert mem['y_jump'] is other


def test_close_gives_up_after_the_limit_on_any_screen():
    mem = memory('close')
    unreadable = SimpleNamespace(kind='text', hand=None, selected=None, text='')
    seen = [bp.step(unreadable, mem, None) for _ in range(bp.CLOSE_LIMIT)]
    assert 'boss_pursuit' in mem
    assert bp.step(unreadable, mem, None) is None
    assert 'boss_pursuit' not in mem


def test_read_limit_is_recorded_once_while_closing():
    mem = memory('close')
    mem['boss_pursuit']['reads'] = bp.PURSUIT_READ_LIMIT + 50
    mem['boss_pursuit']['outcome'] = 'read_limit'
    records = []
    import docich.hanjuku.flows.boss_pursuit as module
    original = module._record
    module._record = lambda *a, **k: records.append(k.get('resulting_event'))
    try:
        unreadable = SimpleNamespace(kind='text', hand=None, selected=None, text='')
        for _ in range(3):
            bp.step(unreadable, mem, None)
    finally:
        module._record = original
    assert records == []


def test_new_pursuit_resets_the_spent_jump_budget(monkeypatch):
    # a fresh wave: the stale-wave gate (v1258) stops pursuit of old entries
    mem = {'chapter': 9, 'tick': 5000, 'y_jumps': {'PURSUIT:target': 3},
           bp.LIST_KEY: {'ミント': {'step': 'A', 'launched': 4500}}}
    monkeypatch.setattr(bp.chart, 'moving_boss_cell', lambda ch: (1, 2))
    monkeypatch.setattr(bp, '_plan_region', lambda m: {})
    screen = SimpleNamespace(kind='map', hand=None, selected=None, text='')
    bp.step(screen, mem, None)
    assert mem['boss_pursuit']['general'] == 'ミント'
    assert 'PURSUIT:target' not in mem['y_jumps']

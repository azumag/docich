"""Repeated roster failures hand the field to the visible-camp recall.

Live g1288 2026-11-20 17:1x-17:2x: every pursuit of the fresh wave ended
`roster_unreadable` (an event dialogue showed where the roster should be), while
the camp recall was told to stand down for the "flying" waves. Nothing reached a
general. After two failures the pursuit suspends itself for a bounded window and
`wave_active` turns false, so the house/camp owners get the field back.
"""
from types import SimpleNamespace

from docich.hanjuku.flows import boss_pursuit as bp


def map_screen():
    return SimpleNamespace(kind='map', marker=None, cursor=None, text='',
                           hand=None, selected=None)


def memory(tick=10000, **extra):
    mem = {
        'chapter': 9,
        'tick': tick,
        'world': {'sorties': {}, 'garrison': {'イデ！': ['どうし', 'ゼウス']}},
        bp.LIST_KEY: {'ミント': {'step': 'A', 'launched': tick - 500}},
        'boss_cell': {'chapter': 9, 'cell': [810, 645], 'tick': tick - 10,
                      'readings': 2},
        'plan': {'active': None},
    }
    mem.update(extra)
    return mem


def probe():
    return {'general': 'ミント', 'step': 'A', 'phase': 'open',
            'reads': 1, 'seeks': 0, 'x_retries': 0}


def test_two_roster_failures_suspend_the_pursuit(monkeypatch):
    mem = memory()
    bp._close(mem, probe(), 'roster_unreadable')
    assert 'boss_pursuit_suspended' not in mem
    bp._close(mem, probe(), 'roster_unreadable')
    assert mem['boss_pursuit_suspended'] == 10000 + bp.PURSUIT_SUSPEND_TICKS
    assert mem['boss_pursuit_fail_streak'] == 0
    records = [r for r in mem.get('_records', [])
               if r['decision'] == 'boss_pursuit_suspended']
    assert len(records) == 1
    # Suspended: no pursuit start and the deferrals no longer see a flying wave.
    monkeypatch.setattr(bp.chart, 'moving_boss_cell', lambda ch: (810, 645))
    assert bp.step(map_screen(), mem, None) is None
    assert bp.wave_active(mem) is False


def test_the_pursuit_resumes_after_the_window(monkeypatch):
    mem = memory()
    bp._close(mem, probe(), 'roster_unreadable')
    bp._close(mem, probe(), 'roster_unreadable')
    mem['tick'] = mem['boss_pursuit_suspended']
    monkeypatch.setattr(bp.chart, 'moving_boss_cell', lambda ch: (810, 645))
    assert bp.wave_active(mem) is True
    assert bp.step(map_screen(), mem, None) == []
    assert mem['boss_pursuit']['general'] == 'ミント'


def test_a_successful_redirect_clears_the_failure_state(monkeypatch):
    mem = memory()
    bp._close(mem, probe(), 'roster_unreadable')
    mem['boss_pursuit_suspended'] = 10000 + bp.PURSUIT_SUSPEND_TICKS
    mem['boss_pursuit_fail_streak'] = 1
    monkeypatch.setattr(bp.chart, 'moving_boss_cell', lambda ch: (810, 645))
    import docich.hanjuku.flows.map as m
    monkeypatch.setattr(m, '_target_roof_under_marker', lambda *a, **k: True)
    mem['boss_pursuit'] = {'general': 'ミント', 'step': 'A', 'phase': 'dest',
                           'reads': 1, 'seeks': 0, 'x_retries': 0}
    mem['tick'] = 10000 + bp.PURSUIT_SUSPEND_TICKS
    picker = SimpleNamespace(kind='map_target', marker=(132, 113), cursor=None,
                             text='', hand=None, selected=None)
    assert bp.step(picker, mem, object()) == [bp.pad('a')]
    assert mem['boss_pursuit_fail_streak'] == 0
    assert mem['boss_pursuit_suspended'] is None

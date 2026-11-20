"""A fresh boss wave is not queued behind the abandoned ones.

Live g1288 2026-11-20 15:33: エストラゴン launched a new wave (tick 33998)
while seven entries from ticks 1475..23839 were still remembered. The pick
took the oldest entry, so the fresh sortie waited while the stale generals were
re-aimed again. Entries past the stale bound are skipped individually; the
whole-wave stale gate still stops the pursuit when no fresh entry exists.
"""
from types import SimpleNamespace

from docich.hanjuku.flows import boss_pursuit as bp


def map_screen():
    return SimpleNamespace(kind='map', marker=None, cursor=None, text='',
                           hand=None, selected=None)


def memory(**extra):
    mem = {
        'chapter': 9,
        'tick': 10000,
        'world': {'sorties': {}, 'garrison': {}},
        bp.LIST_KEY: {
            'カシス': {'step': 'A', 'launched': 1475},
            'エストラゴン': {'step': 'B', 'launched': 9500},
        },
        'boss_cell': {'chapter': 9, 'cell': [810, 645], 'tick': 9990, 'readings': 2},
        'plan': {'active': None},
    }
    mem.update(extra)
    return mem


def test_the_fresh_entry_wins_over_an_abandoned_one(monkeypatch):
    monkeypatch.setattr(bp.chart, 'moving_boss_cell', lambda ch: (810, 645))
    mem = memory()
    assert bp.step(map_screen(), mem, None) == []
    assert mem['boss_pursuit']['general'] == 'エストラゴン'
    assert mem['boss_pursuit']['step'] == 'B'


def test_with_only_stale_entries_the_wave_gate_still_stops_everything(monkeypatch):
    monkeypatch.setattr(bp.chart, 'moving_boss_cell', lambda ch: (810, 645))
    mem = memory()
    mem[bp.LIST_KEY] = {'カシス': {'step': 'A', 'launched': 1475}}
    assert bp.step(map_screen(), mem, None) is None
    assert mem.get('boss_wave_stale_noted') == 10000


def test_stale_boss_cell_still_queues_the_pursuit(monkeypatch):
    """#4558: an expired cell must not block the pursuit whose own Y view
    re-measures it; before this fix the gate was self-reinforcing."""
    monkeypatch.setattr(bp.chart, 'moving_boss_cell', lambda ch: None)
    mem = memory()
    assert bp.step(map_screen(), mem, None) == []
    assert mem['boss_pursuit']['general'] == 'エストラゴン'

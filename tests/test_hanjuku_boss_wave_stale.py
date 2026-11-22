"""A boss wave that never entered is not re-aimed forever.

Live g1288 2026-11-20: the seven remembered waves were launched at ticks
1475..23839 and were still being redirected past tick 32000. Every pursuit
rotated through them, no redirect entered the boss, and because no general ever
came home no castle held a spare, so month after month closed with zero
launches (audit 64-3..65-x). Once even the newest wave is older than the stale
bound the pursuit stops chasing and the camp recall takes the generals back to
a castle so a fresh wave can start.
"""
import datetime
from types import SimpleNamespace

from docich import hanjuku_chart as chart
from docich.hanjuku.flows import boss_pursuit as bp
from docich.hanjuku.flows import recall


def map_screen():
    return SimpleNamespace(kind='map', marker=None, cursor=None, text='',
                           hand=None, selected=None)


def memory(tick=10000, launched=6400, **extra):
    mem = {
        'chapter': 9,
        'tick': tick,
        'captured': list(chart.corridor(9)),
        'world': {'sorties': {}, 'garrison': {}},
        bp.LIST_KEY: {'ミント': {'step': 'A', 'launched': launched}},
        'boss_cell': {'chapter': 9, 'cell': [810, 645], 'tick': tick - 10,
                      'readings': 2},
        'plan': {'active': None},
    }
    mem.update(extra)
    return mem


def arm(monkeypatch):
    monkeypatch.setattr(bp.chart, 'moving_boss_cell', lambda ch: (810, 645))
    monkeypatch.setattr(recall.chart, 'moving_boss_cell', lambda ch: (810, 645))


def test_a_wave_past_the_stale_bound_is_not_pursued_and_is_recorded_once(monkeypatch):
    arm(monkeypatch)
    mem = memory(launched=10000 - bp.PURSUIT_STALE_TICKS)
    assert bp.step(map_screen(), mem, None) is None
    assert mem['boss_wave_stale_noted'] == 10000
    stale = [r for r in mem.get('_records', []) if r['decision'] == 'boss_wave_stale']
    assert len(stale) == 1
    assert stale[0]['observed_metric']['newest_launched'] == 10000 - bp.PURSUIT_STALE_TICKS
    # A second observation does not repeat the record, and still sends nothing.
    assert bp.step(map_screen(), mem, None) is None
    assert len([r for r in mem.get('_records', [])
                if r['decision'] == 'boss_wave_stale']) == 1


def test_a_fresh_wave_still_starts_a_pursuit(monkeypatch):
    arm(monkeypatch)
    mem = memory(launched=10000 - 500)
    assert bp.step(map_screen(), mem, None) == []
    assert mem['boss_pursuit']['general'] == 'ミント'
    assert 'boss_wave_stale_noted' not in mem


def test_no_entries_is_not_a_stale_wave():
    assert bp.wave_stale({'chapter': 9, 'tick': 99999, bp.LIST_KEY: {}}) is False


def test_a_stale_wave_stops_the_camp_boss_redirect(monkeypatch):
    arm(monkeypatch)
    fresh = memory(launched=10000 - 500)
    assert recall.boss_redirect_goal(fresh) == chart.boss_castle(9)
    stale = memory(launched=10000 - bp.PURSUIT_STALE_TICKS)
    assert recall.boss_redirect_goal(stale) is None


def test_wave_active_is_false_for_a_stale_wave_and_true_for_a_fresh_one():
    def staffed(**kw):
        mem = memory(**kw)
        mem.setdefault('world', {})['garrison'] = {'イデ！': ['どうし', 'ゼウス']}
        return mem
    assert bp.wave_active(staffed(launched=10000 - bp.PURSUIT_STALE_TICKS)) is False
    assert bp.wave_active(staffed(launched=10000 - 500)) is True
    empty = memory()
    empty[bp.LIST_KEY] = {}
    assert bp.wave_active(empty) is False

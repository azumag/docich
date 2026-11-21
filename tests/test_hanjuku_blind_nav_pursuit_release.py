"""An exhausted blind month walk must release the field.

Live g1288 2026-11-21 21:02 (chapter 9, moving boss at 1 HP): the month menu
could not be read, so the bounded blind walk tried しょうにん four times. The
state was left in the policy afterwards, and boss_pursuit.step counts any
month_blind_nav as "a month menu owns the screen". No boss pursuit was
queued from 20:59 to 21:45: the launched wave stood on its waypoint while the
boss was nearly dead. The walk now drops its state when the tries are spent,
and the pursuit only defers to a walk that can still press.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from types import SimpleNamespace

from docich import hanjuku_policy as policy
from docich.hanjuku.flows import boss_pursuit as bp
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import Canvas, MONTH_GRID


def menu(on='しょうにん'):
    c = Canvas()
    c.text(48, 15, '1ねん 11のつき 163G')
    for label, (x, y) in MONTH_GRID.items():
        c.text(x, y, label)
    x, y = MONTH_GRID[on]
    c.hand(x - 22, y - 6)
    return parse(c.frame())


def stuck(mem, screen, label='へいしほじゅう'):
    for _ in range(policy.MENU_NAV_PRESS_LIMIT + 3):
        policy._month_menu_move(screen, mem, label)


def test_an_exhausted_blind_walk_stops_claiming_the_month_menu():
    screen = menu()
    mem = {'month': '1-11', '_records': []}
    stuck(mem, screen)
    # The guarded walk may already have spent a try or two of the blind plan;
    # keep walking until the bounded plan is spent and released.
    for _ in range(policy.MONTH_BLIND_NAV_LIMIT + 3):
        if 'month_blind_nav' in mem:
            assert mem['month_blind_nav']['tries'] <= policy.MONTH_BLIND_NAV_LIMIT
        policy._month_menu_move(screen, mem, 'へいしほじゅう')
    assert 'month_blind_nav' not in mem
    assert mem['month_blind_nav_exhausted']['label'] == 'へいしほじゅう'
    assert mem['month_blind_nav_exhausted']['month'] == '1-11'
    # No new decision type: the last evidence stays the bounded blind walk.
    assert [r['decision'] for r in mem['_records']][-1] == 'month_blind_nav'

    # The same label in the same month never restarts the blind plan.
    blind_records = len([r for r in mem['_records']
                         if r['decision'] == 'month_blind_nav'])
    for _ in range(policy.MONTH_BLIND_NAV_LIMIT + 2):
        policy._month_menu_move(screen, mem, 'へいしほじゅう')
        assert 'month_blind_nav' not in mem
        assert len([r for r in mem['_records']
                    if r['decision'] == 'month_blind_nav']) == blind_records

    # A new month may try the measured layout again.
    mem['month'] = '1-12'
    assert policy._month_menu_move(screen, mem, 'へいしほじゅう') != []
    assert mem['month_blind_nav']['tries'] == 1


def map_screen():
    return SimpleNamespace(kind='map', marker=None, cursor=None, text='',
                           hand=None, selected=None)


def memory(tick=10000, **extra):
    mem = {
        'chapter': 9,
        'tick': tick,
        'world': {'sorties': {}, 'garrison': {}},
        bp.LIST_KEY: {'ミント': {'step': 'A', 'launched': tick - 500}},
        'boss_cell': {'chapter': 9, 'cell': [810, 645], 'tick': tick - 10,
                      'readings': 2},
        'plan': {'active': None},
    }
    mem.update(extra)
    return mem


def test_an_exhausted_blind_walk_no_longer_holds_the_boss_field(monkeypatch):
    monkeypatch.setattr(bp.chart, 'moving_boss_cell', lambda ch: (810, 645))
    mem = memory(month_blind_nav={'label': 'しょうにん',
                                  'tries': policy.MONTH_BLIND_NAV_LIMIT})
    assert bp.step(map_screen(), mem, None) == []
    assert mem['boss_pursuit']['general'] == 'ミント'
    assert mem['boss_pursuit_start_tick'] == 10000


def test_an_active_blind_walk_still_owns_the_screen(monkeypatch):
    monkeypatch.setattr(bp.chart, 'moving_boss_cell', lambda ch: (810, 645))
    mem = memory(month_blind_nav={'label': 'しょうにん',
                                  'tries': policy.MONTH_BLIND_NAV_LIMIT - 1})
    assert bp.step(map_screen(), mem, None) is None
    assert 'boss_pursuit' not in mem

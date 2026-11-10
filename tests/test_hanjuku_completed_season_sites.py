"""Completed 四季王 sites must stay closed when an entry lacks its corner."""

import pytest

from docich import hanjuku_chart as chart
from docich.hanjuku.flows import battle, deploy
from docich.hanjuku.strategy import orders
from docich.hanjuku_map_sources import SEASON_KINGS
from docich.hanjuku.state.regions import plan as _plan_region


def memory():
    return {'chapter': 4, 'tick': 100,
            'captured': list(chart.CASTLE_NAMES[4]),
            'orders': {o['step']: 'launched' for o in chart.orders(4)
                       if not chart.is_boss_castle(o['target'])}}


def win(mem, king, site):
    mem['battle'] = {'away': 1, 'ally': 'ゼウス', 'enemy': king,
                     'castle': site, 'side': 'attack', 'enemy_hp': 0,
                     'ally_hp': 40, 'entry_evidence': 'measured_boss_entry'}
    battle.battle_end(mem, 'map')


@pytest.mark.parametrize('site', ['四季王城(北西)', '四季王城(南西)'])
def test_an_unlocated_duplicate_win_keeps_the_measured_corner_closed(site):
    mem = memory()
    order = next(o for o in chart.orders(4) if o['target'] == site)
    win(mem, 'サマカンテ', order['target'])
    receipt = dict(mem['boss_victories']['サマカンテ'])
    mem['tick'] += 1
    win(mem, 'サマカンテ', 'ボス')

    assert mem['boss_victories']['サマカンテ'] == receipt
    assert not orders._ready(order, mem)
    assert orders.next_order(mem)['target'] != order['target']
    # A plan already selected before the win must back out before sending.
    _plan_region(mem)['active'] = order['step']
    assert deploy._hold_locked_boss(mem, order)
    assert _plan_region(mem)['active'] is None


def test_four_measured_wins_close_every_site_even_without_corner_bindings():
    mem = memory()
    for king in sorted(SEASON_KINGS):
        win(mem, king, 'ボス')

    assert mem['boss_defeated'] == 4
    assert orders.next_order(mem) is None
    for order in chart.orders(4):
        if chart.is_boss_castle(order['target']):
            assert not orders._ready(order, mem)
            assert orders._site_king_down(mem, order['target'])


def test_a_single_unlocated_win_does_not_close_unmeasured_sites():
    mem = memory()
    win(mem, 'サマカンテ', 'ボス')
    assert not mem.get('boss_defeated')
    assert orders.next_order(mem) is not None
    assert not any(orders._site_king_down(mem, site) for site in chart.boss_sites(4))


def test_northwest_and_southwest_stay_closed_while_east_sites_remain_ready():
    mem = memory()
    cleared = {'四季王城(北西)', '四季王城(南西)'}
    for king, site in zip(sorted(SEASON_KINGS), sorted(cleared)):
        win(mem, king, site)
        win(mem, king, 'ボス')

    assert not mem.get('boss_defeated')
    assert orders.next_order(mem)['target'] not in cleared
    for order in chart.orders(4):
        if order['target'] in chart.boss_sites(4):
            assert orders._ready(order, mem) == (order['target'] not in cleared)


def test_chapter_change_discards_the_completed_sites():
    from docich.hanjuku.strategy.campaign import _enter_chapter

    mem = memory()
    for king, site in zip(sorted(SEASON_KINGS), chart.boss_sites(4)):
        win(mem, king, site)
    _enter_chapter(mem, 5, reason='test')
    assert not mem.get('boss_defeated')
    assert not mem.get('boss_victories')

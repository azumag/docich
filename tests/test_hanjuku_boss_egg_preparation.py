"""g486 boss sortie waits for measured egg recovery, without inferred payment."""
from test_hanjuku_chart_bot import policy, sortie_canvas
from docich import hanjuku_chart as chart
from docich.hanjuku_screen import Screen
import pytest


def order():
    return next(o for o in chart.orders(1) if o['step'] == '1-B1')


def memory(uses=None):
    mem = {'chapter': 1, 'active': '1-B1', 'orders': {'1-B1': 'pending'},
           'captured': list(set(chart.castles(1)) - {chart.home_castle(1), chart.boss_castle(1)}),
           'sortie_general': {'1-B1': 'どうし'}, 'picked': ['クースカン']}
    if uses is not None:
        mem['egg_uses'] = {'どうし': uses}
    return mem


@pytest.mark.parametrize('uses', range(4))
def test_depleted_boss_order_waits_and_does_not_request_replacement(uses):
    mem = memory(uses)
    assert not policy._ready(order(), mem)
    policy._off_chart(mem)
    policy._off_chart(mem)
    assert 'chart_adjust' not in mem
    assert len(mem['_records']) == 1
    assert mem['_records'][0]['decision'] == 'boss_egg_recovery_wait'
    assert mem['orders']['1-B1'] == 'pending'


def test_newly_read_depletion_cancels_sortie_and_recovery_reopens_check():
    mem = memory()
    assert policy._ready(order(), mem)
    screen = sortie_canvas('どうし', 3)
    policy.observe_events(screen, mem)
    assert policy.deploy_step(screen, mem) == [policy.pad('b'), policy.pad('b')]
    assert mem['active'] is None and mem['picked'] == []
    assert not policy._ready(order(), mem)
    # Existing recovery payment path clears stale counts, never invents 4.
    mem['month_sub'] = {'kind': 'egg', 'gold_before': 100, 'quoted_cost': 50,
                        'full_selected': True, 'left_menu': True}
    assert policy._finish_month_sub(Screen([], None, '', header={'gold': 50}), mem, {'egg': 'opened'})
    assert mem['egg_uses'] == {} and policy._ready(order(), mem)
    # A fresh depleted reading cancels again even after observed payment.
    mem.update(active='1-B1', picked=[], sortie_general={'1-B1': 'どうし'})
    policy.observe_events(screen, mem)
    assert policy.deploy_step(screen, mem) == [policy.pad('b'), policy.pad('b')]
    policy.observe_events(sortie_canvas('どうし', 4), mem)
    assert policy._ready(order(), mem)


def test_guard_is_scoped_to_chapter_one_hero_boss_and_known_counts():
    mem = memory(3)
    road = next(o for o in chart.orders(1) if o['step'] == '1-A1')
    assert policy._ready(road, mem)
    assert not policy._boss_egg_depleted({**order(), 'general': 'ゼウス'},
                                         {**mem, 'sortie_general': {'1-B1': 'ゼウス'}})
    assert not policy._boss_egg_depleted(order(), {**mem, 'chapter': 2})
    assert policy._ready(order(), memory(4))
    assert policy._ready(order(), memory())
    assert policy._ready(order(), memory(True))


def test_recovery_wait_reserves_income_for_eggs_before_soldier_refill():
    mem = memory(3)
    mem['soldiers_seen'] = 60
    mem['soldiers_seen_key'] = '1-6'
    shop = policy._plan(mem, {'year': 1, 'month': 6, 'gold': 120})
    assert shop['reserve'] >= policy.EGG_RECOVER_COST
    assert shop['egg'] == 'pending'
    assert shop['soldiers'] <= 120 - policy.EGG_RECOVER_COST - policy.WAGE_RESERVE

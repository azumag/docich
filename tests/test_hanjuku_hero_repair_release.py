"""#2601: release only current, positively unavailable hero repair funds."""
from copy import deepcopy

import pytest
from docich import hanjuku_policy as p
from docich.hanjuku.strategy import economy as e


def memory():
    identity = {'game': 'hanjuku-hero', 'generation': 889,
                'runtime_id': 'g889-fixture', 'lease_id': 'fixture-lease'}
    return {'_run_identity': identity, 'chapter': 2, 'month': '4-1', 'tick': 500,
            'gold': 65, '_records': [], 'house_eggs': {p.NAME: {
                'general': p.NAME, 'broken': True, 'hp': 30, 'max_hp': 40,
                'location': 'castle', 'location_observed': True, 'month': '4-1',
                'observed_chapter': 2, 'observed_tick': 480,
                'observed_run_identity': dict(identity)}},
            'shop': {'key': '4-1', 'hero_repair_reserve': 50, 'reserve': 0,
                     'general_repair_reserve': 0, 'recruit_reserve': 0,
                     'egg': 'done', 'recruit': 'done', 'merchant_done': True,
                     'items': [], 'soldiers': 45, 'soldiers_done': False}}


def header(month=1):
    return {'year': 4, 'month': month, 'gold': 65}


def test_fresh_wound_releases_budget_and_building_floor_without_repeating_log():
    mem = memory(); shop = mem['shop']
    assert e._soldier_spendable(mem, shop, header()) == 15
    e._reserve_hero_repair(mem, shop, 65)
    assert e._soldier_spendable(mem, shop, header()) == 65
    assert e._chikujou_reserve(mem, dict(shop, soldiers_done=True)) == 30
    e._reserve_hero_repair(mem, shop, 65)
    records = [r for r in mem['_records'] if r['decision'] == 'hero_repair_funds_released']
    assert len(records) == 1
    assert records[0]['observed_metric']['released'] == 50
    assert records[0]['observed_metric']['condition'] == 'wounded'


def test_next_month_fresh_inability_does_not_recreate_reserve_and_recovery_does():
    mem = memory(); e._reserve_hero_repair(mem, mem['shop'], 65)
    mem['month'] = '4-2'; mem['shop']['key'] = '4-2'
    mem['house_eggs'][p.NAME].update(month='4-2', observed_tick=500)
    e._reserve_hero_repair(mem, mem['shop'], 65)
    assert mem['shop']['hero_repair_reserve'] == 0
    mem['house_eggs'][p.NAME]['hp'] = 40
    e._reserve_hero_repair(mem, mem['shop'], 65)
    assert mem['shop']['hero_repair_reserve'] == 50


@pytest.mark.parametrize('changes', [
    {'hp': None}, {'hp': True}, {'hp': 40}, {'max_hp': None},
    {'month': '3-12'}, {'observed_chapter': 1}, {'observed_tick': 501},
    {'observed_tick': 99}, {'observed_run_identity': None}, {'general': 'ゼウス'},
])
def test_unknown_or_stale_evidence_keeps_reserve(changes):
    mem = memory(); mem['house_eggs'][p.NAME].update(changes)
    e._reserve_hero_repair(mem, mem['shop'], 65)
    assert mem['shop']['hero_repair_reserve'] == 50


@pytest.mark.parametrize('phase', ['castle_pick', 'travel', 'buy', 'receipt', 'return_pick'])
def test_active_repair_keeps_its_cash_even_with_older_wound(phase):
    mem = memory(); mem['house'] = {'general': p.NAME, 'phase': phase}
    e._reserve_hero_repair(mem, mem['shop'], 65)
    assert mem['shop']['hero_repair_reserve'] == 50


def test_pending_month_purchase_and_other_reservations_are_preserved():
    mem = memory(); mem['month_sub'] = {'kind': 'soldiers'}
    e._reserve_hero_repair(mem, mem['shop'], 65)
    assert mem['shop']['hero_repair_reserve'] == 50
    del mem['month_sub']
    mem['shop'].update(reserve=7, general_repair_reserve=9, recruit_reserve=11)
    before = deepcopy(mem['shop'])
    e._reserve_hero_repair(mem, mem['shop'], 65)
    for k in ('reserve', 'general_repair_reserve', 'recruit_reserve'):
        assert mem['shop'][k] == before[k]
    assert e._soldier_spendable(mem, mem['shop'], header()) == 38


def test_moving_observation_defers_but_full_hp_castle_or_camp_is_not_inability():
    mem = memory(); info = mem['house_eggs'][p.NAME]
    info.update(hp=40, location='field')
    assert e._hero_repair_defer_reason(mem) == 'moving'
    info['camp_observed'] = True
    assert e._hero_repair_defer_reason(mem) is None
    info.update(location='castle', camp_observed=False)
    assert e._hero_repair_defer_reason(mem) is None


def test_real_month_menu_reopens_low_army_refill_after_release():
    from test_hanjuku_low_army_refill_first import memory as low_memory
    from test_hanjuku_army_count_check import screen
    mem = low_memory(count=5, gold=65)
    mem['tick'] = 500
    info = deepcopy(memory()['house_eggs'][p.NAME])
    info.update(month=mem['month'], observed_chapter=mem['chapter'],
                observed_tick=mem['tick'], observed_run_identity=dict(mem['_run_identity']))
    mem['house_eggs'] = {p.NAME: info}
    mem['shop']['hero_repair_reserve'] = 50
    p.month_step(screen(65, on='しょうにん'), mem)
    assert mem['shop']['hero_repair_reserve'] == 0
    assert mem['shop']['soldiers'] == 45
    assert mem['shop']['soldiers_done'] is False
    assert p.month_step(screen(65, on='へいしほじゅう'), mem) == [p.pad('a')]
    assert not mem['shop'].get('soldier_receipt')


@pytest.mark.parametrize('bad', ['50', True, -1, None])
def test_malformed_reservation_never_becomes_optional_spending(bad):
    mem = memory(); mem['shop']['hero_repair_reserve'] = bad
    e._reserve_hero_repair(mem, mem['shop'], 65)
    assert mem['shop']['hero_repair_reserve'] is bad
    assert e._soldier_spendable(mem, mem['shop'], header()) is None
    assert e._chikujou_reserve(mem, mem['shop']) is None


@pytest.mark.parametrize('stored', [0, 20, 50, 60])
def test_log_distinguishes_stored_cash_from_implicit_building_floor(stored):
    mem = memory(); mem['shop']['hero_repair_reserve'] = stored
    e._reserve_hero_repair(mem, mem['shop'], 65)
    detail = mem['_records'][-1]['observed_metric']
    assert detail['released'] == stored
    assert detail['building_floor_released'] == max(50, stored)

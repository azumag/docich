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


def test_previous_month_status_survives_roster_only_new_month_and_opens_refill():
    from test_hanjuku_low_army_refill_first import memory as low_memory
    from test_hanjuku_army_count_check import screen
    from test_hanjuku_house import status, parse
    from docich.hanjuku.flows import house
    mem = low_memory(count=5, gold=65)
    new_month = mem['month']
    mem.update(month='1-7', tick=50)
    # Actual normal field observation, before the next monthly roster-only scan.
    house._observe_status(parse(status(p.NAME, hp=30, max_hp=40)), mem)
    assert mem['hero_repair_deferral']['reason'] == 'wounded'
    mem.update(month=new_month, tick=1000)
    # No new individual status and no forced repair/field trip in the monthly menu.
    mem['shop']['hero_repair_reserve'] = 50
    p.month_step(screen(65, on='しょうにん'), mem)
    assert mem['house_eggs'][p.NAME]['month'] == '1-7'
    assert mem['shop']['hero_repair_reserve'] == 0
    assert mem['shop']['soldiers'] == 45
    assert mem['shop']['soldiers_done'] is False
    assert p.month_step(screen(65, on='へいしほじゅう'), mem) == [p.pad('a')]
    assert not mem['shop'].get('soldier_receipt')


@pytest.mark.parametrize('change', ['unknown', 'expired', 'month', 'missing'])
def test_established_deferral_persists_without_positive_readiness(change):
    mem = memory(); e._reserve_hero_repair(mem, mem['shop'], 65)
    if change == 'unknown':
        mem['house_eggs'][p.NAME].update(hp=None, location_observed=False)
    elif change == 'expired':
        mem['tick'] += 1000
    elif change == 'month':
        mem['month'] = '4-2'
    else:
        mem.pop('house_eggs')
    mem['shop'] = {'key': '4-2', 'hero_repair_reserve': 50}
    e._reserve_hero_repair(mem, mem['shop'], 65)
    assert mem['shop']['hero_repair_reserve'] == 0


@pytest.mark.parametrize('change', ['run', 'chapter'])
def test_deferral_does_not_cross_run_or_chapter(change):
    mem = memory(); e._reserve_hero_repair(mem, mem['shop'], 65)
    if change == 'run':
        mem['_run_identity']['generation'] += 1
    else:
        mem['chapter'] += 1
    e._reserve_hero_repair(mem, mem['shop'], 65)
    assert mem['shop']['hero_repair_reserve'] == 50
    assert 'hero_repair_deferral' not in mem


def test_positive_status_recheck_restores_reserve_after_unknown_and_new_month():
    from test_hanjuku_house import status, parse
    from docich.hanjuku.flows import house
    mem = memory(); e._reserve_hero_repair(mem, mem['shop'], 65)
    mem.update(month='4-2', tick=1000)
    house._observe_status(parse(status(p.NAME, hp=40, max_hp=40)), mem)
    assert 'hero_repair_deferral' not in mem
    e._reserve_hero_repair(mem, mem['shop'], 65)
    assert mem['shop']['hero_repair_reserve'] == 50


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


@pytest.mark.parametrize('field,value', [('observed_tick', None), ('observed_tick', True),
                                        ('observed_tick', 501), ('month', None), ('reason', 'unknown')])
def test_corrupt_persisted_deferral_cannot_release_money(field, value):
    mem = memory(); e._reserve_hero_repair(mem, mem['shop'], 65)
    mem['hero_repair_deferral'][field] = value
    mem['house_eggs'][p.NAME]['observed_tick'] = 0  # cannot recreate from stale evidence
    e._reserve_hero_repair(mem, mem['shop'], 65)
    assert mem['shop']['hero_repair_reserve'] == 50


@pytest.mark.parametrize('source', ['observed', 'persisted'])
@pytest.mark.parametrize('field', ['generation', 'chapter', 'month'])
def test_boolean_identity_and_invalid_month_never_release(source, field):
    mem = memory()
    mem['_run_identity']['generation'] = 1
    mem['chapter'] = 1
    info = mem['house_eggs'][p.NAME]
    info['observed_run_identity'] = dict(mem['_run_identity'])
    info['observed_chapter'] = 1
    if source == 'persisted':
        e._reserve_hero_repair(mem, mem['shop'], 65)
        target = mem['hero_repair_deferral']
        info['observed_tick'] = 0
        identity_key, chapter_key = 'run_identity', 'chapter'
    else:
        target = info
        identity_key, chapter_key = 'observed_run_identity', 'observed_chapter'
    if field == 'generation':
        target[identity_key]['generation'] = True
    elif field == 'chapter':
        target[chapter_key] = True
    else:
        target['month'] = 'not-a-month'
        mem['month'] = 'not-a-month'
    e._reserve_hero_repair(mem, mem['shop'], 65)
    assert mem['shop']['hero_repair_reserve'] == 50


def test_actual_main_camp_observation_restores_repair_reservation():
    from test_hanjuku_house import Canvas, parse
    from docich.hanjuku.flows import camp_recheck
    mem = memory(); e._reserve_hero_repair(mem, mem['shop'], 65)
    mem.update(month='4-2', tick=1000)
    canvas = Canvas()
    canvas.text(56, 7, '2わ4ねん2のつき65G')
    canvas.text(24, 39, p.NAME)
    canvas.text(88, 39, 'しょうぐん')
    canvas.text(24, 55, 'HP')
    canvas.text(64, 55, '40/ 40 P')
    canvas.text(24, 119, 'たまご')
    canvas.text(56, 119, 'こわれている')
    canvas.text(24, 151, 'キャンプしています')
    camp_recheck.observe_camp_status(parse(canvas.frame()), mem)
    assert 'hero_repair_deferral' not in mem
    e._reserve_hero_repair(mem, mem['shop'], 65)
    assert mem['shop']['hero_repair_reserve'] == 50

"""Egg recovery deferral releases a bounded, paid-and-observed troop refill."""
from copy import deepcopy
import json

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku.flows import month as monthly
from docich.hanjuku.strategy import economy
from docich.hanjuku.strategy.soldier_fallback import (
    DEFERRAL_KEY, egg_deferred_for_soldiers, refresh_soldier_egg_deferral,
)
from docich.hanjuku_font import TextLine
from test_hanjuku_army_count_check import memory as army_memory, screen
from test_hanjuku_soldier_receipts import quantity


def memory(count=10, gold=79):
    mem = army_memory(gold)
    mem.update(soldiers_seen=count, soldiers_seen_key='1-8', egg_uses={'ココット': 0})
    mem['soldiers_seen_scope'] = deepcopy(economy._soldier_count_scope(mem, screen(gold).header))
    mem['shop'].update(egg='skipped', egg_priority=True, egg_cost=50,
                       reserve=min(50, max(0, gold - 30)), recruit='deferred_egg',
                       recruit_priority=False, recruit_reserve=0, closed=True)
    return mem


@pytest.mark.parametrize('count,gold,expected', [(0, 79, 49), (10, 79, 40), (49, 79, 1),
                                               (0, 45, 15), (10, 45, 15)])
def test_cached_zero_plan_opens_refill_after_unaffordable_recovery(count, gold, expected):
    mem = memory(count, gold)
    assert p.month_step(screen(gold), mem) == [p.pad('a')]
    assert mem['shop']['soldiers'] == expected
    assert mem['shop']['soldiers_done'] is False
    assert mem['shop']['reserve'] == 0
    assert mem['shop']['egg'] == 'skipped'  # not recovered, not "unnecessary"
    assert mem['egg_uses'] == {'ココット': 0}
    assert 'soldier_receipt' not in mem['shop']
    assert not mem.get('month_exit')


def test_new_skip_replans_in_the_same_observation_instead_of_exiting():
    mem = memory()
    mem['shop'].update(egg='pending', closed=False)
    assert p.month_step(screen(79), mem) == [p.pad('a')]
    assert mem['shop']['soldiers'] == 40
    assert mem['shop']['egg'] == 'skipped'
    assert not mem.get('month_exit')


def test_affordable_recovery_still_opens_before_soldiers():
    mem = memory(gold=130)
    mem['shop'].update(egg='pending', closed=False)
    assert p.month_step(screen(130, on='たまごのかいふく'), mem) == [p.pad('a')]
    assert mem['month_sub']['kind'] == 'egg'
    assert DEFERRAL_KEY not in mem['shop']


def test_refill_reaches_quantity_payment_and_adds_soldiers_once_after_real_debit():
    mem = memory()
    assert p.month_step(screen(79), mem) == [p.pad('a')]
    q = quantity(79)
    q.lines = [TextLine(199, tuple((24 + i * 8, c) for i, c in enumerate('かわりますぞ'))
                        + ((216, '4'), (224, '0')))]
    mem = json.loads(json.dumps(mem))
    assert p.quantity_step(q, mem, soldiers=True) == [p.pad('a')]
    assert mem['soldiers_seen'] == 10
    assert mem['shop']['soldier_receipt']['status'] == 'pending'
    for _ in range(2):
        p.observe_events(screen(39), mem)
    assert mem['soldiers_seen'] == 50
    assert mem['shop']['soldier_receipt']['status'] == 'paid'
    assert sum(r['decision'] == 'soldier_refill_receipt' for r in mem['_records']) == 1
    assert p.quantity_step(quantity(39), mem, soldiers=True) == [p.pad('b')]


@pytest.mark.parametrize('count', [50, 60, None, True, -1])
def test_sufficient_or_unread_army_does_not_release_recovery_money(count):
    mem = memory(count)
    before = deepcopy(mem['shop'])
    assert not refresh_soldier_egg_deferral(mem, mem['shop'], screen(79).header)
    assert mem['shop'] == before


@pytest.mark.parametrize('field', ['month', 'chapter', 'lease', 'missing_scope'])
def test_old_count_cannot_authorize_a_fallback(field):
    mem = memory()
    if field == 'month':
        mem['soldiers_seen_key'] = '1-7'
    elif field == 'chapter':
        mem['soldiers_seen_scope'][1] = 1
    elif field == 'lease':
        mem['soldiers_seen_scope'][0]['lease_id'] = 'foreign'
    else:
        mem.pop('soldiers_seen_scope')
    assert not refresh_soldier_egg_deferral(mem, mem['shop'], screen(79).header)
    assert DEFERRAL_KEY not in mem['shop']


@pytest.mark.parametrize('block', ['pending', 'paid', 'unverified', 'egg_unverified', 'ritual',
                                    'recruit_opened', 'recruit_unverified', 'payroll',
                                    'failed_check', 'corrupt_check', 'refill_requested', 'unknown_card'])
def test_live_unverified_or_exhausted_transactions_are_not_rearmed(block):
    mem = memory()
    shop = mem['shop']
    if block in ('pending', 'paid', 'unverified'):
        shop['soldier_receipt'] = {'status': block}
    elif block == 'egg_unverified':
        shop['egg'] = 'unverified'
    elif block == 'ritual':
        mem['month_sub'] = {'kind': 'egg', 'stage': 'recovering'}
    elif block.startswith('recruit_'):
        shop['recruit'] = block.removeprefix('recruit_')
    elif block == 'payroll':
        mem['recruit_payroll_pending'] = ['ココット']
    elif block.endswith('check') or block == 'refill_requested':
        mem['army_count_check'] = {'scope': deepcopy(mem['soldiers_seen_scope']),
            'stage': 'unverified' if block == 'failed_check' else 'unexpected' if block == 'corrupt_check' else 'measured',
            'refill_requested': block == 'refill_requested'}
    else:
        shop.update(merchant_done=False, items=[['未測定', 1]])
    before = deepcopy(shop)
    assert not refresh_soldier_egg_deferral(mem, shop, screen(79).header)
    assert shop == before


@pytest.mark.parametrize('gold,repair,cards,expected', [(79, 0, [], 40), (79, 50, [], 0),
    (95, 50, [], 15), (79, 0, [['ブラッキー', 10]], 19), (30, 0, [], 0)])
def test_shortfall_wages_repairs_and_required_cards_remain_reserved(gold, repair, cards, expected):
    mem = memory(gold=gold)
    mem['shop'].update(hero_repair_reserve=repair, items=cards, merchant_done=not cards)
    assert refresh_soldier_egg_deferral(mem, mem['shop'], screen(gold).header) is bool(expected)
    assert mem['shop']['soldiers'] == expected
    assert mem['shop']['hero_repair_reserve'] == repair


def test_large_payroll_does_not_reintroduce_the_old_full_payroll_troop_hold(monkeypatch):
    mem = memory(0, 99)
    monkeypatch.setattr(economy, '_egg_recovery_wage_floor', lambda m: 90)
    assert p.month_step(screen(99), mem) == [p.pad('a')]
    assert mem['shop']['soldiers'] == 50


def test_json_hotload_does_not_restore_egg_or_recruit_reserves_or_renew_finished_budget():
    mem = memory(0)
    assert p.month_step(screen(79), mem) == [p.pad('a')]
    mem = json.loads(json.dumps(mem))
    shop = mem['shop']
    shop.update(soldiers=0, soldiers_done=True)
    for _ in range(5):
        economy._refresh_egg_priority(mem, shop, screen(79).header)
        economy._zero_army_refill(mem, shop, screen(79).header)
        economy._prioritise_recruit(mem, shop, 79)
    assert shop['soldiers'] == 0 and shop['soldiers_done'] is True
    assert shop['reserve'] == shop['recruit_reserve'] == 0
    assert sum(r['decision'] == 'soldier_refill_after_egg_skip' for r in mem['_records']) == 1


@pytest.mark.parametrize('value', [None, True, {}, {'version': True, 'active': True},
                                  {'version': 1, 'active': True, 'scope': ['foreign']}])
def test_corrupt_deferral_is_not_a_second_budget(value):
    mem = memory()
    mem['shop'][DEFERRAL_KEY] = value
    assert not refresh_soldier_egg_deferral(mem, mem['shop'], screen(79).header)
    assert mem['shop']['soldiers_done'] is True
    assert not egg_deferred_for_soldiers(mem['shop'])


@pytest.mark.parametrize('change', ['header', 'scope', 'count', 'failed_check'])
def test_existing_deferral_loses_permission_when_observations_become_invalid(change):
    mem = memory()
    header = screen(79).header
    assert refresh_soldier_egg_deferral(mem, mem['shop'], header)
    if change == 'header':
        header = None
    elif change == 'scope':
        mem['soldiers_seen_scope'][1] = 1
    elif change == 'count':
        mem['soldiers_seen'] = None
    else:
        mem['army_count_check'] = {'scope': deepcopy(mem['soldiers_seen_scope']), 'stage': 'unverified'}
    assert not refresh_soldier_egg_deferral(mem, mem['shop'], header)
    assert not egg_deferred_for_soldiers(mem['shop'])

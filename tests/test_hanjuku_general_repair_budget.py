"""g706: optional 16G building must not spend Cocotte's affordable house trip."""
from copy import deepcopy
from pathlib import Path

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse


def memory(name='ココット'):
    return {'chapter': 2, 'month': '3-4', 'tick': 500, 'gold': 84, '_records': [],
            'captured': ['フーリック', 'ドミノーラ'],
            'garrison': {'ドミノーラ': [name], 'フーリック': ['ゼウス']},
            'house_eggs': {name: {'general': name, 'broken': True, 'hp': p.general_max_hp(name),
                'max_hp': p.general_max_hp(name), 'location': 'castle', 'location_observed': True,
                'month': '3-4', 'observed_chapter': 2, 'observed_tick': 452}},
            'shop': {'key': '3-4', 'gold_start': 234, 'egg': 'done', 'reserve': 0,
                     'recruit': 'deferred_roster', 'items': [], 'merchant_done': True,
                     'soldiers': 54, 'soldiers_done': False, 'chikujou': 'check'}}


def plan(mem, gold=84):
    return p._plan(mem, {'year': 3, 'month': 4, 'gold': gold})


@pytest.mark.parametrize('name', ['ココット', 'ヴィーナス', 'ゼウス'])
def test_cached_plan_keeps_one_house_gift_and_wages_for_any_broken_general(name):
    mem = memory(name)
    shop = plan(mem)
    assert shop['soldiers'] == 4
    assert p._chikujou_reserve(mem, dict(shop, soldiers_done=True)) == 80
    assert p._recruit_cash_floor(shop) == 130


def test_measured_native_16g_quote_is_declined_before_consuming_repair_fund():
    mem = memory()
    screen = parse(read_png(Path(__file__).parent / 'fixtures/hanjuku/g706-cocotte-repair-fund-building.png'))
    assert screen.header['gold'] == 84
    mem['shop']['soldiers_done'] = True
    sub = {'kind': 'chikujou', 'key': '3-4', 'chosen': 'フーリック', 'presses': 0}
    actions = p.month_sub_step(screen, dict(mem, month_sub=sub))
    assert actions != [p.pad('a')]
    assert sub.get('declined') and not sub.get('confirm_sent')


@pytest.mark.parametrize('changes', [
    {'broken': False}, {'location': 'field'}, {'location_observed': False},
    {'month': '3-3'}, {'observed_chapter': 1}, {'observed_tick': 501},
    {'hp': 12}, {'max_hp': None}, {'general': 'ゼウス'},
])
def test_unverified_stale_marching_or_wounded_general_does_not_hold_optional_cash(changes):
    mem = memory()
    mem['house_eggs']['ココット'].update(changes)
    shop = plan(mem)
    assert p._chikujou_reserve(mem, dict(shop, soldiers_done=True)) == 30


def test_last_castles_only_defender_is_not_a_dispatchable_repair_budget():
    mem = memory()
    mem.update(home_lost=True, captured=['ドミノーラ'], garrison={'ドミノーラ': ['ココット']})
    assert p._chikujou_reserve(mem, dict(plan(mem), soldiers_done=True)) == 30
    mem['garrison']['ドミノーラ'].append('ゼウス')
    assert p._chikujou_reserve(mem, dict(plan(mem), soldiers_done=True)) == 80


def test_repeated_plans_do_not_stack_and_actual_repair_or_month_change_releases_fund():
    mem = memory()
    for _ in range(3):
        shop = plan(mem)
    assert p._chikujou_reserve(mem, dict(shop, soldiers_done=True)) == 80
    repaired = deepcopy(mem)
    repaired['house_eggs']['ココット']['broken'] = False
    assert p._chikujou_reserve(repaired, dict(plan(repaired), soldiers_done=True)) == 30
    mem['month'] = '3-5'
    assert p._chikujou_reserve(mem, dict(plan(mem), soldiers_done=True)) == 30


def test_old_castle_status_cannot_override_a_new_march():
    mem = memory()
    mem['sorties'] = {'one': {'general': 'ココット', 'status': 'en_route', 'tick': 480}}
    assert p._chikujou_reserve(mem, dict(plan(mem), soldiers_done=True)) == 30
    mem['house_eggs']['ココット']['observed_tick'] = 490
    assert p._chikujou_reserve(mem, dict(plan(mem), soldiers_done=True)) == 80


def test_multiple_observed_breakages_hold_the_next_trip_without_stacking_fees():
    mem = memory()
    mem['house_eggs'].update(memory('ヴィーナス')['house_eggs'])
    for _ in range(3):
        shop = plan(mem)
    assert p._chikujou_reserve(mem, dict(shop, soldiers_done=True)) == 80
    records = [r for r in mem['_records'] if r['decision'] == 'general_repair_funds_reserved']
    assert len(records) == 1
    assert records[0]['observed_metric']['generals'] == ['ココット', 'ヴィーナス']


def test_other_generals_house_fund_never_prevents_critical_hero_egg_recovery():
    from test_hanjuku_chart_bot import Canvas
    mem = memory()
    plan(mem, gold=80)
    mem['egg_uses'] = {p.NAME: 0}
    c = Canvas()
    c.text(16, 15, '3ねん4のつき80G')
    c.text(16, 151, '1こで50Gになりまんな')
    c.text(32, 191, 'うむッ!')
    c.text(136, 191, 'いかんッ!')
    c.hand(10, 185)
    sub = {'hero_selected': True}
    assert p._egg_recovery_step(parse(c.frame()), mem, sub) == [p.pad('a')]
    assert sub['quoted_cost'] == 50

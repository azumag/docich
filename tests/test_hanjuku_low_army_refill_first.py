"""Twenty or fewer measured soldiers are refilled before eggs, recruits and cards."""
from copy import deepcopy
import json

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku.flows import month as monthly
from docich.hanjuku.strategy import economy
from docich.hanjuku_font import TextLine
from test_hanjuku_army_count_check import memory as army_memory, observe_count, screen
from test_hanjuku_soldier_receipts import quantity


def memory(count=16, gold=120):
    # Owner report (#2366): 16 soldiers, yet recruits and building came first.
    # The saved plan holds an egg recovery, a hiring fee and chart cards and
    # leaves the troops nothing, as the egg-first rule did.
    mem = army_memory(gold)
    mem.update(soldiers_seen=count, soldiers_seen_key='1-8', egg_uses={'ココット': 0})
    mem['soldiers_seen_scope'] = deepcopy(economy._soldier_count_scope(mem, screen(gold).header))
    mem['shop'].update(items=[['ブラッキー', 10]], merchant_done=False,
                       soldiers=0, soldiers_target=0, soldiers_done=True,
                       egg='pending', egg_priority=True, egg_cost=50, reserve=50,
                       recruit='check', recruit_priority=True, recruit_reserve=42)
    return mem


def editor(value, gold=120):
    s = quantity(gold)
    tens, ones = divmod(value, 10)
    s.lines = [TextLine(199, tuple((24 + i * 8, ch) for i, ch in enumerate('かわりますぞ'))
                        + ((216, str(tens)), (224, str(ones))))]
    return s


def test_sixteen_soldiers_open_the_refill_before_cards_egg_and_recruit():
    mem = memory()
    # The cursor rests on the merchant; the refill is reached first instead.
    assert p.month_step(screen(120, on='しょうにん'), mem) == [p.pad('down')]
    shop = mem['shop']
    assert economy.low_army_first(shop)
    assert shop['soldiers'] == 34 and shop['soldiers_done'] is False  # 16 -> 50
    assert shop['merchant_done'] is False and shop['items'] == [['ブラッキー', 10]]
    assert shop['egg'] == 'pending' and shop['reserve'] == 50  # waits, not skipped
    assert shop['recruit'] == 'deferred_army' and shop['recruit_reserve'] == 0
    assert not shop['recruit_priority']
    assert p.month_step(screen(120, on='へいしほじゅう'), mem) == [p.pad('a')]
    assert not shop.get('soldier_receipt') and not mem.get('month_sub')
    record = next(r for r in mem['_records'] if r['decision'] == 'low_army_refill_plan')
    assert record['observed_metric']['soldiers'] == 16
    assert record['observed_metric']['refill'] == 34
    assert record['observed_metric']['cards_after'] == ['ブラッキー']


@pytest.mark.parametrize('label', ['たまごのかいふく', 'しょうぐんぼしゅう'])
def test_egg_and_recruit_menus_are_not_opened_ahead_of_the_refill(label):
    mem = memory()
    assert p.month_step(screen(120, on=label), mem) != [p.pad('a')]
    assert not mem.get('month_sub')
    assert mem['shop']['egg'] == 'pending' and mem['shop']['recruit'] == 'deferred_army'


@pytest.mark.parametrize('count,first', [(0, True), (20, True), (21, False), (49, False)])
def test_threshold_is_twenty_soldiers(count, first):
    mem = memory(count)
    actions = p.month_step(screen(120, on='しょうにん'), mem)
    assert economy.low_army_first(mem['shop']) is first
    if first:
        assert actions == [p.pad('down')]
        assert mem['shop']['soldiers'] == 50 - count
    else:
        assert actions == [p.pad('a')]  # unchanged order: the chart cards first
        assert mem['shop']['soldiers_done'] is True


@pytest.mark.parametrize('gold,repair,expected', [(120, 0, 34), (70, 20, 20), (60, 0, 30),
                                                  (50, 20, 0), (30, 0, 0)])
def test_only_wages_and_house_repair_stay_in_hand(gold, repair, expected):
    mem = memory(gold=gold)
    shop = mem['shop']
    shop['hero_repair_reserve'] = repair
    economy._low_army_refill(mem, shop, screen(gold).header)
    if expected:
        assert economy.low_army_first(shop)
        assert shop['soldiers'] == expected and shop['soldiers_done'] is False
        assert gold - shop['soldiers'] >= economy.WAGE_RESERVE + repair
    else:
        # Nothing beyond wages/repair: the existing plan stays, nothing is held back.
        assert not economy.low_army_first(shop)
        assert shop['soldiers'] == 0 and shop['soldiers_done'] is True
    assert shop['hero_repair_reserve'] == repair and shop['reserve'] == 50


def test_editor_pays_the_full_low_army_refill_despite_rearmed_egg_hold_and_cards():
    mem = memory(gold=70)
    mem['shop']['hero_repair_reserve'] = 20
    assert p.month_step(screen(70, on='へいしほじゅう'), mem) == [p.pad('a')]
    assert mem['shop']['soldiers'] == 20
    mem = json.loads(json.dumps(mem))  # hotloaded between the menu and the editor
    assert economy.low_army_first(mem['shop'])
    assert p.quantity_step(editor(20, 70), mem, soldiers=True) == [p.pad('a')]
    receipt = mem['shop']['soldier_receipt']
    assert receipt['qty'] == 20 and receipt['status'] == 'pending'
    assert receipt['soldiers_before'] == 16
    assert mem['shop']['reserve'] == 50  # the egg hold is kept for afterwards


def test_paid_refill_hands_the_menu_back_to_cards_then_egg():
    mem = memory()
    p.month_step(screen(120, on='へいしほじゅう'), mem)
    assert p.quantity_step(editor(34), mem, soldiers=True) == [p.pad('a')]
    for _ in range(2):
        p.observe_events(screen(86), mem)
    assert mem['shop']['soldier_receipt']['status'] == 'paid'
    assert mem['soldiers_seen'] == 50
    # Settled: the merchant comes next and the egg hold is restored.
    assert p.month_step(screen(86, on='しょうにん'), mem) == [p.pad('a')]
    assert not economy.low_army_first(mem['shop'])
    assert mem['shop']['reserve'] == 50 and mem['shop']['egg'] == 'pending'
    assert sum(r['decision'] == 'soldier_refill_receipt' for r in mem['_records']) == 1


def test_partial_refill_does_not_rearm_once_its_receipt_exists():
    mem = memory(gold=60)
    p.month_step(screen(60, on='へいしほじゅう'), mem)
    assert p.quantity_step(editor(30, 60), mem, soldiers=True) == [p.pad('a')]
    for _ in range(2):
        p.observe_events(screen(30), mem)
    assert mem['soldiers_seen'] == 46
    shop = mem['shop']
    for _ in range(3):
        economy._low_army_refill(mem, shop, screen(30).header)
    assert not economy.low_army_first(shop) and shop['soldiers_done'] is True
    assert shop['soldier_receipt']['status'] == 'paid'


@pytest.mark.parametrize('block', ['pending', 'paid', 'unverified', 'ritual', 'recruit_opened',
                                   'recruit_unverified', 'failed_check', 'payroll'])
def test_live_unverified_or_exhausted_transactions_are_not_rewritten(block):
    mem = memory()
    shop = mem['shop']
    if block in ('pending', 'paid', 'unverified'):
        shop['soldier_receipt'] = {'status': block}
    elif block == 'ritual':
        mem['month_sub'] = {'kind': 'egg', 'stage': 'recovering'}
    elif block.startswith('recruit_'):
        shop['recruit'] = block.removeprefix('recruit_')
    elif block == 'failed_check':
        mem['army_count_check'] = {'scope': deepcopy(mem['soldiers_seen_scope']), 'stage': 'unverified'}
    else:
        mem['recruit_payroll_pending'] = ['ココット']
    before = deepcopy(shop)
    economy._low_army_refill(mem, shop, screen(120).header)
    assert shop == before


@pytest.mark.parametrize('change', ['unread', 'month', 'chapter', 'lease'])
def test_unread_or_old_count_keeps_the_old_order(change):
    mem = memory()
    if change == 'unread':
        mem.pop('soldiers_seen_key')
    elif change == 'month':
        mem['soldiers_seen_key'] = '1-7'
    elif change == 'chapter':
        mem['soldiers_seen_scope'][1] = 1
    else:
        mem['soldiers_seen_scope'][0]['lease_id'] = 'foreign'
    before = deepcopy(mem['shop'])
    economy._low_army_refill(mem, mem['shop'], screen(120).header)
    assert mem['shop'] == before
    assert not economy.low_army_first(mem['shop'])


@pytest.mark.parametrize('value', [None, True, {}, {'version': True, 'active': True},
                                   {'version': 2, 'active': True}])
def test_corrupt_saved_state_is_not_priority(value):
    shop = {economy.LOW_ARMY_KEY: value}
    assert not economy.low_army_first(shop)


def test_stale_priority_is_withdrawn_on_the_next_month_observation():
    mem = memory()
    p.month_step(screen(120, on='へいしほじゅう'), mem)
    assert economy.low_army_first(mem['shop'])
    mem['soldiers_seen_key'] = '1-7'  # the measured total is no longer this month's
    economy._low_army_refill(mem, mem['shop'], screen(120).header)
    assert not economy.low_army_first(mem['shop'])
    assert economy._egg_before_soldiers(mem['shop'])


def test_unanswered_refill_request_is_bounded_and_not_rearmed():
    mem = memory()
    for key in ('soldiers_seen', 'soldiers_seen_key', 'soldiers_seen_scope'):
        mem.pop(key)  # a new month starts unread
    assert p.month_step(screen(120), mem) == [p.pad('a')]  # free count check
    assert not economy.low_army_first(mem['shop'])
    assert p.month_step(observe_count(mem, 16, 120), mem) == [p.pad('b')]
    assert p.month_step(screen(120, on='へいしほじゅう'), mem) == [p.pad('a')]
    assert economy.low_army_first(mem['shop']) and mem['shop']['soldiers'] == 34
    assert mem['army_count_check']['refill_requested'] is True
    for _ in range(3):
        assert p.month_step(screen(120, on='へいしほじゅう'), mem) == []
    assert p.month_step(screen(120, on='へいしほじゅう'), mem) == [p.pad('b')]
    for _ in range(3):
        p.month_step(screen(120, on='へいしほじゅう'), mem)
    assert mem['army_count_check']['stage'] == 'unverified'
    assert not economy.low_army_first(mem['shop'])
    assert mem['shop']['soldiers_done'] is True
    assert not mem['shop'].get('soldier_receipt')


def test_month_without_a_chart_purchase_still_plans_the_low_army_refill(monkeypatch):
    mem = memory()
    mem.pop('shop')
    monkeypatch.setattr(economy, '_charted_purchase_ahead', lambda *a: True)
    monkeypatch.setattr(p.chart, 'purchase_for', lambda *a: None)
    shop = economy._plan(mem, screen(120).header)
    assert shop is not None
    economy._low_army_refill(mem, shop, screen(120).header)
    assert shop['soldiers'] == 34 and shop['soldiers_done'] is False


def test_planned_low_army_refill_survives_a_transient_egg_first_frame():
    mem = memory()
    p.month_step(screen(120, on='しょうにん'), mem)
    mem['shop']['recruit'] = 'unverified'  # priority withdrawn, plan already made
    p.month_step(screen(120, on='しょうにん'), mem)
    assert not economy.low_army_first(mem['shop'])
    assert monthly._low_army_planned(mem, mem['shop'], screen(120).header)
    assert mem['shop']['soldiers'] == 34 and mem['shop']['soldiers_done'] is False

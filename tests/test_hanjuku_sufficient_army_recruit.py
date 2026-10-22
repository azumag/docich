"""A measured refill skip releases its budget, never the wage/recovery floor."""
import pytest

from docich import hanjuku_policy as policy
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import month_canvas
from test_hanjuku_recruit_roster import NAMES, memory
from test_hanjuku_soldier_receipts import quantity


def stalled_month(count=133, seen='1-7'):
    mem = memory(NAMES[:6], castles=6)
    mem.update(soldiers_seen=count, soldiers_seen_key=seen, gold=86)
    mem['shop'] = {'key': '1-7', 'items': [], 'merchant_done': True,
                   'gold_start': 136, 'soldiers': 56, 'soldiers_done': False,
                   'soldiers_before_recruit': 56, 'soldiers_before_recruit_done': False,
                   'recruit': 'deferred_cash', 'recruit_priority': False,
                   'recruit_reserve': 0, 'recruit_budget_version': 2,
                   'recruit_measured_budget': True, 'reserve': 0, 'egg': 'done'}
    screen = quantity(86)
    screen.header['month'] = 7
    return mem, screen


@pytest.mark.parametrize('count', [50, 133])
def test_skip_reopens_cash_check_then_recruits_from_real_menu(count):
    mem, screen = stalled_month(count)
    assert policy._recruit_spare_after_necessities(mem, mem['shop'], 86)[0] is False
    assert policy.quantity_step(screen, mem, soldiers=True) == [policy.pad('b')]
    assert 'soldier_receipt' not in mem['shop']
    assert mem['shop']['soldiers_before_recruit_done'] is True
    assert mem['shop']['recruit'] == 'pending'
    assert policy.month_step(parse(month_canvas(86, on='しょうぐんぼしゅう')), mem) == [policy.pad('a')]
    assert mem['month_sub']['kind'] == 'recruit'
    assert mem['month_sub']['gold_before'] == 86
    assert mem['shop']['soldiers'] == 0


def test_skip_opens_recruitment_without_a_wage_floor():
    mem, screen = stalled_month()
    policy.quantity_step(screen, mem, soldiers=True)
    for _ in range(3):
        policy.month_step(parse(month_canvas(79, on='しょうぐんぼしゅう')), mem)
    assert mem['shop']['recruit'] == 'opened'
    assert (mem.get('month_sub') or {}).get('kind') == 'recruit'


@pytest.mark.parametrize('gold,repair,opens', [(86, 0, True), (49, 0, False),
                                            (99, 50, False), (100, 50, True)])
def test_current_roster_shortage_reopens_membership_hold_at_real_month_menu(gold, repair, opens):
    mem, screen = stalled_month()
    policy.quantity_step(screen, mem, soldiers=True)
    mem['recruit_payroll_pending'] = ['カシュー']
    mem['recruit_hold'] = {'reason': 'pending_employees', 'status': 'deferred_economy'}
    mem['shop'].update(recruit='deferred_economy', recruit_priority=False,
                       hero_repair_reserve=repair)
    actions = policy.month_step(parse(month_canvas(gold, on='しょうぐんぼしゅう')), mem)
    assert ((mem.get('month_sub') or {}).get('kind') == 'recruit') is opens
    assert (actions == [policy.pad('a')]) is opens
    assert mem['recruit_payroll_pending'] == ['カシュー']
    assert mem['shop']['hero_repair_reserve'] == repair
    assert not mem.get('recruited')  # opening is not a hire or payment receipt


@pytest.mark.parametrize('count,seen', [(49, '1-7'), (133, '1-6'), (None, '1-7')])
def test_unfulfilled_or_unread_army_never_releases_original_budget(count, seen):
    mem, screen = stalled_month(count, seen)
    policy.quantity_step(screen, mem, soldiers=True)
    assert mem['shop']['soldiers_before_recruit_done'] is False
    assert mem['shop']['recruit'] == 'deferred_cash'


def test_skip_does_not_unlock_an_unverified_recovery():
    mem, screen = stalled_month()
    policy.quantity_step(screen, mem, soldiers=True)
    mem['shop']['egg'] = 'unverified'
    policy.month_step(parse(month_canvas(86, on='しょうぐんぼしゅう')), mem)
    assert mem['shop']['recruit'] == 'deferred_egg'
    assert (mem.get('month_sub') or {}).get('kind') != 'recruit'


def test_pending_refill_payment_is_never_replaced_by_a_skip():
    mem, screen = stalled_month()
    mem['shop']['soldier_receipt'] = {'status': 'pending', 'key': '1-7', 'qty': 56}
    policy.quantity_step(screen, mem, soldiers=True)
    assert mem['shop']['soldiers_before_recruit_done'] is False
    assert mem['shop']['recruit'] == 'deferred_cash'
    assert mem['shop']['soldier_receipt']['status'] == 'pending'

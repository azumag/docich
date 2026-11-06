"""Owner rule 2026-11-06: comfortable cash fills the roster while wages stay under income."""
from test_hanjuku_recruit_roster import NAMES, memory

from docich import hanjuku_policy as p
from docich.hanjuku.os_layer.roster import MAX_GENERALS


def comfortable_mem():
    # Chapter 1: 8 castles including the boss barrier -> 7 field castles.
    mem = memory(NAMES[:6], complete=True, castles=2)
    mem['gold'] = 1000
    mem['country_income'] = {'income': 557}
    mem['country_wages'] = {'wages': 141}
    return mem


def test_comfortable_cash_and_income_headroom_fills_the_roster():
    mem = comfortable_mem()
    assert p._recruit_target(mem) == MAX_GENERALS


def test_payroll_at_or_over_income_keeps_the_chart_target():
    mem = comfortable_mem()
    mem['country_income'] = {'income': 140}
    mem['country_wages'] = {'wages': 141}
    assert p._recruit_target(mem) == 2 * 7 + 3
    # Less than one hire's maximum wage of headroom is not headroom either.
    mem['country_income'] = {'income': 150}
    assert p._recruit_target(mem) == 2 * 7 + 3


def test_unobserved_income_or_wages_is_not_headroom():
    mem = memory(NAMES[:6], complete=True, castles=2)
    mem['gold'] = 1000
    assert p._recruit_target(mem) == 2 * 7 + 3
    mem['country_income'] = {'income': 557}
    assert p._recruit_target(mem) == 2 * 7 + 3


def test_cash_below_the_comfort_threshold_keeps_the_owned_castle_target():
    mem = comfortable_mem()
    mem['gold'] = 999
    assert p._recruit_target(mem) == 2 * 2 + 3

def test_a_partial_roster_still_recruits_with_cash_and_wage_room():
    # g1139 2026-11-06 47-12: 12 generals and 9899G, but the incomplete list
    # held recruit_hold=roster_or_demand_unknown for the whole month.
    mem = memory(NAMES[:6], complete=False, castles=2)
    mem['gold'] = 1000
    mem['country_income'] = {'income': 542}
    mem['country_wages'] = {'wages': 78}
    shortage = p._recruit_shortage(mem)
    assert shortage is not None
    assert shortage['count_lower_bound'] == 6
    assert shortage['target'] == MAX_GENERALS


def test_a_partial_roster_without_headroom_or_cash_is_not_recruited():
    mem = memory(NAMES[:6], complete=False, castles=2)
    mem['gold'] = 1000
    mem['country_income'] = {'income': 90}
    mem['country_wages'] = {'wages': 88}
    assert p._recruit_shortage(mem) is None
    mem['country_income'] = {'income': 542}
    mem['country_wages'] = {'wages': 78}
    mem['gold'] = 999
    assert p._recruit_shortage(mem) is None


def test_wage_hires_are_bounded_by_the_measured_payroll():
    mem = memory(NAMES[:6], complete=False, castles=2)
    mem['country_income'] = {'income': 542}
    mem['country_wages'] = {'wages': 78}
    assert p._recruit_wage_hires(mem) == (542 - 78) // 15
    mem['country_wages'] = {'wages': 540}
    assert p._recruit_wage_hires(mem) == 0
    mem.pop('country_income')
    assert p._recruit_wage_hires(mem) == 0

def test_the_recruit_gate_does_not_defer_a_partial_roster_with_headroom():
    mem = memory(NAMES[:6], complete=False, castles=2)
    mem['gold'] = 1000
    mem['country_income'] = {'income': 542}
    mem['country_wages'] = {'wages': 78}
    shop = {'key': '48-1', 'recruit': 'check'}
    assert p._stop_unneeded_recruit(mem, shop) is False
    mem['country_wages'] = {'wages': 540}
    assert p._stop_unneeded_recruit(mem, shop) is True

def test_partial_roster_with_measured_headroom_reaches_cash_gate():
    # g1139: first two recruit gates allowed partial lists, but the final
    # spendable-cash gate still required a complete roster and held the hire.
    mem = memory(NAMES[:6], complete=False, castles=2)
    mem['gold'] = 9899
    mem['country_income'] = {'income': 542}
    mem['country_wages'] = {'wages': 78}
    shop = {'key': '1-7', 'recruit': 'check', 'recruit_priority': True,
            'merchant_done': True, 'items': [], 'soldiers': 0,
            'soldiers_done': True, 'reserve': 0, 'hero_repair_reserve': 0}
    assert p._stop_unneeded_recruit(mem, shop) is False
    assert p._recruit_shortage(mem) is not None
    result, evidence = p._recruit_spare_after_necessities(mem, shop, 9899)
    assert result is True
    assert evidence['generals'] is None
    assert evidence['roster_lower_bound'] == 6
    assert evidence['spare'] == 9899


def test_partial_roster_cash_gate_keeps_wage_and_unknown_roster_safety():
    mem = memory(NAMES[:6], complete=False, castles=2)
    mem['gold'] = 1000
    mem['country_income'] = {'income': 542}
    mem['country_wages'] = {'wages': 540}
    shop = {'key': '1-7', 'recruit': 'check', 'recruit_priority': True,
            'merchant_done': True, 'items': [], 'soldiers': 0,
            'soldiers_done': True, 'reserve': 0, 'hero_repair_reserve': 0}
    assert p._recruit_spare_after_necessities(mem, shop, 1000)[0] is None
    mem['country_wages'] = {'wages': 78}
    mem['gold'] = 999
    assert p._recruit_spare_after_necessities(mem, shop, 999)[0] is None
    mem['gold'] = 1000
    mem['captured'] = ['unknown castle']
    assert p._recruit_spare_after_necessities(mem, shop, 1000)[0] is None


def test_partial_roster_cash_gate_still_subtracts_pending_army_and_repairs():
    mem = memory(NAMES[:6], complete=False, castles=2)
    mem['gold'] = 1000
    mem['country_income'] = {'income': 542}
    mem['country_wages'] = {'wages': 78}
    shop = {'key': '1-7', 'recruit': 'check', 'recruit_priority': True,
            'merchant_done': True, 'items': [], 'soldiers': 900,
            'soldiers_done': False, 'reserve': 0, 'hero_repair_reserve': 60}
    result, evidence = p._recruit_spare_after_necessities(mem, shop, 1000)
    assert result is False
    assert evidence['held'] == 960
    assert evidence['spare'] == 40

def test_previous_partial_roster_cash_hold_is_rechecked_same_month():
    # v481 had already stored deferred_cash. A hotloaded v482 must re-open
    # the monthly recruitment without waiting for the next month.
    mem = memory(NAMES[:6], complete=False, castles=2)
    mem['gold'] = 9899
    mem['country_income'] = {'income': 542}
    mem['country_wages'] = {'wages': 78}
    shop = {'key': '1-7', 'recruit': 'deferred_cash',
            'recruit_priority': False, 'recruit_measured_budget': True,
            'merchant_done': True, 'items': [], 'soldiers': 0,
            'soldiers_done': True, 'reserve': 0, 'hero_repair_reserve': 0}
    p._prioritise_recruit(mem, shop, 9899)
    assert shop['recruit'] == 'check'
    assert shop['recruit_priority'] is True
    assert shop['recruit_batch']['left'] == p._recruit_target(mem) - 6
    assert 'recruit_measured_budget' not in shop


def test_previous_real_cash_shortage_still_keeps_hold():
    mem = memory(NAMES[:6], complete=False, castles=2)
    mem['gold'] = 1000
    mem['country_income'] = {'income': 542}
    mem['country_wages'] = {'wages': 78}
    shop = {'key': '1-7', 'recruit': 'deferred_cash',
            'recruit_priority': False, 'merchant_done': True,
            'items': [], 'soldiers': 990, 'soldiers_done': False,
            'reserve': 0, 'hero_repair_reserve': 0}
    p._prioritise_recruit(mem, shop, 1000)
    assert shop['recruit'] == 'deferred_cash'
    assert not shop['recruit_priority']


def _previous_country_memory(*, month='1-8', old_month='1-7', old_count=12):
    identity = {'game': 'hanjuku-hero', 'runtime_id': 'month-carry',
                'generation': 1, 'lease_id': 'recruit-month-carry'}
    mem = memory(NAMES[:6], complete=False, castles=2)
    mem.update(month=month, tick=110, gold=1500, _run_identity=identity)
    mem['country_general_count'] = {
        'scope': [1, old_month], 'identity': identity,
        'count': old_count, 'tick': 100}
    mem['country_income'] = {'scope': [1, old_month], 'income': 542}
    mem['country_wages'] = {'scope': [1, old_month], 'wages': 78}
    return mem


def test_month_boundary_previous_country_count_arms_recruitment_before_new_status():
    # The national status visit runs after purchases. The old same-month-only
    # check meant every new month started with an unknown headcount.
    mem = _previous_country_memory()
    count, complete, names = p._recruit_headcount(mem)
    assert (count, complete, names) == (12, False, [])
    shortage = p._recruit_shortage(mem)
    assert shortage is not None and shortage['count_lower_bound'] == 12
    assert shortage['target'] == 32
    shop = p._plan(mem, {'year': 1, 'month': 8, 'gold': 1500})
    assert shop is not None
    assert shop['recruit_priority'] is True
    assert shop['recruit_batch']['left'] == 20
    assert shop['recruit'] == 'check'


def test_previous_month_count_passes_real_cash_gate_but_never_claims_complete_roster():
    mem = _previous_country_memory()
    shop = {'key': '1-8', 'recruit': 'check', 'recruit_priority': True,
            'merchant_done': True, 'items': [], 'soldiers': 0,
            'soldiers_done': True, 'reserve': 0, 'hero_repair_reserve': 0}
    spare, evidence = p._recruit_spare_after_necessities(mem, shop, 1500)
    assert spare is True
    assert evidence['generals'] is None
    assert evidence['roster_lower_bound'] == 12


def test_country_count_carries_across_year_boundary_only_one_month():
    mem = _previous_country_memory(month='49-1', old_month='48-12')
    assert p._recruit_headcount(mem)[:2] == (12, False)
    mem['month'] = '49-2'
    assert p._recruit_headcount(mem)[0] is None


def test_previous_country_count_never_crosses_run_chapter_or_future_receipt():
    mem = _previous_country_memory()
    country = mem['country_general_count']
    country['identity'] = {**country['identity'], 'generation': 2}
    assert p._recruit_headcount(mem)[0] is None
    country['identity'] = mem['_run_identity']
    country['scope'] = [2, '1-7']
    assert p._recruit_headcount(mem)[0] is None
    country['scope'] = [1, '1-7']
    country['tick'] = 111
    assert p._recruit_headcount(mem)[0] is None


def test_current_country_receipt_supersedes_provisional_previous_month_count():
    mem = _previous_country_memory()
    mem['country_general_count'].update(scope=[1, '1-8'], count=19)
    assert p._recruit_headcount(mem) == (19, True, [])
    assert p._recruit_shortage(mem)['count_lower_bound'] == 19


def test_stale_full_headcount_does_not_prove_current_month_is_sufficient():
    mem = _previous_country_memory(old_count=32)
    assert not p._recruit_sufficient(mem)
    assert p._recruit_shortage(mem) is None  # Never blindly spend past cap.


def test_deferred_egg_reopens_after_verified_recovery_same_month():
    mem = memory(NAMES[:6], complete=True, castles=2)
    mem['gold'] = 1200
    shop = {'key': '1-7', 'recruit': 'deferred_egg', 'egg': 'done',
            'recruit_priority': False, 'merchant_done': True, 'items': [],
            'soldiers': 0, 'soldiers_done': True, 'reserve': 0,
            'hero_repair_reserve': 0, 'recruit_measured_budget': True}
    p._prioritise_recruit(mem, shop, 1200)
    assert shop['recruit_priority'] is True
    assert shop['recruit'] == 'check'
    assert 'recruit_measured_budget' not in shop
    assert shop['recruit_batch']['left'] == p._recruit_target(mem) - 6


def test_deferred_egg_does_not_retry_while_recovery_is_unverified():
    mem = memory(NAMES[:6], complete=True, castles=2)
    mem['gold'] = 1200
    shop = {'key': '1-7', 'recruit': 'deferred_egg', 'egg': 'unverified',
            'recruit_priority': False, 'merchant_done': True, 'items': [],
            'soldiers': 0, 'soldiers_done': True, 'reserve': 0,
            'hero_repair_reserve': 0}
    p._prioritise_recruit(mem, shop, 1200)
    assert shop['recruit'] == 'deferred_egg'
    assert not shop['recruit_priority']

def test_a_stale_roster_recruits_from_the_measured_garrison_lower_bound():
    # The month's first menu runs before the national status read: the roster
    # still has the previous month's scope, but the garrison proves 12 names.
    mem = memory(NAMES[:6], complete=True, castles=2)
    mem['recruit_roster'] = None
    mem['country_general_count'] = None
    mem['gold'] = 1000
    mem['country_income'] = {'income': 542}
    mem['country_wages'] = {'wages': 78}
    mem['garrison'] = {f'c{i}': [f'g{2*i}', f'g{2*i+1}'] for i in range(6)}
    shortage = p._recruit_shortage(mem)
    assert shortage is not None and shortage['count_lower_bound'] == 12
    assert p._stop_unneeded_recruit(mem, {'key': '48-7', 'recruit': 'check'}) is False


def test_a_stale_roster_without_a_garrison_or_headroom_still_waits():
    mem = memory(NAMES[:6], complete=True, castles=2)
    mem['recruit_roster'] = None
    mem['country_general_count'] = None
    mem['gold'] = 1000
    mem['country_income'] = {'income': 542}
    mem['country_wages'] = {'wages': 78}
    mem['garrison'] = {}
    assert p._recruit_shortage(mem) is None
    assert p._stop_unneeded_recruit(mem, {'key': '48-7', 'recruit': 'check'}) is True

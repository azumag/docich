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

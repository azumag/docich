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

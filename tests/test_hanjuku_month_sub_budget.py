"""owner 2026-11-28: 出撃未着手の月は月一メニューの処理を打ち切る。"""
from docich.hanjuku.flows import month as month_flow


def test_over_budget_when_no_sortie_started_this_month():
    mem = {'month': '4-12', 'sortie_started_month': None,
           'month_sub_opens': {'month': '4-12', 'count': month_flow.MONTH_SUB_OPEN_LIMIT}}
    assert month_flow._month_sub_over_budget(mem) is True


def test_not_over_budget_before_the_limit():
    mem = {'month': '4-12',
           'month_sub_opens': {'month': '4-12', 'count': month_flow.MONTH_SUB_OPEN_LIMIT - 1}}
    assert month_flow._month_sub_over_budget(mem) is False


def test_not_over_budget_after_a_sortie_started():
    mem = {'month': '4-12', 'sortie_started_month': '4-12',
           'month_sub_opens': {'month': '4-12', 'count': 20}}
    assert month_flow._month_sub_over_budget(mem) is False


def test_the_counter_resets_each_month():
    mem = {'month': '5-1', 'month_sub_opens': {'month': '4-12', 'count': 9}}
    month_flow._note_month_sub_open(mem)
    assert mem['month_sub_opens'] == {'month': '5-1', 'count': 1}

"""The month menu gates on counts it read, holds the worst case, pays the quote.

Two numbers used to be one figure: what the menu refuses to spend and what it
asks the screen for. Only the second may be a lower bound - an unread recheck
name is a question, and pricing it there skipped checks the game would have
quoted at nothing (g574 2-9: one unread name priced 50G against 55G on hand) -
while the first must stay an upper bound, or the soldiers spend money a
possibly consumed egg still owns.
"""
import pytest

from docich import hanjuku_policy as policy
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import Canvas, month_canvas


@pytest.fixture(autouse=True)
def plain_month(monkeypatch):
    """A month the chart buys nothing for: the plan is only eggs and soldiers."""
    monkeypatch.setattr(policy, '_charted_purchase_ahead', lambda *a: False)
    monkeypatch.setattr(policy.chart, 'purchase_for', lambda *a: None)


def plan(gold, uses=None, recheck=(), count=40):
    mem = {'chapter': 1, 'egg_uses': dict(uses or {}), 'egg_recheck': list(recheck),
           'soldiers_seen': count, 'soldiers_seen_key': '1-7'}
    return mem, policy._plan(mem, {'year': 1, 'month': 7, 'gold': gold})


def decisions(mem):
    return [r['decision'] for r in mem.get('_records') or []]


def test_read_counts_are_priced_and_unread_recheck_names_are_not():
    mem, shop = plan(300, uses={'キッシュ': 2, 'どうし': 4}, recheck=['どうし', 'ゼウス'], count=60)
    assert set(policy._egg_recovery_targets(mem)) == {'キッシュ', 'ゼウス', 'どうし'}
    assert policy._egg_measured_cost(mem) == 50            # キッシュ only
    assert shop['egg_cost'] == 50                          # what the menu may open on
    # The hold is the worst case: money the refill may not touch.
    assert shop['reserve'] == 150
    assert shop['egg'] == 'pending'
    assert shop['soldiers'] == policy.SOLDIER_CAP


def test_recheck_only_month_gates_on_zero_and_still_opens_the_check():
    mem, shop = plan(55, uses={'どうし': 4}, recheck=['ピスタチオ'])
    assert shop['egg'] == 'check' and shop['egg_cost'] == 0 and shop['reserve'] == 0
    screen = parse(month_canvas(55, on='たまごのかいふく'))
    assert policy._month_extra(screen, mem, shop) == [policy.pad('a')]
    assert mem['month_sub']['kind'] == 'egg'


def test_recheck_only_check_still_yields_to_the_wage_reserve():
    mem, shop = plan(25, uses={'どうし': 4}, recheck=['ピスタチオ'])
    screen = parse(month_canvas(25, on='へいしほじゅう'))
    assert policy._month_extra(screen, mem, shop) is None
    assert shop['egg'] == 'skipped'
    assert 'egg_recover_skip' in decisions(mem)


def test_measured_price_refuses_a_screen_it_cannot_finish():
    mem, shop = plan(70, uses={'キッシュ': 2, 'ゼウス': 1})
    assert shop['egg_cost'] == 100 and shop['egg'] == 'check'
    screen = parse(month_canvas(70, on='たまごのかいふく'))
    assert policy._month_extra(screen, mem, shop) is None
    assert shop['egg'] == 'skipped'


@pytest.mark.parametrize('gold,quote,expected', [
    (100, '2こで100Gになりまんな', 'b'),   # would leave 0G: the wage floor is gone
    (130, '2こで100Gになりまんな', 'a'),
    (60, '1こで50Gになりまんな', 'b'),
    (80, '1こで50Gになりまんな', 'a'),
])
def test_quote_never_spends_the_wage_reserve(gold, quote, expected):
    c = Canvas()
    c.text(72, 15, f'1ねん 7のつき {gold}G')
    c.text(24, 183, quote)
    c.text(184, 183, 'うむッ!')
    c.text(184, 199, 'いかんッ!')
    c.hand(162, 177)
    mem = {'month_sub': {'kind': 'egg', 'gold_before': gold, 'presses': 0,
                         'full_selected': True}}
    assert policy.month_sub_step(parse(c.frame()), mem) == [policy.pad(expected)]
    assert mem['month_sub'].get('stage') == ('recovering' if expected == 'a' else None)


def test_optional_building_still_yields_to_the_unread_worst_case():
    mem, shop = plan(200, uses={'キッシュ': 2}, recheck=['どうし', 'ゼウス'], count=60)
    assert shop['egg_cost'] == 50
    assert policy._chikujou_reserve(mem, shop) >= 3 * policy.EGG_RECOVER_COST

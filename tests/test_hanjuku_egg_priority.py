"""A freshly read sufficient army redirects a cached refill into egg recovery."""
import pytest

from docich import hanjuku_policy as policy
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import month_canvas
from test_hanjuku_soldier_receipts import memory, quantity


def enough_army(gold=179):
    mem = memory(count=None)
    mem['shop']['gold_start'] = gold
    mem['egg_uses'] = {'ココット': 0}
    screen = quantity(gold=gold)
    screen.text += 'へいしすうは60めいです'
    policy.observe_events(screen, mem)
    return mem, screen


def test_count_read_after_planning_cancels_refill_and_opens_recovery_first():
    mem, screen = enough_army()
    assert mem['shop'].get('reserve', 0) == 0  # plan predates the count
    assert policy.month_step(screen, mem) == [policy.pad('b')]
    assert 'soldier_receipt' not in mem['shop']
    assert mem['shop']['egg_priority'] is True
    assert mem['shop']['reserve'] == 50
    assert policy.month_step(parse(month_canvas(179, on='たまごのかいふく', month=8)), mem) == [policy.pad('a')]
    assert mem['month_sub']['kind'] == 'egg'
    assert mem['shop']['soldiers_done'] is False


def test_successful_recovery_releases_reserve_before_optional_refill():
    mem, screen = enough_army()
    policy.month_step(screen, mem)
    policy.month_step(parse(month_canvas(179, on='たまごのかいふく', month=8)), mem)
    mem['month_sub'].update(full_selected=True, quoted_cost=50, left_menu=True)
    actions = policy.month_step(parse(month_canvas(129, on='へいしほじゅう', month=8)), mem)
    assert actions == [policy.pad('a')]
    assert mem['shop']['egg'] == 'done'
    assert mem['shop']['reserve'] == 0
    assert mem['egg_uses'] == {}
    assert mem['shop']['soldiers'] == 24
    assert 'soldier_receipt' not in mem['shop']


def test_paid_egg_estimate_is_not_reserved_again_alongside_hero_repair():
    mem, screen = enough_army(180)
    mem['shop']['hero_repair_reserve'] = 50
    policy.month_step(screen, mem)
    policy.month_step(parse(month_canvas(180, on='たまごのかいふく', month=8)), mem)
    mem['month_sub'].update(full_selected=True, quoted_cost=50, left_menu=True)
    actions = policy.month_step(parse(month_canvas(130, on='へいしほじゅう', month=8)), mem)
    assert actions == [policy.pad('a')]
    assert mem['shop']['reserve'] == 0
    assert mem['shop']['hero_repair_reserve'] == 50
    assert mem['shop']['soldiers'] == 24
    assert mem['shop']['soldiers_done'] is False


@pytest.mark.parametrize('gold', [45, 79])
def test_insufficient_recovery_money_is_never_spent_on_more_soldiers(gold):
    mem, screen = enough_army(gold)
    assert policy.month_step(screen, mem) == [policy.pad('b')]
    policy.month_step(parse(month_canvas(gold, on='へいしほじゅう', month=8)), mem)
    assert mem['shop']['soldiers'] == 0
    assert mem['shop']['soldiers_done'] is True
    assert mem['shop']['reserve'] > 0
    assert mem['shop']['egg'] == 'skipped'
    assert 'soldier_receipt' not in mem['shop']


@pytest.mark.parametrize('count,seen,eggs', [
    (49, '1-8', {'ココット': 0}), (60, '1-7', {'ココット': 0}),
    (None, None, {'ココット': 0}), (60, '1-8', {'ココット': 4}),
])
def test_small_stale_or_unknown_army_and_full_eggs_keep_existing_refill(count, seen, eggs):
    mem = memory(count, seen)
    mem['egg_uses'] = eggs
    assert policy.quantity_step(quantity(), mem, soldiers=True) == [policy.pad('a')]
    assert not mem['shop'].get('egg_priority')


def test_50_is_sufficient_and_cached_skipped_egg_is_reconsidered_once():
    mem = memory(50)
    mem['egg_uses'] = {'ココット': 0}
    mem['shop']['egg'] = 'skipped'
    assert policy.quantity_step(quantity(179), mem, soldiers=True) == [policy.pad('b')]
    assert mem['shop']['egg'] == 'pending'
    mem['shop']['egg'] = 'unverified'
    policy.month_step(parse(month_canvas(179, month=8)), mem)
    assert mem['shop']['egg'] == 'unverified'
    assert mem['shop']['soldiers'] == 0
    assert sum(r['decision'] == 'egg_priority_replan' for r in mem['_records']) == 1


def test_actual_recovery_quote_can_be_lower_than_saved_egg_estimate():
    mem, screen = enough_army(130)
    mem['egg_uses'].update({'ゼウス': 0, 'ヴィーナス': 0})
    policy.month_step(screen, mem)
    assert mem['shop']['egg_cost'] == 150
    assert policy.month_step(parse(month_canvas(130, on='たまごのかいふく', month=8)), mem) == [policy.pad('a')]
    assert mem['month_sub']['kind'] == 'egg'


def test_unknown_recovery_menu_holds_its_money_instead_of_buying_soldiers():
    mem, screen = enough_army()
    policy.month_step(screen, mem)
    missing = parse(month_canvas(179, on='へいしほじゅう', month=8))
    missing.lines = [line for line in missing.lines if 'たまごのかいふく' not in line.known]
    policy.month_step(missing, mem)
    assert mem['shop']['egg'] == 'skipped'
    assert mem['shop']['soldiers_done'] is True
    assert mem['shop']['soldiers'] == 0


def test_failed_priority_recovery_money_is_also_protected_from_recruitment():
    from test_hanjuku_chart_bot import _short_recruit_memory
    mem = _short_recruit_memory()
    mem.update(soldiers_seen=60, soldiers_seen_key='1-7', month='1-7',
               egg_uses={'ココット': 0})
    mem['shop'] = {'key': '1-7', 'items': [], 'merchant_done': True,
                   'gold_start': 100, 'soldiers': 0, 'soldiers_done': True,
                   'recruit': 'check', 'recruit_priority': True, 'recruit_reserve': 20,
                   'egg': 'unverified', 'egg_priority': True, 'reserve': 50}
    policy.month_step(parse(month_canvas(100, on='しょうぐんぼしゅう')), mem)
    assert mem['shop']['recruit'] == 'skipped'
    assert 'month_sub' not in mem
    assert mem['shop']['reserve'] == 50

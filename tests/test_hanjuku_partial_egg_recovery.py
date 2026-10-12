"""Individual quotes/payments retain other generals' quantities and zero latches."""
from copy import deepcopy

import pytest
from docich import hanjuku_policy as p
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import Canvas, MONTH_GRID
from test_hanjuku_recovery_payroll import memory as payroll_memory


def memory(gold=130):
    mem = payroll_memory()
    mem['egg_uses'] = {'どうし': 4, 'ゼウス': 1, 'ヴィーナス': 1, 'ココット': 0}
    mem['month_sub'] = {'kind': 'egg', 'gold_before': gold, 'presses': 0, 'key': '1-6', 'left_menu': True}
    mem['shop'] = {'key': '1-6', 'egg': 'opened', 'egg_priority': True, 'reserve': 150,
                   'egg_cost': 150, 'soldiers': 50, 'soldiers_done': False}
    mem['egg_recheck'] = ['ゼウス', 'ココット']
    return mem


def recovery_list(actor='ココット', quantity='0', selected=95):
    c = Canvas()
    c.text(32, 47, 'ぜんかいふく')
    c.text(32, 71, 'ゼウス'); c.text(104, 71, '1')
    c.text(32, 95, actor); c.text(104, 95, quantity)
    c.hand(10, selected - 6)
    return parse(c.frame())


def quote(gold=130, quantity=1, cost=50, month=6):
    c = Canvas()
    c.text(48, 15, f'1ねん {month}のつき {gold}G')
    c.text(24, 183, f'{quantity}こで{cost}Gになりまんな')
    c.text(184, 183, 'うむッ!'); c.text(184, 199, 'いかんッ!')
    c.hand(162, 177)
    return parse(c.frame())


def ready_menu(gold=80):
    c = Canvas(); c.text(48, 15, f'1ねん 6のつき {gold}G')
    for label, (x, y) in MONTH_GRID.items(): c.text(x, y, label)
    x, y = MONTH_GRID['しょうにん']; c.hand(x-22, y-6)
    return parse(c.frame())


def selected_memory(gold=130):
    mem = memory(gold)
    assert p._egg_recovery_step(recovery_list(), mem, mem['month_sub']) == [p.pad('a')]
    assert mem['month_sub']['individual_actor'] == 'ココット'
    return mem


def test_lowest_actual_quantity_wins_over_earlier_one_use_row():
    mem = memory(); before = deepcopy(mem['egg_uses'])
    assert p._egg_recovery_step(recovery_list(selected=47), mem, mem['month_sub']) == [p.pad('down')]
    assert not mem['month_sub'].get('individual_selected')
    assert p._egg_recovery_step(recovery_list(), mem, mem['month_sub']) == [p.pad('a')]
    assert mem['month_sub']['individual_actor'] == 'ココット'
    assert mem['egg_uses'] == before


def test_individual_quote_does_not_release_zero_or_claim_completion():
    mem = selected_memory(); before = deepcopy(mem['egg_uses'])
    assert p._egg_recovery_step(quote(), mem, mem['month_sub']) == [p.pad('a')]
    assert mem['month_sub']['quoted_cost'] == 50
    assert mem['egg_uses'] == before and p._own_egg_empty(mem, {'ally': 'ココット'})


@pytest.mark.parametrize('change', ['multi', 'wrong_price', 'short', 'month', 'run', 'chapter', 'missing_selection'])
def test_quote_needs_exact_one_egg_price_current_owner_and_reserved_payroll(change):
    mem = selected_memory(); screen = quote()
    if change == 'multi': screen = quote(quantity=2, cost=100)
    elif change == 'wrong_price': screen = quote(cost=49)
    elif change == 'short': screen = quote(gold=57)  # actual full payroll8 -> needs58
    elif change == 'month': screen = quote(month=5)
    elif change == 'run': mem['_run_identity']['lease_id'] = 'other'
    elif change == 'chapter': mem['chapter'] = 2
    else: mem['month_sub']['individual_selected'] = False
    assert p._egg_recovery_step(screen, mem, mem['month_sub']) == [p.pad('b')]
    assert not mem['month_sub'].get('quoted_cost')
    assert mem['egg_uses']['ココット'] == 0


def test_paid_individual_keeps_other_counts_and_reserved_unpaid_eggs():
    mem = selected_memory()
    assert p._egg_recovery_step(quote(), mem, mem['month_sub']) == [p.pad('a')]
    assert p._finish_month_sub(ready_menu(), mem, mem['shop'])
    assert mem['egg_uses'] == {'どうし': 4, 'ゼウス': 1, 'ヴィーナス': 1}
    assert mem['egg_recheck'] == ['ゼウス']
    assert mem['shop']['egg_paid_actors'] == ['ココット']
    assert mem['shop']['egg'] == 'check' and mem['shop']['reserve'] == 100
    assert mem['shop']['soldiers'] == 0
    assert p._own_egg_empty(mem, {'ally': 'ココット'})
    mem['egg_uses']['ココット'] = 4  # only the later actual named reading releases zero
    assert not p._own_egg_empty(mem, {'ally': 'ココット'})


def test_absent_payment_retains_all_counts_and_has_no_paid_actor():
    mem = selected_memory()
    p._egg_recovery_step(quote(), mem, mem['month_sub'])
    assert p._finish_month_sub(ready_menu(gold=130), mem, mem['shop'])
    assert mem['egg_uses']['ココット'] == 0
    assert not mem['shop'].get('egg_paid_actors')
    assert mem['shop']['egg'] == 'unverified'


def test_real_full_quote_shortfall_rechecks_once_for_an_individual():
    mem = memory(); sub = mem['month_sub']; sub['full_selected'] = True
    assert p._egg_recovery_step(quote(quantity=4, cost=200), mem, sub) == [p.pad('b')]
    assert sub['full_quote_short'] and not sub.get('aborted')
    assert p._egg_recovery_step(recovery_list(), mem, sub) == [p.pad('a')]
    assert sub['individual_actor'] == 'ココット'


def test_full_affordable_keeps_full_recovery():
    mem = memory(200)
    assert p._egg_recovery_step(recovery_list(selected=47), mem, mem['month_sub']) == [p.pad('a')]
    assert mem['month_sub']['full_selected'] is True
    assert not mem['month_sub'].get('individual_actor')


def test_paid_actor_not_reselected_but_other_low_use_actor_remains():
    mem = memory(); mem['shop']['egg_paid_actors'] = ['ココット']
    assert p._egg_recovery_step(recovery_list(selected=71), mem, mem['month_sub']) == [p.pad('a')]
    assert mem['month_sub']['individual_actor'] == 'ゼウス'


@pytest.mark.parametrize('change', ['unknown_qty', 'unknown_name', 'unbound_run', 'full_one_shot'])
def test_unknown_or_full_rows_are_never_paid_as_partial_recovery(change):
    mem = memory(); screen = recovery_list(quantity='?')
    if change == 'unknown_name': screen = recovery_list(actor='だれか')
    elif change == 'unbound_run': mem.pop('_run_identity')
    elif change == 'full_one_shot':
        screen = recovery_list(quantity='1'); mem.setdefault('egg_types', {})['ココット'] = 'いっぱつエッグ'
    # Zeus remains an eligible one-use alternative except the unbound run.
    if change != 'unbound_run':
        mem['shop']['egg_paid_actors'] = ['ゼウス']
    assert not p._individual_egg_candidate(screen, mem, mem['month_sub'])


@pytest.mark.parametrize('gold,opens', [(57, False), (58, True), (80, True)])
def test_entry_can_afford_one_recovery_with_complete_actual_payroll(gold, opens):
    mem = memory(gold); mem.pop('month_sub'); mem['shop']['egg'] = 'check'
    actions = p._month_extra(ready_menu(gold), mem, mem['shop'], egg_only=True)
    assert bool(actions) is opens
    assert (mem['shop']['egg'] == 'skipped') is (not opens)


def test_second_affordable_actor_can_recover_without_repaying_the_first():
    mem = selected_memory(); p._egg_recovery_step(quote(), mem, mem['month_sub'])
    p._finish_month_sub(ready_menu(), mem, mem['shop'])
    mem['month_sub'] = {'kind': 'egg', 'gold_before': 80, 'presses': 0, 'key': '1-6', 'left_menu': True}
    mem['shop']['egg'] = 'opened'
    assert p._egg_recovery_step(recovery_list(selected=71), mem, mem['month_sub']) == [p.pad('a')]
    assert mem['month_sub']['individual_actor'] == 'ゼウス'
    assert p._egg_recovery_step(quote(gold=80), mem, mem['month_sub']) == [p.pad('a')]
    assert p._finish_month_sub(ready_menu(gold=30), mem, mem['shop'])
    assert mem['shop']['egg_paid_actors'] == ['ココット', 'ゼウス']
    assert mem['shop']['reserve'] == 50 and mem['egg_uses']['ヴィーナス'] == 1
    assert mem['shop']['soldiers'] == 0
    assert not p._month_extra(ready_menu(30), mem, mem['shop'], egg_only=True)


def test_partial_payment_requires_its_single_quote_even_if_another_debit_matches():
    mem = selected_memory(); sub = mem['month_sub']; sub.update(quoted_cost=100, stage='recovering')
    assert p._finish_month_sub(ready_menu(gold=30), mem, mem['shop'])
    assert not mem['shop'].get('egg_paid_actors') and mem['egg_uses']['ココット'] == 0


def test_fading_quote_after_individual_confirm_does_not_resubmit_or_drop_ownership():
    mem = selected_memory(); p._egg_recovery_step(quote(), mem, mem['month_sub'])
    sub = mem['month_sub']
    for gold in [130, 80]:
        assert p._egg_recovery_step(quote(gold=gold), mem, sub) == []
        assert not sub.get('aborted') and sub['stage'] == 'recovering'
    assert p._finish_month_sub(ready_menu(), mem, mem['shop'])
    assert mem['shop']['egg_paid_actors'] == ['ココット']


def test_actual_zero_list_overrules_stale_positive_and_survives_payment():
    mem = memory(); mem['egg_uses']['ココット'] = 1
    assert p._egg_recovery_step(recovery_list(), mem, mem['month_sub']) == [p.pad('a')]
    assert mem['egg_uses']['ココット'] == 0 and p._own_egg_empty(mem, {'ally': 'ココット'})
    p._egg_recovery_step(quote(), mem, mem['month_sub'])
    p._finish_month_sub(ready_menu(), mem, mem['shop'])
    assert p._own_egg_empty(mem, {'ally': 'ココット'})


def test_duplicate_named_quantities_cannot_bind_an_individual_selection():
    from docich.hanjuku_font import TextLine
    mem = memory(); screen = recovery_list()
    screen.lines.append(TextLine(119, tuple((32+8*i, ch) for i, ch in enumerate('ココット')) + ((104, '0'),)))
    assert p._egg_recovery_step(screen, mem, mem['month_sub']) == []
    assert not mem['month_sub'].get('individual_selected')

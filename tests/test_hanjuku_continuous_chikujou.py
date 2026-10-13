"""Continue from actual castle-list receipts without reopening the month menu."""
from copy import deepcopy

import pytest
from docich import hanjuku_policy as p
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import Canvas
from test_hanjuku_multi_chikujou import HOME, OTHER, memory, open_build, quote, result, month
from test_hanjuku_recovery_payroll import ID


def castle_list(gold=145, selected=HOME, names=(HOME, OTHER), month_number=7):
    c = Canvas(); c.text(48, 15, f'1ねん {month_number}のつき {gold}G')
    for i, name in enumerate(names): c.text(168, 39 + 16*i, name)
    c.text(24, 183, 'どのしろを ぞうちく なさいますか?')
    c.hand(146, 33 + 16*names.index(selected))
    return parse(c.frame())


def paid_home(gold=150, cost=5):
    mem = memory(); mem.update(month='1-7', _run_identity=deepcopy(ID))
    open_build(mem, gold)
    assert p.month_sub_step(castle_list(gold), mem) == [p.pad('a')]
    assert p.month_sub_step(quote(HOME, gold, cost), mem) == [p.pad('a')]
    assert p.month_sub_step(result(HOME), mem) == [p.pad('a')]
    return mem


def test_next_castle_is_selected_in_the_same_list_with_distinct_receipts():
    mem = paid_home(); previous = mem['month_sub']['presses']
    assert p.month_sub_step(castle_list(), mem) == [p.pad('down')]
    sub = mem['month_sub']
    assert sub['gold_before'] == 145 and sub['presses'] == previous + 1
    assert sub['rows_tried'] == [HOME] and not sub.get('confirm_sent')
    assert mem['shop']['chikujou_upgrades'] == [dict(castle=HOME,cost=5,gold_before=150,gold_after=145)]
    assert p.month_sub_step(castle_list(selected=OTHER), mem) == [p.pad('a')]
    assert p.month_sub_step(quote(OTHER, 145, 9), mem) == [p.pad('a')]
    assert p.month_sub_step(result(OTHER), mem) == [p.pad('a')]
    # The entire batch is done; one final B closes it.
    assert p.month_sub_step(castle_list(136, selected=OTHER), mem) == [p.pad('b')]
    assert p._finish_month_sub(month(136), mem, mem['shop'])
    assert [r['castle'] for r in mem['shop']['chikujou_upgrades']] == [HOME, OTHER]
    assert mem['shop']['chikujou'] == 'done'
    assert len([r for r in mem['_records'] if r['decision']=='month_sub_open']) == 1
    receipts = [r for r in mem['_records'] if r['decision']=='chikujou']
    assert [r['observed_metric']['receipt_screen'] for r in receipts] == ['castle_list','month_menu']


@pytest.mark.parametrize('gold', [150, 144])
def test_stale_or_wrong_debit_never_selects_another_castle(gold):
    mem = paid_home(); sub = mem['month_sub']
    for _ in range(p.CHIKUJOU_WAIT_LIMIT - 1):
        assert p.month_sub_step(castle_list(gold), mem) == []
    assert p.month_sub_step(castle_list(gold), mem) == [p.pad('b')]
    assert sub['aborted'] and not mem['shop'].get('chikujou_upgrades')


def test_lagging_list_balance_can_be_rechecked_without_exiting():
    mem = paid_home()
    assert p.month_sub_step(castle_list(150), mem) == []
    assert p.month_sub_step(castle_list(145), mem) == [p.pad('down')]
    assert mem['shop']['chikujou_upgrades'][0]['gold_after'] == 145


@pytest.mark.parametrize('bad', ['month', 'missing_gold', 'hand', 'duplicate', 'layout', 'identity', 'changed_run', 'changed_chapter', 'unbound_old'])
def test_ambiguous_or_different_scope_returns_without_a_new_purchase(bad):
    mem = paid_home(); screen = castle_list()
    if bad == 'month': screen.header['month'] = 8
    elif bad == 'missing_gold': screen.header.pop('gold')
    elif bad == 'hand': screen.hand = None
    elif bad == 'identity': mem.pop('_run_identity')
    elif bad == 'changed_run': mem['_run_identity']['lease_id']='other'
    elif bad == 'changed_chapter': mem['chapter']=2
    elif bad == 'unbound_old': mem['month_sub'].pop('chikujou_identity')
    elif bad == 'duplicate': screen.lines.append(screen.lines[1])
    else:
        from dataclasses import replace
        screen.lines = [replace(l, cells=tuple((x-8,ch) for x,ch in l.cells)) if l.y==39 else l for l in screen.lines]
    assert p.month_sub_step(screen, mem) == [p.pad('b')]
    assert not mem['shop'].get('chikujou_upgrades')
    assert mem['month_sub']['chosen'] == HOME


def test_original_total_input_bound_is_not_reset_between_castles():
    mem = paid_home(); mem['month_sub']['presses'] = p.CHIKUJOU_SUB_LIMIT - 1
    assert p.month_sub_step(castle_list(), mem) == [p.pad('down')]
    assert mem['month_sub']['presses'] == p.CHIKUJOU_SUB_LIMIT
    assert p.month_sub_step(castle_list(selected=OTHER), mem) == [p.pad('b')]
    assert mem['month_sub']['aborted'] and not mem['month_sub'].get('confirm_sent')


@pytest.mark.parametrize('hold', ['egg', 'repair', 'recruit'])
def test_unfinished_necessities_end_the_batch_once_without_spending_their_reserve(hold):
    mem = paid_home(90, 10)
    if hold=='egg': mem['shop'].update(egg='unverified', reserve=50, egg_cost=50)
    elif hold=='repair': mem['shop']['hero_repair_reserve'] = 50
    else: mem['shop'].update(recruit='check', recruit_reserve=50)
    assert p.month_sub_step(castle_list(80), mem) == [p.pad('b')]
    assert not mem['shop'].get('chikujou_upgrades')
    assert p._finish_month_sub(month(80), mem, mem['shop'])
    assert mem['shop']['chikujou']=='done'


def test_continuation_keeps_known_large_payroll_instead_of_the_30g_fallback():
    from test_hanjuku_partial_egg_recovery import large_payroll_memory
    mem = paid_home(72, 10)
    wages = large_payroll_memory()
    mem.update(tick=wages['tick'], recruit_roster=deepcopy(wages['recruit_roster']))
    mem['recruit_roster']['month']='1-7'
    assert p._egg_recovery_wage_floor(mem)==62
    assert p.month_sub_step(castle_list(62), mem)==[p.pad('b')]
    assert p._finish_month_sub(month(62),mem,mem['shop'])
    assert mem['shop']['chikujou']=='done'


def test_old_castle_quote_cannot_confirm_the_new_selection_after_continuation():
    mem = paid_home()
    p.month_sub_step(castle_list(), mem)
    assert p.month_sub_step(castle_list(selected=OTHER), mem)==[p.pad('a')]
    assert p.month_sub_step(quote(HOME,145,5),mem)==[]
    assert not mem['month_sub'].get('confirm_sent')
    assert p.month_sub_step(quote(OTHER,145,9),mem)==[p.pad('a')]


def test_unobserved_result_cannot_be_replaced_by_a_matching_balance_alone():
    mem=paid_home(); mem['month_sub']['upgraded']=False
    assert p.month_sub_step(castle_list(),mem)==[p.pad('b')]
    assert not mem['shop'].get('chikujou_upgrades')
    assert mem['month_sub']['chosen']==HOME

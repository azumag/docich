"""Issue #2366: a soldier refill is never cancelled silently or taken as bought.

g809 (v227, 2026-10-16) 5-7 and 6-2: the free army-count inspection had just
started (planned refill 16 and 50) when the monthly roster survey took the
next ready menu frame to メインメニュー. The inspection treated that main menu
as another input owner, marked itself unverified and ended the month's refill
without a record; 6-2 then paid a recruit (206G->150G) and chikujou (->145G)
and closed with no soldiers bought (frames decision-004/009, frame-070/074).
The frames below are synthetic compositions of the measured glyphs and hands.
"""
from copy import deepcopy

import pytest

from docich import hanjuku_commentary as commentary, hanjuku_policy as policy
from docich.hanjuku.flows import month as monthly
from docich.hanjuku.strategy import economy
from docich.hanjuku_bot import decide
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import Canvas, MONTH_GRID
from test_hanjuku_soldier_receipts import quantity

IDENTITY = {'game': 'hanjuku-hero', 'runtime_id': 'g809-0b75a42c', 'generation': 809,
            'lease_id': '50ce9f46-4235-4ad5-82d8-d5846dba7f29'}


def canvas(gold=96, on='しょうにん'):
    c = Canvas()
    c.text(48, 15, f'5ねん 7のつき {gold}G')
    for label, (x, y) in MONTH_GRID.items():
        c.text(x, y, label)
    if on:
        x, y = MONTH_GRID[on]
        c.hand(x - 22, y - 6)
    return c


def month(gold=96, on='しょうにん'):
    return canvas(gold, on).frame()


def count_intro(count, gold=96):
    c = canvas(gold, 'へいしほじゅう')
    c.text(24, 183, f'げんざいわがぐんのへいしすうは{count}めいですが')
    c.text(24, 199, 'なんめいほじゅうされますかな?')
    return c.frame()


def main_menu(gold=96):
    c = canvas(gold, None)
    c.text(48, 127, 'しょうぐん')
    c.text(48, 143, 'ステータス')
    c.text(48, 159, 'システム')
    c.hand(26, 121)
    return c.frame()


def new_month():
    """5-6 -> 5-7 as in g809: last month's 39 is history, not this month's count."""
    mem = {'chapter': 1, 'month': '5-6', 'gold': 88, 'orders': {}, 'picked': [],
           'stats': {'battles': 10, 'wins': 5}, '_run_identity': deepcopy(IDENTITY),
           'soldiers_seen': 39, 'soldiers_seen_key': '5-6'}
    actions, state = step({'policy': mem, 'screen_kind': 'unknown'}, month(on=None))
    assert actions == []  # the menu hand has not appeared yet
    assert state['policy']['army_count_check']['stage'] == 'opening'
    assert state['policy']['shop']['soldiers'] > 0
    return state


def step(state, frame):
    return decide(frame, state, run_identity=deepcopy(IDENTITY))


def decisions(state):
    return [r['decision'] for r in state.get('_records') or []]


def outcomes(mem):
    return [r for r in mem.get('_records') or [] if r['decision'] == 'soldier_refill_outcome']


def test_monthly_roster_survey_waits_for_the_free_army_count_check():
    state = new_month()
    actions, state = step(state, month())
    assert 'house_scan_started' not in decisions(state)
    assert 'house' not in state['policy']
    assert actions == [policy.pad('down')]  # しょうにん -> へいしほじゅう, not メインメニュー
    actions, state = step(state, month(on='へいしほじゅう'))
    assert actions == [policy.pad('a')]
    assert state['policy']['army_count_check']['stage'] == 'requested'
    assert 'house' not in state['policy']
    actions, state = step(state, count_intro(16))
    assert state['policy']['soldiers_seen'] == 16
    assert actions == [policy.pad('a')]  # opens the editor for its count only
    assert 'house' not in state['policy']


def measured(count):
    state = new_month()
    for frame in (month(), month(on='へいしほじゅう'), count_intro(count)):
        _, state = step(state, frame)
    assert state['policy']['soldiers_seen'] == count
    # The inspection's own B closed the editor; the month menu is back.
    state['policy']['army_count_check'].update(stage='closing', result='measured')
    return state


def test_short_army_refill_settles_before_the_survey_starts():
    # The month opened with the roster unknown, so its plan already put the
    # soldiers before any recruit (recruit_before_soldiers: false in g809).
    state = measured(16)
    actions, state = step(state, month(on='へいしほじゅう'))
    assert actions == [policy.pad('a')]
    assert state['policy']['army_count_check']['refill_requested'] is True
    actions, state = step(state, month(on='へいしほじゅう'))  # dialogue not drawn yet
    assert actions == [] and 'house' not in state['policy']
    shop = state['policy']['shop']
    qty = shop['soldiers']
    shop['soldier_receipt'] = {'status': 'paid', 'key': '5-7', 'qty': qty, 'gold_before': 96,
                               'soldiers_before': 16}
    shop['soldiers_done'] = True
    actions, state = step(state, month(gold=96 - qty))
    assert 'house_scan_started' in decisions(state)  # the survey is delayed, not dropped
    assert not outcomes(state)


def test_sufficient_army_lets_the_survey_start_after_the_count():
    state = measured(60)
    for _ in range(3):
        actions, state = step(state, month())
        if 'house_scan_started' in decisions(state):
            break
    else:
        raise AssertionError('monthly survey never started after the measured count')
    mem = state['policy']
    assert mem['army_count_check']['stage'] == 'measured'
    assert not mem['shop'].get('soldier_receipt') and not outcomes(state)


@pytest.mark.parametrize('kind', ['map', 'battle', 'main_menu'])
def test_interrupted_inspection_records_one_cancelled_outcome(kind):
    state = new_month()
    mem = state['policy']
    mem.pop('_records', None)
    screen = parse(month())
    screen.kind = kind
    assert monthly.army_count_pending_step(screen, mem) is None
    assert mem['army_count_check']['stage'] == 'unverified'
    assert mem['shop']['soldiers_done'] is True and not mem['shop'].get('soldier_receipt')
    [rec] = outcomes(mem)
    assert rec['resulting_event'] == 'cancelled'
    assert rec['observed_metric']['screen'] == kind
    assert rec['observed_metric']['planned'] > 0
    monthly.army_count_pending_step(screen, mem)
    assert len(outcomes(mem)) == 1  # one ending per month, not per frame


def test_refill_open_attempt_limit_is_not_a_purchase():
    state = new_month()
    mem = state['policy']
    mem.update(soldiers_seen=16, soldiers_seen_key='5-7',
               soldiers_seen_scope=economy._soldier_count_scope(mem, parse(month()).header))
    mem['army_count_check'].update(stage='measured', result='measured', refill_requested=True)
    mem.pop('_records', None)
    screen = parse(month(on='へいしほじゅう'))
    for _ in range(3):
        assert monthly._month_army_count_check(screen, mem, mem['shop']) == []
    assert monthly._month_army_count_check(screen, mem, mem['shop']) == [policy.pad('b')]
    [rec] = outcomes(mem)
    assert rec['resulting_event'] == 'attempt_limit'
    assert mem['shop']['soldiers_done'] is True and not mem['shop'].get('soldier_receipt')


def test_unconfirmed_editor_payment_ends_unverified_not_paid():
    identity = dict(IDENTITY)
    mem = {'chapter': 1, 'month': '1-8', 'gold': 79, '_run_identity': identity,
           'soldiers_seen': 20, 'soldiers_seen_key': '1-8', 'soldiers_seen_scope': [identity, 1, '1-8'],
           'army_count_check': {'scope': [identity, 1, '1-8'], 'stage': 'measured', 'result': 'measured'},
           'shop': {'key': '1-8', 'items': [], 'merchant_done': True, 'soldiers': 24,
                    'soldiers_done': False, 'gold_start': 79, 'egg': None, 'recruit': None,
                    'chikujou': 'skipped'}}
    assert policy.quantity_step(quantity(), mem, soldiers=True) == [policy.pad('a')]
    for _ in range(3):
        assert policy.quantity_step(quantity(), mem, soldiers=True) == []
    assert policy.quantity_step(quantity(), mem, soldiers=True) == [policy.pad('b')]
    assert mem['shop']['soldier_receipt']['status'] == 'unverified'
    [rec] = outcomes(mem)
    assert rec['resulting_event'] == 'unverified'
    assert not any(r['decision'] == 'soldier_refill_receipt' and r['resulting_event'] == 'paid'
                   for r in mem['_records'])


def test_exit_refusal_bound_names_the_attempt_limit():
    c = Canvas()
    c.text(48, 15, '2ねん 4のつき 70G')
    c.text(48, 47, 'しょうにん')
    c.text(24, 183, 'よろしいですかな?')
    c.text(184, 183, 'うむッ!')
    c.text(184, 199, 'いかんッ!')
    c.hand(162, 177)
    identity = dict(IDENTITY)
    mem = {'chapter': 1, 'month': '2-4', '_run_identity': identity, 'soldiers_seen': 0,
           'soldiers_seen_key': '2-4', 'soldiers_seen_scope': [identity, 1, '2-4']}
    for _ in range(10):
        mem['month_exit'] = True
        shop = mem.setdefault('shop', {})
        shop.update(key='2-4', soldiers=24, soldiers_done=False, merchant_done=True, items=[])
        actions = policy.month_step(parse(c.frame()), mem)
        if actions[0]['buttons'] == ['a']:
            break
    [rec] = outcomes(mem)
    assert rec['resulting_event'] == 'attempt_limit'


def _closing_month(count, gold, **shop):
    identity = dict(IDENTITY)
    header = {'year': 5, 'month': 8, 'gold': gold}
    mem = {'chapter': 1, 'month': '5-8', 'gold': gold, '_run_identity': identity,
           'soldiers_seen': count, 'soldiers_seen_key': '5-8',
           'soldiers_seen_scope': [identity, 1, '5-8']}
    mem['shop'] = {'key': '5-8', 'items': [], 'merchant_done': True, 'gold_start': gold,
                   'soldiers': 0, 'soldiers_done': True, 'egg': 'skipped', 'reserve': 0,
                   'recruit': 'done', 'recruit_reserve': 0, 'chikujou': 'done', **shop}
    return mem, header


def test_month_done_names_insufficient_funds_for_a_short_army():
    # g809 5-8: 5 soldiers at 65G with the hero's 50G house repair held.
    mem, header = _closing_month(5, 65, hero_repair_reserve=50)
    summary = monthly._refill_month_outcome(mem, mem['shop'], header)
    assert summary['outcome'] == 'insufficient_funds' and summary['soldiers'] == 5
    [rec] = outcomes(mem)
    assert rec['resulting_event'] == 'insufficient_funds'
    assert rec['observed_metric']['spendable'] == 0
    assert rec['observed_metric']['repair'] == 50


def test_month_done_paid_and_sufficient_months_add_no_failure_record():
    mem, header = _closing_month(50, 54)
    mem['shop']['soldier_receipt'] = {'status': 'paid', 'key': '5-8', 'qty': 29,
                                      'gold_before': 83, 'soldiers_before': 21}
    summary = monthly._refill_month_outcome(mem, mem['shop'], header)
    assert summary == {'outcome': 'paid', 'qty': 29, 'soldiers': 50}
    mem, header = _closing_month(60, 200)
    assert monthly._refill_month_outcome(mem, mem['shop'], header)['outcome'] == 'not_needed'
    assert not outcomes(mem)


def test_month_done_keeps_the_first_recorded_ending():
    mem, header = _closing_month(16, 26)
    economy.refill_outcome(mem, mem['shop'], 'cancelled', '割り込み', screen='map')
    summary = monthly._refill_month_outcome(mem, mem['shop'], header)
    assert summary['outcome'] == 'cancelled'
    assert len(outcomes(mem)) == 1


def test_month_plan_speaks_a_plan_and_an_unpaid_ending_is_never_a_purchase():
    _, text = commentary.compose({'decision': 'month_plan', 'month': '5-7', 'gold': 96,
                                  'plan': {'cards': [], 'soldiers': 16}})
    assert '兵士を16人補充する予定です' in text
    for outcome in ('cancelled', 'unverified', 'attempt_limit'):
        rec = {'decision': 'soldier_refill_outcome', 'resulting_event': outcome,
               'observed_metric': {'outcome': outcome, 'planned': 16}}
        _, text = commentary.compose(rec)
        assert text and '補充しました' not in text and '補充できました' not in text
        assert commentary.evidence_kind(rec) == 'observation'
    assert 'soldier_refill_outcome' in commentary.SPOKEN
    quiet = {'decision': 'soldier_refill_outcome', 'resulting_event': 'insufficient_funds',
             'observed_metric': {'outcome': 'insufficient_funds', 'planned': 0}}
    assert commentary.compose(quiet)[1] is None

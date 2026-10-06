"""Goninja's 1000G floor survives monthly owners and the no-input fallback.

Synthetic native glyphs/cursors only; no game or input transport is involved.
"""
from dataclasses import replace
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from docich import hanjuku_policy as policy
from docich.hanjuku_bot import NO_INPUT_HOLD_MAX, decide
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import Canvas, GREEN, MONTH_GRID, _goninja_offer, paid_recruit_screen
from test_hanjuku_month_foreground import monthly_canvas, monthly_memory


def offer(gold, *, background=False, cursor='accept', both_choices=True,
          prompt='うちとおすか?'):
    canvas = monthly_canvas() if background else Canvas((50, 30, 10))
    # Replace the monthly fixture's header. None must not reuse its old 59G.
    for y in range(0, 32):
        for x in range(256):
            canvas.put(x, y, (50, 30, 10))
    if gold is not None:
        canvas.text(16, 15, f'2ねん11のつき{gold}G')
    for y in range(128, 207):
        for x in range(18, 236):
            canvas.put(x, y, GREEN)
    canvas.text(24, 151, '50Gで てきの ティピオカ')
    canvas.text(24, 167, prompt)
    canvas.text(184, 183, 'うむッ!')
    if both_choices:
        canvas.text(184, 199, 'いかんッ!')
    if cursor in ('accept', 'ambiguous'):
        canvas.hand(162, 177)
    if cursor in ('decline', 'ambiguous'):
        canvas.hand(162, 193)
    return canvas.frame()


def owned_state(owner):
    mem = monthly_memory()
    # Old readable funds never substitute for this offer's unreadable header.
    mem['gold'] = 7572
    mem['tick'] = 100
    mem['garrison'] = {'アルマムーン': ['どうし', 'ゼウス']}
    mem['recruit_roster'] = {'chapter': 1, 'month': '2-11', 'tick': 95,
                             'names': ['どうし', 'ゼウス'],
                             'wages': {'どうし': 0, 'ゼウス': 4}, 'complete': True}
    mem['castle_income'] = {'アルマムーン': {'chapter': 1, 'month': '2-11',
                                              'tick': 95, 'income': 30}}
    mem['shop'] = {'key': '2-11', 'egg': 'done', 'items': [],
                   'merchant_done': True, 'soldiers': 0, 'soldiers_done': True}
    if owner == 'recruit':
        mem['month_sub'] = {'kind': 'recruit', 'gold_before': 59, 'presses': 0,
                            'key': '2-11', 'left_menu': True}
    elif owner == 'month_exit':
        mem['month_exit'] = True
    return {'policy': mem}


@pytest.mark.parametrize('gold,choice,variant', [
    (999, 'いかんッ!', 'decline_goninja_below_floor'),
    (1000, 'うむッ!', 'accept_goninja'),
    (1001, 'うむッ!', 'accept_goninja'),
    (None, 'いかんッ!', 'decline_goninja_unreadable'),
])
@pytest.mark.parametrize('cursor_y', [177, 193])
def test_cash_floor_at_policy_boundary(gold, choice, variant, cursor_y):
    screen = parse(_goninja_offer(gold, hand_y=cursor_y).frame())
    mem = {'chapter': 3, 'gold': 7572, '_records': []}
    actions = policy.yes_no_step(screen, mem)
    selected = 'うむッ!' if cursor_y == 177 else 'いかんッ!'
    expected = 'a' if choice == selected else 'down' if cursor_y == 177 else 'up'
    assert actions == [policy.pad(expected)]
    if expected == 'a':
        record = mem['_records'][-1]
        assert record['choice'] == choice
        assert record['strategy_variant'] == variant
        assert record['observed_metric'] == (
            {'gold': gold, 'minimum_gold': 1000} if gold is not None else None)


@pytest.mark.parametrize('gold', [None, True, False, -1, 1000.0, '1000'])
def test_only_nonnegative_exact_integer_gold_is_readable(gold):
    screen = replace(parse(_goninja_offer(1000, hand_y=193).frame()),
                     header={'chapter': None, 'year': 5, 'month': 10, 'gold': gold})
    mem = {'chapter': 3, 'gold': 7572, '_records': []}
    assert policy.yes_no_step(screen, mem) == [policy.pad('a')]
    assert mem['_records'][-1]['strategy_variant'] == 'decline_goninja_unreadable'


def test_cash_floor_does_not_depend_on_chart_prices_or_reserves():
    mem = {'chapter': 1, '_records': [], 'soldiers_seen': 99,
           'egg_uses': {'どうし': 0},
           'chart_plan': {'purchases': {'month': (5, 10),
                                        'cards': (('デッドガン', 100),),
                                        'soldiers': 2000}}}
    # The old spare calculation rejected an unpriced adjusted chart, even rich.
    assert policy.yes_no_step(parse(_goninja_offer(1000).frame()), mem) == [policy.pad('a')]
    assert mem['_records'][-1]['strategy_variant'] == 'accept_goninja'


@pytest.mark.parametrize('background', [False, True])
@pytest.mark.parametrize('owner', [None, 'recruit', 'month_exit'])
@pytest.mark.parametrize('gold,choice,variant', [
    (999, 'いかんッ!', 'decline_goninja_below_floor'),
    (1000, 'うむッ!', 'accept_goninja'),
    (1001, 'うむッ!', 'accept_goninja'),
    (None, 'いかんッ!', 'decline_goninja_unreadable'),
])
def test_goninja_floor_overrides_monthly_acceptance_owner(background, owner, gold, choice, variant):
    actions, state = decide(offer(gold, background=background), owned_state(owner))
    assert actions == [policy.pad('a' if choice == 'うむッ!' else 'down')]
    if choice == 'いかんッ!':
        actions, state = decide(offer(gold, background=background, cursor='decline'), state)
        assert actions == [policy.pad('a')]
    records = [row for row in state['_records'] if row['strategy_variant'] == variant]
    assert len(records) == 1 and records[0]['choice'] == choice
    assert not any(row['decision'] == 'month_sub_step' for row in state['_records'])
    # The event answer cannot declare recruitment or monthly exit complete.
    if owner == 'recruit':
        assert state['policy']['month_sub']['presses'] == 0
        assert not state['policy']['month_sub'].get('recruit_paid_gold')
    elif owner == 'month_exit':
        assert state['policy']['month_exit'] is True


@pytest.mark.parametrize('background', [False, True])
@pytest.mark.parametrize('owner', [None, 'recruit', 'month_exit'])
@pytest.mark.parametrize('gold', [999, 1000, None])
@pytest.mark.parametrize('cursor', ['missing', 'ambiguous'])
def test_unreadable_goninja_cursor_never_falls_back_to_accept(background, owner, gold, cursor):
    frame = offer(gold, background=background, cursor=cursor)
    screen = parse(frame)
    assert screen.hand is None and screen.selected is None
    actions, state = decide(frame, owned_state(owner))
    assert actions == []
    state['no_input_frames'] = [frame.digest()]
    state['no_input_streak'] = NO_INPUT_HOLD_MAX
    actions, state = decide(frame, state)
    assert actions == [policy.pad('b')]
    assert any(row['decision'] == 'no_input_fallback' for row in state['_records'])


@pytest.mark.parametrize('background', [False, True])
@pytest.mark.parametrize('owner', ['recruit', 'month_exit'])
@pytest.mark.parametrize('prompt', ['しょうぐんをぼしゅうしますか?',
                                    'よろしいですかな?', 'ごあいてをしましょうか?'])
def test_other_prompts_do_not_borrow_goninja_cash_floor(background, owner, prompt):
    actions, state = decide(offer(999, background=background, prompt=prompt), owned_state(owner))
    assert actions == [policy.pad('a')]
    assert not any('goninja' in row['strategy_variant'] for row in state['_records'])


def test_ordinary_recruitment_is_affordable_below_goninja_floor():
    screen = parse(offer(999, prompt='しょうぐんをぼしゅうしますか?'))
    mem = owned_state('recruit')['policy']
    mem['_records'] = []
    # Complete, fresh roster and a finished recovery leave 999G affordable
    # under recruitment's own checks, independently of the event override.
    assert policy._recruit_spare_after_necessities(mem, mem['shop'], 999)[0] is True
    assert policy.month_sub_step(screen, mem) == [policy.pad('a')]
    assert mem['month_sub']['presses'] == 1


def test_goninja_words_without_both_choices_keep_recruit_owner():
    actions, state = decide(offer(999, both_choices=False), owned_state('recruit'))
    assert actions == [policy.pad('a')]
    assert state['policy']['month_sub']['presses'] == 1
    assert not any('goninja' in row['strategy_variant'] for row in state['_records'])


def test_trade_remains_declined_even_above_goninja_floor():
    actions, state = decide(offer(1001, prompt='しょうぐんどうしのトレードだ!'), owned_state('recruit'))
    assert actions == [policy.pad('down')]


def monthly_return(gold):
    canvas = Canvas()
    canvas.text(48, 15, f'2ねん 11のつき {gold}G')
    for label, (x, y) in MONTH_GRID.items():
        canvas.text(x, y, label)
    x, y = MONTH_GRID['しょうにん']
    canvas.hand(x - 22, y - 6)
    return canvas.frame()


def pending_payment(kind):
    state = owned_state(None)
    mem = state['policy']
    mem['month_sub'] = {'kind': kind, 'gold_before': 1000, 'presses': 0,
                        'key': '2-11', 'left_menu': True}
    mem['shop'].update({kind: 'opened', 'gold_start': 1000})
    if kind == 'egg':
        mem['egg_uses'] = {'どうし': 0}
        mem['shop']['recruit'] = 'check'
        mem['month_sub'].update(quoted_cost=50, full_selected=True, stage='recovering')
    elif kind == 'chikujou':
        mem['month_sub'].update(quoted_cost=50, confirm_gold=1000, confirm_sent=True,
                                upgraded=True, chosen='アルマムーン')
    return state


@pytest.mark.parametrize('kind', ['egg', 'recruit', 'chikujou'])
@pytest.mark.parametrize('prompt', ['うちとおすか?', 'しょうぐんどうしのトレードだ!'])
def test_foreign_event_deduction_never_becomes_a_monthly_payment_receipt(kind, prompt):
    _, state = decide(offer(1000, prompt=prompt), pending_payment(kind))
    mem = state['policy']
    for _ in range(policy.MONTH_SUB_MENU_WAIT + 1):
        if mem.get('month_sub') is None:
            break
        policy._finish_month_sub(parse(monthly_return(950)), mem, mem['shop'])
    assert mem.get('month_sub') is None
    assert mem['shop'][kind] == 'unverified'
    if kind == 'egg':
        assert mem['egg_uses'] == {'どうし': 0}
        assert mem['shop']['recruit'] == 'check'
    if kind == 'chikujou':
        assert not mem['shop'].get('chikujou_upgrades')


@pytest.mark.parametrize('kind', ['egg', 'recruit'])
def test_two_frame_goninja_payment_does_not_unlock_unfinished_recovery_or_recruitment(kind):
    actions, state = decide(offer(1000), pending_payment(kind))
    assert actions == [policy.pad('a')]
    _, state = decide(monthly_return(950), state)
    mem = state['policy']
    assert mem['shop'][kind] != 'done'
    result = next(r for r in state['_records'] if r['decision'] == ('egg_recover' if kind == 'egg' else 'recruit'))
    assert result['deviation_reason'] == 'payment_interrupted'
    if kind == 'egg':
        assert mem['egg_uses'] == {'どうし': 0}
        assert (mem.get('month_sub') or {}).get('kind') != 'recruit'


@pytest.mark.parametrize('kind', ['egg', 'recruit', 'chikujou'])
def test_interrupted_unpaid_owner_closes_without_resuming_spending(kind):
    _, state = decide(offer(1000), pending_payment(kind))
    mem = state['policy']
    assert policy.month_sub_step(parse(monthly_return(950)), mem) == [policy.pad('b')]
    assert mem['month_sub']['aborted'] is True
    assert not mem['month_sub'].get('recruit_paid_gold')


def test_a_candidate_balance_after_an_event_cannot_create_a_new_recruit_fee_receipt():
    _, state = decide(offer(1000), pending_payment('recruit'))
    mem = state['policy']
    screen = replace(paid_recruit_screen(gold=950), header={'year': 2, 'month': 11, 'gold': 950})
    assert policy.month_sub_step(screen, mem) == [policy.pad('b')]
    assert not mem['month_sub'].get('recruit_paid_gold')
    assert not mem['month_sub'].get('paid_candidates')


@pytest.mark.parametrize('kind', ['egg', 'recruit'])
def test_normal_amount_receipts_without_a_foreign_event_remain_valid(kind):
    mem = pending_payment(kind)['policy']
    assert policy._finish_month_sub(parse(monthly_return(950)), mem, mem['shop'])
    assert mem['shop'][kind] == 'done'


def test_a_previously_observed_recruit_fee_survives_a_later_goninja_deduction():
    state = pending_payment('recruit')
    sub = state['policy']['month_sub']
    sub.update(gold_before=1050, recruit_paid_gold=1000, paid_candidates=True,
               candidate_names=['ゼウス'])
    _, state = decide(offer(1000), state)
    _, state = decide(monthly_return(950), state)
    assert state['policy']['shop']['recruit'] == 'done'
    result = next(r for r in state['_records'] if r['decision'] == 'recruit')
    assert result['observed_metric']['fee_receipt_gold'] == 1000
    assert result['deviation_reason'] is None

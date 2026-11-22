"""The month must not close the merchant before eggs/recruits run (v1376).

Production g1314 v1376: months 93-12 and 94-1 wrote month_merchant_skipped
19 s / 28 s after month_seen because the first plan_bargains returned early on
``_necessities_pending`` and left ``items`` empty; flows/month.py read that as
"nothing to buy" and set merchant_done before the egg/recruit sub ever ran.

The merchant's own purchase confirmation is also covered here: the generic
quantity-confirm A used to spend the money without recording buy/last_buy/
card_stock, so the next list asked for the same card again and the
holding-limit refusal was learned against the wrong state (prod v1374 93-9:
494G, buy 0, refusal 3).
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import pytest

from docich import hanjuku_policy as policy
from docich.hanjuku_bot import decide
from docich.hanjuku_screen import parse
from docich.hanjuku.strategy import bargains
from test_hanjuku_chart_bot import Canvas, month_canvas
from test_hanjuku_merchant_reserve import memory


IDENTITY = {'game': 'hanjuku-hero', 'generation': 7,
            'runtime_id': 'g7-synthetic', 'lease_id': 'synthetic-lease'}


@pytest.fixture(autouse=True)
def no_later_chart_purchase(monkeypatch):
    # A later charted purchase reserves its cash and would stop the spare-cash
    # plan for reasons unrelated to this bug; the plan itself is covered by
    # test_hanjuku_merchant_bargains.
    monkeypatch.setattr(bargains, '_charted_purchase_ahead', lambda mem, header: False)


def month_memory(*, egg='pending', recruit=False, gold=9999, stock_count=0):
    mem = {
        'chapter': 1, 'month': '1-7', 'tick': 100,
        '_run_identity': dict(IDENTITY),
        'survey_run_identity': dict(IDENTITY),
        'soldiers_seen': 60, 'soldiers_seen_key': '1-7',
        'soldiers_seen_scope': [dict(IDENTITY), 1, '1-7'],
        'hanjuku_level_seen': {'scope': [1, '1-7']},
        'egg_uses': {'ココット': 0},
        'card_stock': {name: stock_count for name in bargains.KNOWN_PRICES},
        'shop': {'key': '1-7', 'items': [], 'want': None, 'soldiers': 0,
                 'soldiers_done': True, 'merchant_done': False,
                 'gold_start': gold, 'variant': 'soldier_refill_only',
                 'egg': egg, 'egg_priority': egg == 'pending',
                 'reserve': 50 if egg == 'pending' else 0},
    }
    if recruit:
        # A measured roster keeps the recruit step executable in this
        # synthetic month: without it the flow can only defer the fee and the
        # month would close before the assertion. One general in the home
        # castle is a real shortage against the 32-general target.
        mem['recruit_roster'] = {
            'identity': dict(IDENTITY), 'chapter': 1, 'month': '1-7',
            'tick': 90, 'complete': True, 'names': ['ココット'],
            'wages': {'ココット': 1}}
        mem['shop'].update(recruit='check', recruit_priority=True,
                           recruit_reserve=100)
    return mem


def decisions(state):
    return [row.get('decision') for row in state.get('_records') or ()]


def finish_egg(shop):
    shop.update(egg='done', egg_priority=False, reserve=0)


def test_a_pending_egg_does_not_close_the_merchant():
    frame = month_canvas(9999, on='しょうにん', month=7)
    mem = month_memory(egg='pending')
    actions, state = decide(frame, {'policy': mem}, run_identity=dict(IDENTITY))
    assert 'month_merchant_skipped' not in decisions(state)
    assert state['policy']['shop']['bargain_deferred'] == 'necessities'

    # The recovery is settled: the same month menu must now plan the known
    # shortage and open the merchant.
    state['policy'].pop('month_sub', None)
    finish_egg(state['policy']['shop'])
    actions, state = decide(frame, state, run_identity=dict(IDENTITY))
    shop = state['policy']['shop']
    assert shop['items'], 'the settled month must plan the known shortage'
    assert not shop.get('bargain_deferred')
    assert 'bargain_plan' in decisions(state)
    assert 'month_merchant_skipped' not in decisions(state)
    assert actions == [policy.pad('a')]
    assert state['policy'].get('month_merchant_confirm') is True


def test_a_pending_recruit_does_not_close_the_merchant():
    frame = month_canvas(9999, on='しょうにん', month=7)
    mem = month_memory(egg='done', recruit=True)
    actions, state = decide(frame, {'policy': mem}, run_identity=dict(IDENTITY))
    assert 'month_merchant_skipped' not in decisions(state)
    assert state['policy']['shop']['bargain_deferred'] == 'necessities'

    state['policy'].pop('month_sub', None)
    shop = state['policy']['shop']
    shop.update(recruit='done', recruit_priority=False, recruit_reserve=0)
    actions, state = decide(frame, state, run_identity=dict(IDENTITY))
    shop = state['policy']['shop']
    assert shop['items']
    assert not shop.get('bargain_deferred')
    assert 'month_merchant_skipped' not in decisions(state)
    assert actions == [policy.pad('a')]


def test_a_fully_stocked_month_still_closes_the_merchant_once():
    # Every target is at the per-kind cap: after the egg is settled there is
    # genuinely nothing to buy. The old single month_merchant_skipped close
    # must still happen, and the month must reach month_done (not loop).
    frame = month_canvas(9999, on='しょうにん', month=7)
    mem = month_memory(egg='pending', stock_count=bargains.HOLD_CAP)
    actions, state = decide(frame, {'policy': mem}, run_identity=dict(IDENTITY))
    assert 'month_merchant_skipped' not in decisions(state)

    state['policy'].pop('month_sub', None)
    finish_egg(state['policy']['shop'])
    actions, state = decide(frame, state, run_identity=dict(IDENTITY))
    names = decisions(state)
    assert names.count('month_merchant_skipped') == 1
    assert state['policy']['shop']['merchant_done'] is True
    assert names.count('month_done') == 1
    assert state['policy']['shop']['closed'] is True
    # しょうにん -> も〜おしまい!: navigation, never an endless empty hold.
    assert actions


def shop_listing(gold=9999):
    c = Canvas()
    c.text(48, 15, f'1ねん 5のつき {gold}G')
    for y, name, price in ((175, 'ノリウツール', 18), (191, 'イッテツーン', 1),
                           (143, 'クースカン', 24)):
        c.text(120, y, name); c.text(216, y, f'{price}G')
    c.hand(98, 169)
    return c.frame()


def merchant_confirm(gold=9999, qty=99, price=18):
    """The measured merchant quote: NこでNGになりまんな! + よろしいでっか?"""
    c = Canvas()
    c.text(48, 15, f'1ねん 5のつき {gold}G')
    # The quote wording sits one line above the buttons (the measured live
    # frame shares y=183, but a single synthetic line corrupts the うむッ!
    # cell; classify_text and the quote regex read the whole screen).
    c.text(24, 167, f'{qty}こで{qty * price}Gになりまんな!')
    c.text(24, 183, 'よろしいでっか?')
    c.text(184, 183, 'うむッ!')
    c.text(184, 199, 'いかんッ!')
    c.hand(162, 177)
    return c.frame()


def buy_memory(gold=9999, qty=99, price=18):
    mem = memory(complete=True)
    # Same run as the observation: a different survey identity makes decide()
    # forget the ledger before the buy (and the test would count nothing).
    mem['_run_identity'] = dict(IDENTITY)
    mem['survey_run_identity'] = dict(IDENTITY)
    mem['month'] = '1-5'
    mem['shop'].update(key='1-5', items=[['ノリウツール', qty]],
                       want=['ノリウツール', qty, price],
                       rich_merchant=True, merchant_done=False,
                       bargain_cards=[], bargain_planned=True,
                       bargain_listing_planned=True)
    mem['card_stock'] = {name: bargains.HOLD_CAP for name in bargains.KNOWN_PRICES}
    mem['card_stock']['ノリウツール'] = 0
    return mem


def assert_buy_recorded(state, qty=99):
    names = decisions(state)
    assert 'quantity_confirm_accept' not in names
    assert 'buy' in names
    buy = next(row for row in state['_records'] if row['decision'] == 'buy')
    assert buy['card'] == 'ノリウツール' and buy['qty'] == qty and buy['price'] == 18
    shop = state['policy']['shop']
    assert shop['want'] is None
    assert shop['items'] == []
    assert shop['bought'] == [['ノリウツール', qty, 18]]
    assert shop['last_buy']['row'] == ['ノリウツール', qty, 18]
    assert state['policy']['card_stock']['ノリウツール'] == qty


def test_the_merchant_quote_is_recorded_by_the_shop_flow():
    actions, state = decide(merchant_confirm(), {'policy': buy_memory()},
                            run_identity=dict(IDENTITY))
    assert actions == [policy.pad('a')]
    assert_buy_recorded(state)


@pytest.mark.parametrize('unreadable', ['selected', 'hand'])
def test_an_unreadable_quote_still_records_the_buy(monkeypatch, unreadable):
    real_parse = parse

    def unread(frame, phase=None):
        screen = real_parse(frame, phase=phase)
        if unreadable == 'selected':
            screen.selected = None
        else:
            screen.hand = None
        return screen

    # _decide_impl imports parse at call time; patching the module attribute
    # makes every parse in this observation return the unreadable reading.
    monkeypatch.setattr('docich.hanjuku.os_layer.screen.parse', unread)
    actions, state = decide(merchant_confirm(), {'policy': buy_memory()},
                            run_identity=dict(IDENTITY))
    assert actions == [policy.pad('a')]
    assert_buy_recorded(state)


def test_the_next_list_does_not_ask_for_the_same_card_again():
    actions, state = decide(merchant_confirm(), {'policy': buy_memory()},
                            run_identity=dict(IDENTITY))
    assert_buy_recorded(state)

    # The metre is now 99: a listing that still shows the card must not plan
    # or ask for it again (that re-ask is what produced buy_skip
    # holding_limit after a purchase the old path failed to record).
    actions, state = decide(shop_listing(9999), state, run_identity=dict(IDENTITY))
    shop = state['policy']['shop']
    assert shop.get('want') is None
    assert shop['items'] == []
    assert 'buy' not in decisions(state)
    assert 'buy_skip' not in decisions(state)
    assert actions == [policy.pad('b')]


def test_the_generic_accept_still_fires_when_no_flow_owns_the_quote():
    # g1190 2026-11-18 63-5 was the priest's 「1こで50Gになりまんな!」 quote;
    # since #5046 a tracked egg sub owns it, and this fix only excludes the
    # merchant (mem['shop']['want']). With neither owner the generic fallback
    # must still press A, or an untracked quote would hold forever.
    mem = {'chapter': 1, 'month': '1-5', '_run_identity': dict(IDENTITY),
           'survey_run_identity': dict(IDENTITY),
           'shop': {'key': '1-5', 'want': None}}
    frame = Canvas()
    frame.text(48, 15, '1ねん 5のつき 300G')
    frame.text(24, 167, '1こで50Gになりまんな!')
    frame.text(24, 183, 'よろしいでっか?')
    frame.text(184, 183, 'うむッ!')
    frame.text(184, 199, 'いかんッ!')
    frame.hand(162, 177)
    actions, state = decide(frame.frame(), {'policy': mem},
                            run_identity=dict(IDENTITY))
    assert actions == [policy.pad('a')]
    assert 'quantity_confirm_accept' in decisions(state)

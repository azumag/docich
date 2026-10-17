"""A base boss card short in measured stock is bought back and carried again.

g825 (runtime g825-0111756c, 2026-10-17): chapter 1 charts one card purchase
(1-5). Strong-card carries spent 1-B1's クースカン in ordinary sorties, and
2-8 (127G, 14 soldiers) / 2-9 (88G, 43 soldiers) paid recruits, soldiers and
building instead; the boss sortie's card list then had none (08:58-09:39 JST
held at card select). Screens are synthetic; no live purchase is claimed.
"""
from copy import deepcopy
import json

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku.strategy import boss_cards, economy
from docich.hanjuku.strategy.kit import _deploy_cards, _observe_card_stock
from docich.hanjuku_screen import parse
from test_hanjuku_army_count_check import IDENTITY
from test_hanjuku_boss_card_proven_absent import G825, panel
from test_hanjuku_chart_bot import Canvas, _short_recruit_memory, month_canvas
from test_hanjuku_low_army_refill_first import editor
from test_hanjuku_merchant_reserve import quote

BOSS = next(o for o in p.chart.orders(1) if o['step'] == '1-B1')
CART = (('イッテツーン', 1), ('ノリウツール', 18), ('クースカン', 24))


def screen(gold, on='しょうにん', month=9):
    return parse(month_canvas(gold, month=month, on=on))


def listing(gold, month=9, rows=CART):
    c = Canvas(); c.text(48, 15, f'1ねん {month}のつき {gold}G')
    for i, (name, price) in enumerate(rows):
        c.text(120, 175 + i * 16, name); c.text(216, 175 + i * 16, f'{price}G')
    c.hand(98, 169)
    s = parse(c.frame()); s.kind = 'shop_list'
    return s


def memory(month=9, **extra):
    # After #2374 dropped the proven-absent クースカン from 1-B1 (g825 2-9).
    mem = {'chapter': 1, 'month': f'1-{month}', 'tick': 10, 'egg_uses': {'どうし': 4},
           'card_stock': {'クースカン': 0, 'ノリウツール': 2, 'イッテツーン': 1},
           'card_drop': {'1-B1': ['クースカン']}, 'card_scroll': {'1-B1': p.CARD_SCROLL_LIMIT},
           'card_miss': {'1-B1:クースカン': 2}, 'orders': {'1-B1': 'pending'}}
    mem.update(extra)
    return mem


def open_month(mem, gold, month=9, on='しょうにん'):
    s = screen(gold, on=on, month=month)
    p.observe_events(s, mem)
    return p.month_step(s, mem)


def records(mem, kind):
    return [r for r in mem.get('_records', ()) if r['decision'] == kind]


def buy(mem, gold, month=9):
    assert p.shop_step(listing(gold, month), mem) == [p.pad('down')]  # cursor to クースカン
    assert mem['shop']['want'] == ['クースカン', 1, 24]
    assert p.quantity_step(quote(gold, count=1, cost=24, month=month), mem) == [p.pad('a')]


def test_g825_short_boss_card_is_bought_first_and_returns_to_the_boss_kit():
    mem = memory()
    assert open_month(mem, 88) == [p.pad('a')]  # the merchant opens first
    shop = mem['shop']
    assert shop['items'] == [['クースカン', 1]]
    assert shop['soldiers'] == 88 - 24 - economy.WAGE_RESERVE  # what the card leaves
    plan = records(mem, 'boss_card_rebuy_plan')[0]
    assert plan['plan']['cards'] == [['クースカン', 1]]
    assert plan['observed_metric']['short'] == {'クースカン': 1}
    assert plan['observed_metric']['spendable'] == 88 - economy.WAGE_RESERVE
    assert records(mem, 'month_plan')[0]['plan']['cards'] == [['クースカン', 1]]
    mem = json.loads(json.dumps(mem))  # hot-loaded between observations
    buy(mem, 88)
    assert mem['shop']['bought'] == [['クースカン', 1, 24]]
    assert mem['card_drop'] == {'1-B1': []}
    assert '1-B1' not in mem['card_scroll'] and '1-B1:クースカン' not in mem['card_miss']
    assert mem['card_bought_unseen'] == {'クースカン': 1}
    assert mem['card_stock']['クースカン'] == 0  # a purchase is not an observed stock
    assert records(mem, 'boss_card_restored')[0]['observed_metric']['restored'] == 1
    assert _deploy_cards(BOSS, mem) == ['クースカン', 'ノリウツール']


def test_bought_copy_is_not_bought_again_until_a_panel_reads_it():
    mem = memory()
    open_month(mem, 88)
    buy(mem, 88)
    open_month(mem, 90, month=10)
    assert mem['shop']['items'] == [] and not records(mem, 'boss_card_rebuy_plan')[1:]
    # The next card panel reads the copy: the observation replaces the receipt.
    _observe_card_stock(mem, {'rows': [{'card': 'ノリウツール', 'stock': 2, 'y': 55},
                                       {'card': 'クースカン', 'stock': 1, 'y': 71}], 'remaining': 3})
    assert mem['card_stock']['クースカン'] == 1 and mem['card_bought_unseen'] == {}
    open_month(mem, 92, month=11)
    assert mem['shop']['items'] == []


def test_restored_card_is_carried_by_the_next_boss_sortie():
    mem = {**memory(), 'active': '1-B1', 'picked': ['ノリウツール'],
           'sortie_general': {'1-B1': 'アンディーブ'}}
    boss_cards.note_card_purchase(mem, 'クースカン', 1)
    shown = ('ブラッキー', 'フットバース', 'ノリウツール', 'クースカン')
    assert p.deploy_step(panel(shown, selected=3), mem) == [p.pad('a')]
    assert mem['picked'] == ['ノリウツール', 'クースカン']
    assert mem['card_stock']['クースカン'] == 1 and not mem['card_drop']['1-B1']


def test_restored_card_below_the_panel_is_searched_not_held():
    # The drop's exhausted scroll budget restarts with the purchase: the bought
    # copy may sit below a full panel whose IDs end before クースカン(13).
    mem = {**memory(), 'active': '1-B1', 'picked': ['ノリウツール'],
           'sortie_general': {'1-B1': 'アンディーブ'}}
    boss_cards.note_card_purchase(mem, 'クースカン', 1)
    top = ('イッテツーン', 'ブラッキー', 'フットバース', 'ノリウツール')
    assert p.deploy_step(panel(top, selected=3), mem) == [p.pad('down')]
    assert mem['card_scroll'] == {'1-B1': 1} and not records(mem, 'situation_held')


def test_low_army_refill_is_still_paid_before_the_boss_card():
    # g825 2-8: 127G and 14 soldiers.
    mem = memory(month=8, _run_identity=deepcopy(IDENTITY), soldiers_seen=14,
                 soldiers_seen_key='1-8')
    mem['soldiers_seen_scope'] = deepcopy(economy._soldier_count_scope(mem, screen(127, month=8).header))
    assert open_month(mem, 127, month=8) == [p.pad('down')]  # past the merchant
    shop = mem['shop']
    assert economy.low_army_first(shop) and shop['soldiers'] == 36  # 14 -> 50, card ignored
    assert shop['items'] == [['クースカン', 1]] and shop['merchant_done'] is False
    assert records(mem, 'low_army_refill_plan')[0]['observed_metric']['cards_after'] == ['クースカン']
    assert open_month(mem, 127, month=8, on='へいしほじゅう') == [p.pad('a')]
    assert p.quantity_step(editor(36, 127), mem, soldiers=True) == [p.pad('a')]
    for _ in range(2):
        p.observe_events(screen(91, month=8), mem)
    assert shop['soldier_receipt']['status'] == 'paid' and mem['soldiers_seen'] == 50
    # Settled: the merchant comes next with what the refill left.
    assert p.month_step(screen(91, month=8), mem) == [p.pad('a')]
    assert not economy.low_army_first(shop)
    buy(mem, 91, month=8)
    assert _deploy_cards(BOSS, mem) == ['クースカン', 'ノリウツール']


def test_refill_that_leaves_no_card_money_buys_it_another_month():
    mem = memory(month=8, _run_identity=deepcopy(IDENTITY), soldiers_seen=14,
                 soldiers_seen_key='1-8')
    mem['soldiers_seen_scope'] = deepcopy(economy._soldier_count_scope(mem, screen(60, month=8).header))
    open_month(mem, 60, month=8)
    assert mem['shop']['soldiers'] == 30  # every coin above the wage floor
    open_month(mem, 60, month=8, on='へいしほじゅう')
    p.quantity_step(editor(30, 60), mem, soldiers=True)
    for _ in range(2):
        p.observe_events(screen(30, month=8), mem)
    assert p.month_step(screen(30, month=8), mem) == [p.pad('a')]
    assert p.shop_step(listing(30, 8), mem) == [p.pad('b')]  # wages stay in hand
    assert not mem['shop'].get('bought') and mem['card_drop'] == {'1-B1': ['クースカン']}
    assert records(mem, 'buy_skip')[0]['deviation_reason'] == 'reserved_funds'
    mem['_records'] = []
    open_month(mem, 90, month=9)
    assert mem['shop']['items'] == [['クースカン', 1]]


@pytest.mark.parametrize('gold,bought', [(53, False), (54, True), (88, True)])
def test_wage_floor_is_kept(gold, bought):
    mem = memory()
    open_month(mem, gold)
    assert mem['shop']['items'] == ([['クースカン', 1]] if bought else [])
    if not bought:
        deferred = records(mem, 'boss_card_rebuy_deferred')[0]
        assert deferred['deviation_reason'] == 'insufficient_funds'
        assert deferred['observed_metric']['spendable'] == gold - economy.WAGE_RESERVE


@pytest.mark.parametrize('gold,bought', [(103, False), (104, True)])
def test_egg_recovery_hold_is_kept(gold, bought):
    mem = memory(egg_uses={'どうし': 3})  # a depleted hero egg holds 50G first
    open_month(mem, gold)
    assert mem['shop']['reserve'] == 50
    assert mem['shop']['items'] == ([['クースカン', 1]] if bought else [])


def test_recruit_fee_is_sized_after_the_boss_card():
    mem = _short_recruit_memory()
    mem.update(card_stock={'クースカン': 0, 'ノリウツール': 1}, orders={'1-B1': 'pending'})
    shop = p._plan(mem, {'year': 1, 'month': 7, 'gold': 101})
    assert shop['items'][0] == ['クースカン', 1]
    assert shop['recruit_priority'] and shop['recruit_reserve'] == 101 - 24 - economy.WAGE_RESERVE
    assert shop['soldiers'] == 0


def test_adjusted_month_buys_the_boss_card_before_its_own_cards():
    # g825 2-7 planned an unlisted ダイチスイム x3 and bought nothing.
    mem = memory(month=7, chart_plan={'purchases': {'month': [1, 7], 'cards': [['ダイチスイム', 3]],
                                                    'soldiers': 10}})
    open_month(mem, 90, month=7)
    assert mem['shop']['variant'] == 'chart_adjusted'
    assert mem['shop']['items'] == [['クースカン', 1], ['ダイチスイム', 3]]


def test_later_charted_purchase_keeps_its_budget():
    mem = memory(month=4)  # chapter 1 still buys クースカン x4 in 1-5
    open_month(mem, 200, month=4)
    assert not (mem.get('shop') or {}).get('items') and not records(mem, 'boss_card_rebuy_plan')
    # A low army still gets its month plan; the card waits for the chart.
    mem = memory(month=4, _run_identity=deepcopy(IDENTITY), soldiers_seen=10,
                 soldiers_seen_key='1-4')
    mem['soldiers_seen_scope'] = deepcopy(economy._soldier_count_scope(mem, screen(200, month=4).header))
    open_month(mem, 200, month=4)
    assert mem['shop']['items'] == []
    assert records(mem, 'boss_card_rebuy_deferred')[0]['deviation_reason'] == 'chart_purchase_ahead'


def test_charted_month_that_buys_the_card_adds_no_second_order():
    mem = memory(month=5, card_stock={'クースカン': 0})
    open_month(mem, 214, month=5)
    assert ['クースカン', 4] in mem['shop']['items']
    assert sum(q for n, q in mem['shop']['items'] if n == 'クースカン') == 4
    assert not records(mem, 'boss_card_rebuy_plan')


@pytest.mark.parametrize('change', ['unmeasured', 'enough', 'launched', 'failed', 'defeated', 'rare'])
def test_no_rebuy_without_a_measured_shortage_for_a_boss_still_to_sortie(change):
    mem = memory()
    if change == 'unmeasured':
        mem['card_stock'].pop('クースカン')
    elif change == 'enough':
        mem['card_stock']['クースカン'] = 1
    elif change == 'launched':
        mem['orders']['1-B1'] = 'launched'  # the sortie carries its kit
    elif change == 'failed':
        mem['orders']['1-B1'] = 'failed'
    elif change == 'defeated':
        mem['boss_defeated'] = 1
    else:
        mem['rare_card_kit'] = {'1-B1': ['キャトルミュー']}
    open_month(mem, 200)
    assert ['クースカン', 1] not in mem['shop']['items']
    assert not records(mem, 'boss_card_rebuy_plan')


def test_id_ordered_panel_proves_a_stale_positive_stock_is_zero():
    mem = {'card_stock': {'クースカン': 1, 'ミックミー': 2, 'ゼンマイン': 1},
           'card_bought_unseen': {'クースカン': 1, 'ゼンマイン': 1}}
    rows = [{'card': name, 'stock': 1, 'y': 55 + 16 * i} for i, name in enumerate(G825)]
    _observe_card_stock(mem, {'rows': rows, 'remaining': 3})
    # 11 < クースカン(13), ゼンマイン(15) < 16; ミックミー(17) may sit below.
    assert mem['card_stock']['クースカン'] == 0 and mem['card_stock']['ゼンマイン'] == 0
    assert mem['card_stock']['ミックミー'] == 2
    assert mem['card_bought_unseen'] == {}
    shuffled = [dict(r) for r in rows]
    shuffled[0], shuffled[3] = shuffled[3], shuffled[0]
    mem = {'card_stock': {'クースカン': 1}, 'card_bought_unseen': {'クースカン': 1}}
    _observe_card_stock(mem, {'rows': shuffled, 'remaining': 3})
    assert mem['card_stock']['クースカン'] == 1 and mem['card_bought_unseen'] == {'クースカン': 1}


def test_proven_absent_boss_card_never_listed_before_is_recorded_and_rebought():
    mem = {**memory(), 'active': '1-B1', 'picked': ['ノリウツール'],
           'sortie_general': {'1-B1': 'アンディーブ'}, 'card_drop': {}, 'card_scroll': {}}
    mem['card_stock'] = {}
    for _ in range(p.CARD_MISS_LIMIT):
        p.deploy_step(panel(), mem)
    assert mem['card_drop'] == {'1-B1': ['クースカン']}
    assert mem['card_stock']['クースカン'] == 0
    mem.update(active=None, picked=[], _records=[])
    open_month(mem, 88, month=10)
    assert mem['shop']['items'] == [['クースカン', 1]]


def _strong_sortie(step, count, **extra):
    mem = {'rare_scan_month': 'chapter-1:unknown', 'chapter': 1, 'month': '1-9', 'active': step,
           'variant': 'chart', 'orders': {step: 'pending'}, 'picked': [], '_records': [], **extra}
    inventory = {'rows': [{'card': 'クースカン', 'stock': count, 'y': 55}],
                 'selected_y': 55, 'remaining': 3}
    _observe_card_stock(mem, inventory)
    order = p._order(mem)
    return order, mem, inventory


def test_strong_card_supplement_leaves_the_boss_copy_alone():
    # g825 07:52-09:10: 1-B1's only クースカン was added to ordinary sorties.
    order, mem, inventory = _strong_sortie('1-A2', 1)
    wanted = _deploy_cards(order, mem)
    for _ in range(3):
        assert p._strong_card_inventory(mem, order, inventory, wanted) is False
    assert 'strong_card_kit' not in mem
    held = records(mem, 'strong_card_reserved_for_boss')
    assert len(held) == 1 and held[0]['observed_metric']['reserved'] == ['クースカン']
    assert held[0]['observed_metric']['boss_kit'] == {'クースカン': 1, 'ノリウツール': 1}


@pytest.mark.parametrize('step,count,extra', [
    ('1-A2', 2, {}),                         # a surplus copy is spare
    ('1-A2', 1, {'boss_defeated': 1}),       # no boss left to keep it for
    ('1-B1', 1, {}),                         # the boss sortie itself
])
def test_strong_card_supplement_still_uses_spare_copies(step, count, extra):
    order, mem, inventory = _strong_sortie(step, count, **extra)
    wanted = _deploy_cards(order, mem)
    if step == '1-B1':
        inventory['rows'].append({'card': 'ノリウツール', 'stock': 1, 'y': 71})
        mem['card_stock'].update({'ミックミー': 1})
    assert p._strong_card_inventory(mem, order, inventory, wanted) is True
    assert not records(mem, 'strong_card_reserved_for_boss')

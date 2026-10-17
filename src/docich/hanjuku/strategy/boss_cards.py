"""戦略層: buy the base boss kit's cards back once measured stock runs short.

g825 (runtime g825-0111756c, 2026-10-17): chapter 1 charts one card purchase
(1-5). Ordinary sorties carried the クースカン the boss order 1-B1 needs as a
strong card, and no later month bought another: at 2-8 (127G) and 2-9 (88G)
the money went to recruits, soldiers and building while the boss sortie's
card list had none, and card select held from 08:58 to 09:39 JST. #2374 leaves
a card the panel proves absent (``card_drop``). This module plans the merchant
purchase that lets the next boss sortie carry it again, returns a bought card
to the boss kit, and keeps the strong-card supplement off the copies the boss
kit still needs.

Order of money: a measured army of LOW_ARMY or fewer is still refilled first
(owner 2026-10-17, v232). ``_low_army_refill``/``_soldier_spendable`` ignore
unbought cards and the merchant waits for that refill, so a rebuy only uses
what the refill leaves. Otherwise the rebuy is a necessity like a charted
card: it is priced after the wage floor, the egg recovery hold and the house
repair money (``_merchant_spendable``), ahead of the hiring fee and optional
soldiers, which are planned from what it leaves.
"""
from __future__ import annotations

from collections import Counter

from ... import hanjuku_chart as chart
from ..decision_log import _record
from .economy import KNOWN_PRICES, _merchant_spendable, _soldier_spendable
from .kit import _is_boss_order

# Copies bought (confirmed at the merchant's quote) that no card panel has shown
# yet. card_stock stays an observation; this keeps a bought card from being
# bought again before a sortie panel reads it.
UNSEEN = 'card_bought_unseen'


def boss_kit(mem):
    """``(step, Counter)`` of this chapter's base boss kit while it can still sortie.

    The base chart's boss order is the strict kit (``_strict_boss_cards``). An
    in-flight boss sortie already carries its cards, a measured event-card kit
    replaced them, and an exhausted (failed) or won boss needs no more.
    """
    chapter = mem.get('chapter')
    if type(chapter) is not int or mem.get('boss_defeated') == chapter:
        return None
    boss = chart.boss_castle(chapter)
    order = next((o for o in chart.orders(chapter) if o['target'] == boss), None)
    if order is None or not order['cards']:
        return None
    step = order['step']
    if ((mem.get('orders') or {}).get(step) not in (None, 'pending')
            or (mem.get('rare_card_kit') or {}).get(step) is not None):
        return None
    return step, Counter(order['cards'])


def _unseen(mem):
    ledger = mem.get(UNSEEN)
    return ledger if isinstance(ledger, dict) else {}


def _chart_purchase_ahead(mem, header):
    """A later base-chart purchase this chapter keeps its budget (as bargains do)."""
    here = (header['year'], header['month'])
    return any(tuple(p.get('month') or ()) > here
               for p in chart.purchases(mem.get('chapter') or 0))


def _shortage(mem, kit, items):
    """Copies to buy per card: measured stock + unseen purchases + this month's plan."""
    stock = mem.get('card_stock')
    if not isinstance(stock, dict):
        return {}
    unseen = _unseen(mem)
    planned = Counter()
    for row in items:
        if isinstance(row, (list, tuple)) and len(row) == 2 and type(row[1]) is int:
            planned[row[0]] += row[1]
    short = {}
    for card, need in kit.items():
        seen = stock.get(card)
        if type(seen) is not int or seen < 0:
            continue  # never measured: no evidence that the copy is missing
        bought = unseen.get(card, 0)
        have = seen + (bought if type(bought) is int and bought > 0 else 0) + planned[card]
        if have < need:
            short[card] = need - have
    return short


def plan_boss_card_rebuy(mem, shop):
    """Put a short boss card first in a new month's merchant list.

    Called from ``_plan_extras`` once per new month shop, after the egg hold
    and repair money are set and before the hiring fee and soldiers are sized.
    Only measured stock counts as missing (``card_stock`` 0 or below the kit).
    A month already planned, a finished merchant visit or a later charted
    purchase this chapter plans nothing.
    """
    if (not isinstance(shop, dict) or shop.get('merchant_done') or shop.get('boss_card_rebuy')
            or not isinstance(shop.get('items'), list)):
        return
    kit = boss_kit(mem)
    try:
        year, month = (int(part) for part in str(shop.get('key')).split('-'))
    except ValueError:
        return
    gold = shop.get('gold_start')
    if kit is None or type(gold) is not int:
        return
    header = {'year': year, 'month': month, 'gold': gold}
    step, need = kit
    short = _shortage(mem, need, shop['items'])
    if not short:
        return
    evidence = {'boss_step': step, 'kit': dict(need), 'short': dict(short), 'gold': gold,
                'card_stock': {c: (mem.get('card_stock') or {}).get(c) for c in need},
                'bought_unseen': {c: _unseen(mem)[c] for c in need if c in _unseen(mem)},
                'card_drop': list((mem.get('card_drop') or {}).get(step) or ())}
    if _chart_purchase_ahead(mem, header):
        shop['boss_card_rebuy'] = {'step': step, 'cards': [], 'deferred': 'chart_purchase_ahead'}
        _record(mem, 'boss_card_rebuy_deferred', month=shop['key'], chart_step=step,
                observed_metric=evidence, deviation_reason='chart_purchase_ahead',
                reason='ボス札の実在庫が不足しているが、この章の後の月にチャートの購入があるため予算を残す')
        return
    budget = _merchant_spendable(mem, shop, header)
    rows = []
    for card in need:  # charted kit order: the first card is used first in the boss battle
        price = KNOWN_PRICES.get(card)
        if card not in short or type(price) is not int or price <= 0 or budget is None:
            continue
        qty = min(short[card], budget // price)
        if qty > 0:
            rows.append([card, qty])
            budget -= qty * price
    evidence['spendable'] = _merchant_spendable(mem, shop, header)
    shop['boss_card_rebuy'] = {'step': step, 'cards': [list(r) for r in rows]}
    if not rows:
        shop['boss_card_rebuy']['deferred'] = 'insufficient_funds'
        _record(mem, 'boss_card_rebuy_deferred', month=shop['key'], chart_step=step,
                observed_metric=evidence, deviation_reason='insufficient_funds',
                reason='ボス札の実在庫が不足しているが、賃金予備・卵回復・修理費を残すと買えないため翌月に再判定する')
        return
    shop['items'][:0] = rows  # bought before charted/adjusted cards and extras
    if not shop.get('soldiers_done') and type(shop.get('soldiers')) is int:
        available = _soldier_spendable(mem, shop, header, recruit=False)
        if available is not None and available < shop['soldiers']:
            shop['soldiers'] = available
            shop['soldiers_done'] = available == 0
    _record(mem, 'boss_card_rebuy_plan', month=shop['key'], chart_step=step,
            strategy_variant='boss_card_rebuy', plan={'cards': [list(r) for r in rows],
                                                      'soldiers': shop.get('soldiers')},
            observed_metric=evidence,
            reason='ボス注文の切り札の実在庫が足りないため、賃金予備・卵回復・修理費を残した資金で商人から買い直す'
                   '（20人以下の兵士補充はこれより先）')


def note_card_purchase(mem, card, qty):
    """A confirmed merchant purchase of a boss-kit card returns it to the boss kit.

    The quote was matched and うむッ pressed (the same basis as ``bought``); the
    stock itself is read again on the next card panel. Removing the copy from
    ``card_drop`` lets the next boss sortie look for it; its scroll budget and
    miss count restart so a proven-absent copy is dropped again, not held for.
    """
    kit = boss_kit(mem)
    if kit is None or type(qty) is not int or qty <= 0 or card not in kit[1]:
        return
    step = kit[0]
    ledger = mem.get(UNSEEN)
    if not isinstance(ledger, dict):
        ledger = mem[UNSEEN] = {}
    previous = ledger.get(card, 0)
    ledger[card] = (previous if type(previous) is int and previous > 0 else 0) + qty
    dropped = (mem.get('card_drop') or {}).get(step)
    restored = 0
    while isinstance(dropped, list) and card in dropped and restored < qty:
        dropped.remove(card)
        restored += 1
    if restored:
        (mem.get('card_scroll') or {}).pop(step, None)
        (mem.get('card_miss') or {}).pop(f'{step}:{card}', None)
    _record(mem, 'boss_card_restored' if restored else 'boss_card_bought', chart_step=step,
            card=card, qty=qty,
            observed_metric={'restored': restored, 'card_drop': list(dropped or ()),
                             'bought_unseen': ledger[card],
                             'card_stock': (mem.get('card_stock') or {}).get(card)},
            reason=('買い直したボス札を外した札から戻し、次のボス出撃で携行する。在庫は次の札一覧で確認する'
                    if restored else 'ボス札の購入を記録し、札一覧で在庫を確認するまで重ねて買わない'))


def boss_card_reserved(mem, order, card):
    """A non-boss sortie may not add a copy the pending boss kit still needs.

    Strong cards are carried to strong generals (owner 2026-10-03); only the
    measured surplus beyond the base boss kit is spare for that (g825: the
    strong-card kit took 1-B1's クースカン in ordinary sorties).
    """
    if _is_boss_order(order, mem):
        return False
    kit = boss_kit(mem)
    if kit is None or card not in kit[1]:
        return False
    seen = (mem.get('card_stock') or {}).get(card)
    return type(seen) is int and seen <= kit[1][card]

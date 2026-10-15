"""戦略層: bounded extra strong cards, without spending necessity reserves.

The month plan may invite a merchant even without charted purchases. Prices
here are ceilings, not evidence of a vendor's stock: the shop flow must read
and recheck the actual listing and final quote before confirming anything.
"""
from __future__ import annotations

from ..decision_log import _record
from .economy import KNOWN_PRICES, _charted_purchase_ahead, _merchant_spendable


# Owner 2026-10-15: buy useful cheap cards from merchants/carts when cash is
# plentiful. Do not buy a weak card merely because it costs only one gold.
BARGAIN_TARGETS = {'ノリウツール': 3, 'クースカン': 3, 'ゼンマイン': 2, 'ミックミー': 2}
BARGAIN_CASH_BUFFER = 30
BARGAIN_MONTHLY_GOLD = 100
BARGAIN_MONTHLY_COPIES = 6


def bargain_spendable(mem, shop, header):
    """Recheck actual cash, pending essentials and this month's extra cap."""
    cash = _merchant_spendable(mem, shop, header)
    if cash is None or mem.get('recruit_payroll_pending'):
        return None
    soldiers = 0 if shop.get('soldiers_done') is True else shop.get('soldiers', 0)
    before_recruit = (0 if shop.get('soldiers_before_recruit_done') is True
                      else shop.get('soldiers_before_recruit', soldiers))
    if before_recruit is None:
        before_recruit = soldiers
    if any(type(n) is not int or n < 0 for n in (soldiers, before_recruit)):
        return None
    extras = shop.get('bargain_cards', [])
    if (not isinstance(extras, list) or len(extras) != len(set(extras))
            or any(not isinstance(name, str) or name not in BARGAIN_TARGETS for name in extras)):
        return None
    items, bought = shop.get('items', []), shop.get('bought', [])
    if not isinstance(items, list) or not isinstance(bought, list):
        return None
    committed = spent = 0
    for row in items:
        if (not isinstance(row, (list, tuple)) or len(row) != 2
                or not isinstance(row[0], str) or row[0] not in KNOWN_PRICES
                or type(row[1]) is not int or not 0 < row[1] <= 99):
            return None
        if row[0] not in extras:
            committed += KNOWN_PRICES[row[0]] * row[1]
    for row in bought:
        if (not isinstance(row, (list, tuple)) or len(row) != 3
                or not isinstance(row[0], str)
                or any(type(n) is not int or n <= 0 for n in row[1:])):
            return None
        if row[0] in extras:
            spent += row[1] * row[2]
    return max(0, min(BARGAIN_MONTHLY_GOLD - spent,
                      cash - max(soldiers, before_recruit) - committed - BARGAIN_CASH_BUFFER))


def plan_bargains(mem, shop, header):
    """Append once to a current plan; never re-arm a spent/rejected target.

    Saved observations are used conservatively to avoid overstocking, never
    updated as a purchase receipt. Unknown stock permits only the bounded
    target. Later charted purchases retain their existing reserved budget.
    """
    if (not isinstance(shop, dict) or shop.get('bargain_planned')
            or shop.get('merchant_done') or shop.get('closed') or shop.get('want')
            or mem.get('month_sub') or shop.get('soldier_receipt')
            or not isinstance(header, dict)
            or any(type(header.get(k)) is not int for k in ('year', 'month', 'gold'))):
        return shop
    budget = bargain_spendable(mem, shop, header)
    if budget is None or budget < min(KNOWN_PRICES[n] for n in BARGAIN_TARGETS):
        return shop
    if _charted_purchase_ahead(mem, header):
        return shop
    stock = mem.get('card_stock', {})
    if not isinstance(stock, dict):
        return shop
    items = shop.setdefault('items', [])
    queued = {name for name, _ in items}
    bought = {}
    for name, qty, _ in shop.get('bought', []):
        bought[name] = bought.get(name, 0) + qty
    added, copies = [], 0
    for name, target in BARGAIN_TARGETS.items():
        if name in queued:
            continue  # chart quantities and their priority remain untouched
        seen = stock.get(name, 0)
        if type(seen) is not int or seen < 0:
            continue  # malformed stock is not zero stock
        need = max(0, target - seen - bought.get(name, 0))
        qty = min(need, BARGAIN_MONTHLY_COPIES - copies, budget // KNOWN_PRICES[name])
        if qty:
            added.append([name, qty])
            budget -= qty * KNOWN_PRICES[name]
            copies += qty
    shop['bargain_planned'] = True
    shop['bargain_cards'] = [name for name, _ in added]
    if added:
        items.extend(added)
        _record(mem, 'bargain_plan', month=shop.get('key'),
                strategy_variant='spare_cash_strong_cards', plan={'cards': added},
                observed_metric={'cash_buffer': BARGAIN_CASH_BUFFER,
                                 'extra_gold_cap': BARGAIN_MONTHLY_GOLD,
                                 'extra_copy_cap': BARGAIN_MONTHLY_COPIES},
                reason='必要資金と予備費を残し、在庫不足の強力札を月次購入へ追加。実売価格は店内で再確認する')
    return shop

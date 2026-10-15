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
    if (not isinstance(extras, list)
            or any(not isinstance(name, str) or name not in BARGAIN_TARGETS for name in extras)
            or len(extras) != len(set(extras))):
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


def merchant_card_spendable(mem, shop, header, name, price):
    """Chart purchases keep their old gate; extras recheck the stricter one."""
    if name not in (shop.get('bargain_cards') or []):
        return _merchant_spendable(mem, shop, header)
    if (type(price) is not int or price <= 0 or name not in BARGAIN_TARGETS
            or price > KNOWN_PRICES[name]):
        return None
    cash = bargain_spendable(mem, shop, header)
    if cash is None or _charted_purchase_ahead(mem, header):
        return None
    return cash


def plan_bargains(mem, shop, header, *, listed=None):
    """Plan once for a visit, then once from the actual vendor's listing.

    Saved observations only avoid overstocking; they are never updated as a
    purchase receipt. The listing pass replaces only our provisional extras,
    so a cart selling a different strong card is not skipped just because an
    earlier provisional candidate was unavailable. Neither pass is rearmed
    after a rejected quote, a purchase, or JSON save/hotload.
    """
    if (not isinstance(shop, dict) or shop.get('merchant_done') or shop.get('closed')
            or shop.get('want') or mem.get('month_sub') or shop.get('soldier_receipt')
            or not isinstance(header, dict)
            or any(type(header.get(k)) is not int for k in ('year', 'month', 'gold'))
            or (listed is None and shop.get('bargain_planned'))
            or (listed is not None and shop.get('bargain_listing_planned'))):
        return shop
    budget = bargain_spendable(mem, shop, header)
    if budget is None or _charted_purchase_ahead(mem, header):
        return shop
    if listed is None and budget < min(KNOWN_PRICES[n] for n in BARGAIN_TARGETS):
        return shop
    if listed is not None and (not isinstance(listed, dict) or not listed
            or any(not isinstance(n, str) or type(p) is not int or p <= 0
                   for n, p in listed.items())):
        return shop
    stock = mem.get('card_stock', {})
    if not isinstance(stock, dict):
        return shop
    items = shop.setdefault('items', [])
    previous = shop.get('bargain_cards', [])
    # Only the first readable listing can revise the tentative visit plan.
    items[:] = [row for row in items if row[0] not in previous]
    queued = {name for name, _ in items}
    bought = {}
    for name, qty, _ in shop.get('bought', []):
        bought[name] = bought.get(name, 0) + qty
    added, copies = [], 0
    for name, target in BARGAIN_TARGETS.items():
        if name in queued or name in bought:
            continue  # chart quantities and already-attempted purchases stay untouched
        seen = stock.get(name, 0)
        price = KNOWN_PRICES[name] if listed is None else listed.get(name)
        if (type(seen) is not int or seen < 0 or type(price) is not int
                or not 0 < price <= KNOWN_PRICES[name]):
            continue
        need = max(0, target - seen)
        qty = min(need, BARGAIN_MONTHLY_COPIES - copies, budget // price)
        if qty:
            added.append([name, qty])
            budget -= qty * price
            copies += qty
    shop['bargain_planned'] = True
    if listed is not None:
        shop['bargain_listing_planned'] = True
    shop['bargain_cards'] = [name for name, _ in added]
    if added:
        items.extend(added)
        _record(mem, 'bargain_plan', month=shop.get('key'),
                strategy_variant='spare_cash_strong_cards', plan={'cards': added},
                observed_metric={'listing_seen': listed is not None,
                                 'cash_buffer': BARGAIN_CASH_BUFFER,
                                 'extra_gold_cap': BARGAIN_MONTHLY_GOLD,
                                 'extra_copy_cap': BARGAIN_MONTHLY_COPIES},
                reason='必要資金と予備費を残し、在庫不足の強力札を月次購入へ追加。実売価格は店内で再確認する')
    return shop

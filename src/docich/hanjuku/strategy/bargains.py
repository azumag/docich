"""戦略層: bounded extra strong cards, without spending necessity reserves.

The month plan may invite a merchant even without charted purchases. Prices
here are ceilings, not evidence of a vendor's stock: the shop flow must read
and recheck the actual listing and final quote before confirming anything.
"""
from __future__ import annotations

from ..decision_log import _record
from .economy import (KNOWN_PRICES, _charted_purchase_ahead,
                      _egg_recovery_targets, _merchant_spendable, low_army_first)


# Owner 2026-10-15: buy useful cheap cards from merchants/carts when cash is
# plentiful. Do not buy a weak card merely because it costs only one gold.
# Owner 2026-11-02: stock the useful vendor cards too, not only the cheap
# four — マグネガキン halves egg/boss attack (STRONG_CARDS), エンジェリン
# fully heals and refills the egg, ころぼぐんだん is the cheap egg-dropper
# of the recommended sets.
BARGAIN_TARGETS = {'ノリウツール': 3, 'クースカン': 3, 'ゼンマイン': 2, 'ミックミー': 2,
                   'マグネガキン': 2, 'エンジェリン': 2, 'ころぼぐんだん': 2}
BARGAIN_CASH_BUFFER = 30
BARGAIN_MONTHLY_GOLD = 200
BARGAIN_MONTHLY_COPIES = 8
# A cart that sells the strong cards at about half price after the month's own
# merchant visit (g1097 2026-11-03 00:57: クースカン12 ゼンマイン16 ミックミー20
# against 24/32/40, right after the monthly duel) is a chance, not a second
# month: the single listing pass of the month was already spent and the bot
# left in the same observation with 9987G unused. It gets one pass of its own
# with larger caps; every payment check below still applies.
CHEAP_VENDOR_RATIO = 0.6
CHEAP_VENDOR_GOLD = 1000
CHEAP_VENDOR_COPIES = 16
CHEAP_VENDOR_TARGET_FACTOR = 2


def _caps(shop):
    """(monthly gold, monthly copies, per-card target factor) for this visit."""
    if isinstance(shop, dict) and shop.get('cheap_vendor') is True:
        return CHEAP_VENDOR_GOLD, CHEAP_VENDOR_COPIES, CHEAP_VENDOR_TARGET_FACTOR
    return BARGAIN_MONTHLY_GOLD, BARGAIN_MONTHLY_COPIES, 1


def cheap_listing(listed):
    """Target cards the cart sells at or below CHEAP_VENDOR_RATIO of the known price."""
    if not isinstance(listed, dict):
        return []
    return [name for name in BARGAIN_TARGETS
            if type(listed.get(name)) is int and 0 < listed[name] <= KNOWN_PRICES[name] * CHEAP_VENDOR_RATIO]


def _egg_necessity_pending(mem, shop):
    """Egg recovery still owns the menu and its reserved cash."""
    return bool(shop.get('egg') not in ('done', 'not_needed')
                and (shop.get('egg_priority') or shop.get('egg_before_recruit')
                     or _egg_recovery_targets(mem)))


def _necessities_pending(mem, shop):
    # A spare-cash purchase must not take the menu away from recovery or
    # necessary recruitment. This gate stops a NEW extra from being planned;
    # an extra already planned before the recruit step may still be bought
    # when _merchant_spendable() keeps the fee (see merchant_card_spendable).
    recruit = (shop.get('recruit_priority')
               and shop.get('recruit') not in ('done', 'not_needed'))
    return bool(_egg_necessity_pending(mem, shop) or recruit)


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
    return max(0, min(_caps(shop)[0] - spent,
                      cash - max(soldiers, before_recruit) - committed - BARGAIN_CASH_BUFFER))


def merchant_card_spendable(mem, shop, header, name, price):
    """Chart purchases keep their old gate; extras recheck the stricter one."""
    extras = shop.get('bargain_cards', [])
    if (not isinstance(extras, list)
            or any(not isinstance(n, str) or n not in BARGAIN_TARGETS for n in extras)
            or len(extras) != len(set(extras))):
        return None
    if name not in extras:
        return _merchant_spendable(mem, shop, header)
    if (type(price) is not int or price <= 0 or name not in BARGAIN_TARGETS
            or price > KNOWN_PRICES[name] or _egg_necessity_pending(mem, shop)):
        return None
    cash = bargain_spendable(mem, shop, header)
    if cash is None or _charted_purchase_ahead(mem, header):
        return None
    # A saved quantity target is not authority to bypass the planning caps.
    bought = shop.get('bought', [])  # validated by bargain_spendable
    used = sum(q for n, q, _ in bought if n in extras)
    same = sum(q for n, q, _ in bought if n == name)
    _, copy_cap, factor = _caps(shop)
    copies = max(0, min(copy_cap - used, BARGAIN_TARGETS[name] * factor - same))
    return min(cash, copies * price)


def plan_bargains(mem, shop, header, *, listed=None):
    """Plan once for a visit, then once from the actual vendor's listing.

    Saved observations only avoid overstocking; they are never updated as a
    purchase receipt. The listing pass replaces only our provisional extras,
    so a cart selling a different strong card is not skipped just because an
    earlier provisional candidate was unavailable. Neither pass is rearmed
    after a rejected quote, a purchase, or JSON save/hotload.
    """
    cheap_pass = (isinstance(shop, dict) and listed is not None
                  and shop.get('bargain_listing_planned') is True
                  and shop.get('cheap_vendor_planned') is not True
                  and bool(cheap_listing(listed)))
    if (not isinstance(shop, dict) or shop.get('want') or mem.get('month_sub')
            or not isinstance(header, dict)
            or any(type(header.get(k)) is not int for k in ('year', 'month', 'gold'))
            or (not cheap_pass and (shop.get('merchant_done') or shop.get('closed')
                                    or (listed is None and shop.get('bargain_planned'))
                                    or (listed is not None and shop.get('bargain_listing_planned'))))):
        return shop
    if cheap_pass:
        shop['cheap_vendor'] = True          # caps of this pass; one pass per month
    receipt = shop.get('soldier_receipt')
    if (receipt and (not isinstance(receipt, dict) or receipt.get('status') != 'paid')):
        return shop
    if _necessities_pending(mem, shop) or low_army_first(shop):
        # A month refilling a low army has no spare cash, even if the 5G
        # ころぼぐんだん still fits above the buffer (g825 2-8: 127G, 14 soldiers).
        return shop
    budget = bargain_spendable(mem, shop, header)
    if budget is None or _charted_purchase_ahead(mem, header):
        return shop
    if listed is None and budget < min(KNOWN_PRICES[n] for n in BARGAIN_TARGETS):
        return shop
    if listed is not None and (not isinstance(listed, dict) or not listed
            or any(not isinstance(n, str) or type(p) is not int or p <= 0
                   for n, p in listed.items())):
        shop.pop('cheap_vendor', None)
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
    _, copy_cap, factor = _caps(shop)
    for name, target in BARGAIN_TARGETS.items():
        if name in queued or (name in bought and not cheap_pass):
            continue  # chart quantities and already-attempted purchases stay untouched
        seen = stock.get(name, 0) + (bought.get(name, 0) if cheap_pass else 0)
        target = target * factor
        price = KNOWN_PRICES[name] if listed is None else listed.get(name)
        if (type(seen) is not int or seen < 0 or type(price) is not int
                or not 0 < price <= KNOWN_PRICES[name]):
            continue
        need = max(0, target - seen)
        if not need and budget > 0:
            # Owner (2026-11-05): 目標在庫に達していても、チャートに関係なく
            # 余剰資金で買い足す。月次の金額・枚数上限と実売価格の確認は同じ。
            need = copy_cap
        qty = min(need, copy_cap - copies, budget // price)
        if qty:
            added.append([name, qty])
            budget -= qty * price
            copies += qty
    shop['bargain_planned'] = True
    if listed is not None:
        shop['bargain_listing_planned'] = True
    if cheap_pass:
        shop['cheap_vendor_planned'] = True
    shop['bargain_cards'] = [name for name, _ in added]
    if cheap_pass:
        # Earlier extras bought this month keep counting against the caps.
        shop['bargain_cards'] = list(dict.fromkeys([*shop['bargain_cards'], *previous]))
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

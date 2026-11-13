"""戦略層: bounded extra strong cards, without spending necessity reserves.

The month plan may invite a merchant even without charted purchases. Prices
here are ceilings, not evidence of a vendor's stock: the shop flow must read
and recheck the actual listing and final quote before confirming anything.
"""
from __future__ import annotations

import random

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
# Owner 2026-11-06: at 1000G+ stock all explicitly requested cards, while
# keeping cash for essential spending. Random other cards use only leftover cash.
RICH_MERCHANT_GOLD = 1000
RICH_MERCHANT_CARDS = (
    'ノリウツール', 'ミックミー', 'グリンボー', 'イッテツーン',
    'ころぼぐんだん', 'フットバース', 'ピッグローラー',
    'ファバード', 'エンジェリン',
)
RICH_MERCHANT_MAX_COPIES = 99
RICH_RANDOM_CARDS = tuple(name for name in KNOWN_PRICES
                          if name not in RICH_MERCHANT_CARDS and name not in BARGAIN_TARGETS)
RICH_RANDOM_MAX_COPIES = 8
RICH_RANDOM_MAX_KINDS = 3


def _rich_card(shop, name):
    random_cards = shop.get('rich_random_cards', [])
    return (shop.get('rich_merchant') is True
            and (name in RICH_MERCHANT_CARDS
                 or (isinstance(random_cards, list) and name in random_cards)))


# Owner 2026-11-06: each kind of card is held up to 99 copies. A merchant sells
# no more than that ("それいじょうもてないようですな"), so a plan that ignores
# what is already held is refused and, before #3589, froze the bot.
HOLD_CAP = 99
SINCE_READ = 'card_bought_since_read'   # copies bought after the last panel read
HELD_FLOOR = 'card_held_floor'          # holding proved by a merchant refusal


def _count(table, name):
    value = table.get(name) if isinstance(table, dict) else None
    return value if type(value) is int and value > 0 else 0


def held_estimate(mem, name):
    """Copies of ``name`` held now: last panel read + purchases since, never below a refusal's proof.

    The panel is only read on a sortie, so merchant purchases do not show in
    ``card_stock`` for a while; counting them here keeps a second visit from
    asking for a full 99 on top of them.
    """
    stock = _count(mem.get('card_stock'), name)
    return max(stock + _count(mem.get(SINCE_READ), name), _count(mem.get(HELD_FLOOR), name))


def note_card_bought(mem, name, qty):
    if type(qty) is int and qty > 0 and isinstance(name, str):
        ledger = mem.setdefault(SINCE_READ, {})
        ledger[name] = min(_count(ledger, name) + qty, 10 ** 4)


def hold_room(mem, name):
    """Copies of ``name`` the hand can still take (merchant purchases included)."""
    return max(0, HOLD_CAP - held_estimate(mem, name))


def _rich_room(mem, name, stock, bought):
    """Copies a rich merchant may still sell: the 99 assumption, then any learned ceiling."""
    room = RICH_MERCHANT_MAX_COPIES - stock.get(name, 0) - bought.get(name, 0)
    return min(room, hold_room(mem, name))


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


def bargain_spendable(mem, shop, header, *, rich=False):
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
    random_cards = shop.get('rich_random_cards', [])
    if (not isinstance(extras, list)
            or any(not isinstance(name, str) or name not in KNOWN_PRICES for name in extras)
            or len(extras) != len(set(extras))
            or not isinstance(random_cards, list)
            or any(not isinstance(name, str) or name not in RICH_RANDOM_CARDS
                   for name in random_cards)
            or len(random_cards) != len(set(random_cards))):
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
        if row[0] in extras and not _rich_card(shop, row[0]):
            spent += row[1] * row[2]
    funds = cash - max(soldiers, before_recruit) - committed - BARGAIN_CASH_BUFFER
    if rich:
        return max(0, funds) if shop.get('rich_merchant') is True else None
    return max(0, min(_caps(shop)[0] - spent, funds))


def merchant_card_spendable(mem, shop, header, name, price):
    """Chart purchases keep their old gate; extras recheck the stricter one."""
    extras = shop.get('bargain_cards', [])
    if (not isinstance(extras, list)
            or any(not isinstance(n, str) or n not in KNOWN_PRICES for n in extras)
            or len(extras) != len(set(extras))):
        return None
    if name not in extras:
        return _merchant_spendable(mem, shop, header)
    if (type(price) is not int or price <= 0 or name not in KNOWN_PRICES
            or price > KNOWN_PRICES[name] or _egg_necessity_pending(mem, shop)):
        return None
    rich = _rich_card(shop, name)
    cash = bargain_spendable(mem, shop, header, rich=rich)
    if cash is None or _charted_purchase_ahead(mem, header):
        return None
    # Revalidate quantities at the real quote, independently of cached plans.
    bought = shop.get('bought', [])  # validated by bargain_spendable
    used = sum(q for n, q, _ in bought if n in extras and not _rich_card(shop, n))
    same = sum(q for n, q, _ in bought if n == name)
    _, copy_cap, factor = _caps(shop)
    copies = (max(0, (RICH_RANDOM_MAX_COPIES if name in shop.get('rich_random_cards', [])
                      else RICH_MERCHANT_MAX_COPIES) - same) if rich else
              max(0, min(copy_cap - used, BARGAIN_TARGETS[name] * factor - same)))
    copies = min(copies, hold_room(mem, name))
    return min(cash, copies * price)


def plan_bargains(mem, shop, header, *, listed=None):
    """Plan once for a visit, then once from the actual vendor's listing.

    Saved observations only avoid overstocking; they are never updated as a
    purchase receipt. The listing pass replaces only our provisional extras,
    so a cart selling a different strong card is not skipped just because an
    earlier provisional candidate was unavailable. Neither pass is rearmed
    after a rejected quote, a purchase, or JSON save/hotload.
    """
    # The optional card shopping must not hold the field while a measured
    # near-dead defender is waiting for its finish sortie (g1163 2026-11-13
    # 22:2x: オオキク's defender sat at 3 HP through the 40-11 month routine
    # and its shopping). The finish is an opportunity the defender's heal/
    # replacement can close; the cards can wait for next month.
    from .corridor import finish_pending
    if finish_pending(mem):
        return shop
    if (isinstance(shop, dict) and isinstance(header, dict)
            and type(header.get('gold')) is int and header['gold'] >= RICH_MERCHANT_GOLD):
        shop['rich_merchant'] = True  # latched: the later quote may show less than 1000G
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
    rich = shop.get('rich_merchant') is True
    rich_budget = bargain_spendable(mem, shop, header, rich=True) if rich else 0
    if budget is None or (rich and rich_budget is None) or _charted_purchase_ahead(mem, header):
        return shop
    if listed is None and budget < min(KNOWN_PRICES[n] for n in BARGAIN_TARGETS) and not rich_budget:
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
    # Basis the plan actually used per card (panel stock plus, where the pass
    # counts them, this visit's buys); logged so a refusal can be checked
    # against what the planner believed was held.
    seen_basis = {}
    # On the observed listing, buy both requested cards before optional extras.
    # Pair purchases prevent the cheaper card from monopolizing the budget.
    # The observed listing is authoritative. Round-robin the requested kinds
    # so a cheap first item cannot consume all cash before other requested
    # kinds get a chance. Stop at 99 held copies of each, or the real budget.
    if rich and listed is not None:
        choices = []
        for name in RICH_MERCHANT_CARDS:
            price, seen = listed.get(name), stock.get(name, 0)
            if (name not in queued and type(price) is int and 0 < price <= KNOWN_PRICES[name]
                    and type(seen) is int and 0 <= seen < RICH_MERCHANT_MAX_COPIES
                    and _rich_room(mem, name, stock, bought) > 0):
                seen_basis[name] = seen + bought.get(name, 0)
                choices.append(name)
        amounts = {name: 0 for name in choices}
        remaining = rich_budget
        while True:
            changed = False
            for name in choices:
                if (amounts[name] < _rich_room(mem, name, stock, bought)
                        and remaining >= listed[name]):
                    amounts[name] += 1
                    remaining -= listed[name]
                    changed = True
            if not changed:
                break
        for name in choices:
            if amounts[name]:
                added.append([name, amounts[name]])
                queued.add(name)

        # Optional random purchases are allowed only after every available
        # requested card has exhausted its budget/holding room. A stable seed
        # keeps retries and hotload from changing the chosen shopping list.
        random_names = list(shop.get('rich_random_cards', []))
        candidates = [
            name for name in sorted(listed) if name in RICH_RANDOM_CARDS
            and name not in queued and name not in bought
            and type(listed[name]) is int and 0 < listed[name] <= KNOWN_PRICES[name]
            and hold_room(mem, name) > 0
        ]
        rng = random.Random(f"{shop.get('key')}:{mem.get('chapter')}:{shop.get('gold_start')}")
        rng.shuffle(candidates)
        for name in candidates:
            if len(random_names) >= RICH_RANDOM_MAX_KINDS:
                break
            qty = min(RICH_RANDOM_MAX_COPIES, hold_room(mem, name),
                      remaining // listed[name])
            if qty:
                added.append([name, qty])
                queued.add(name)
                random_names.append(name)
                remaining -= qty * listed[name]
        shop['rich_random_cards'] = random_names
        budget = max(0, budget - (rich_budget - remaining))
    for name, target in BARGAIN_TARGETS.items():
        if rich and listed is not None and name in RICH_MERCHANT_CARDS:
            continue
        if name in queued or (name in bought and not cheap_pass):
            continue  # chart quantities and already-attempted purchases stay untouched
        seen = stock.get(name, 0) + (bought.get(name, 0) if cheap_pass else 0)
        seen_basis[name] = seen
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
        qty = min(qty, hold_room(mem, name))
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
                                 'extra_copy_cap': BARGAIN_MONTHLY_COPIES,
                                 'seen': dict(seen_basis),
                                 'planned_cards_seen_total': sum(seen_basis.values())},
                reason='必要資金と予備費を残し、在庫不足の強力札を月次購入へ追加。実売価格は店内で再確認する')
    return shop

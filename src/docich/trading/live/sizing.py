"""Pure limit-order sizing: LIVE buy/sell candidates -> limit-order plans (#3623, L4b-4).

``size_orders`` turns the candidates ``signals.scan_live`` produced (``LiveSignals``) into
limit orders for the executor, using the activated policy snapshot, one market view per
symbol (rules, order book, last close) and the funds/inventory the caller looked up.

It is PURE: it reads its arguments only.  No ledger, no exchange reader, no network, no
clock (``now`` is a parameter), no file writes and no module-level mutable state.  The
caller does the ledger lookups and passes plain values in: ``decided_keys`` is the set of
decisions the executor already handled, and ``open_buy_symbols`` / ``open_sell_symbols``
are the symbols with an open intent on that side (the ledger allows at most one open
intent per (symbol, side)), so a second order there is skipped.  ``legacy_position_symbols``
are the symbols with an open position entered under another policy digest, so a new buy
there would open a second position (only buys are skipped); ``ambiguous_position_symbols``
are the symbols with two or more open positions, so nothing at all is planned for them.

A sell carries the digest its position was entered under (``HeldPosition.entry_policy_digest``)
when the caller supplies it, else the current policy digest; the decision key uses that
digest, because the ledger's ``sell_uses_entry_policy`` trigger refuses any other one.

``markets``' order books (``OrderBook``) must already be freshness-filtered by the caller;
this module does not judge freshness.

Money and amounts are ``Decimal`` (never ``float``).  The only floats here are the ``now``
parameter and ``Opportunity.expires_at`` timestamps.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, AbstractSet, Mapping

from ..models import SkipDecision
from .exchange import MarketRules, OrderBook, dec, dec_text
from .ledger import IntentRequest

if TYPE_CHECKING:  # imported for annotations only: keeps this module free of the PAPER graph
    from .policy import LivePolicySet
    from .signals import LiveSignals

ZERO = Decimal("0")
ONE = Decimal("1")
BASIS_POINTS = Decimal("10000")

# Every reason code a skip in this module can carry (documented closed set).
SKIP_CODES = frozenset({
    "expired",
    "no_policy",
    "no_position",
    "duplicate_exit",
    "market_unavailable",
    "sell_attempts_exhausted",
    "no_inventory",
    "below_min_amount",
    "exchange_free_short",
    "book_empty",
    "book_off_tick",
    "depth_insufficient",
    "buys_blocked_safety",
    "already_decided",
    "open_buy_exists",
    "open_sell_exists",
    "no_capital_recorded",
    "fee_rate_unknown",
    "book_far_from_signal",
    "total_cap_exhausted",
    "insufficient_funds",
    "legacy_position_open",
    "ambiguous_position",
})


@dataclass(frozen=True)
class SizingSettings:
    buy_premium_cap_bps: Decimal = Decimal("50")
    order_ttl_s: int = 45
    max_sell_attempts: int = 5

    def __post_init__(self) -> None:
        bps = self.buy_premium_cap_bps
        if isinstance(bps, bool) or not isinstance(bps, Decimal):
            raise ValueError("buy_premium_cap_bps must be a Decimal")
        try:
            bps = dec(bps)
        except ValueError as exc:
            raise ValueError(f"buy_premium_cap_bps: {exc}") from exc
        if bps < 0:
            raise ValueError("buy_premium_cap_bps must be non-negative")
        object.__setattr__(self, "buy_premium_cap_bps", bps)
        for name in ("order_ttl_s", "max_sell_attempts"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be an int >= 1")


@dataclass(frozen=True)
class SymbolMarket:
    rules: MarketRules
    book: OrderBook
    last_close: Decimal                 # close of the signal frame


@dataclass(frozen=True)
class HeldPosition:
    position_id: str
    net_base: Decimal                   # ledger quantity (views.CostBasis.net_base)
    free_base: Decimal                  # exchange free balance of the base currency
    entry_policy_digest: str | None = None  # the digest the position was entered under, if known


@dataclass(frozen=True)
class Funds:
    capital: Decimal                    # C: live JPY (free+locked) + position cost
    deployed: Decimal                   # D: cost of open positions
    free_quote: Decimal                 # F: usable JPY (caller already took the min)


@dataclass(frozen=True)
class OrderPlan:
    decision_key: str
    opportunity_id: str
    symbol: str
    side: str                           # "buy" | "sell"
    amount: Decimal
    price: Decimal
    order_type: str                     # always "limit"
    policy_digest: str                  # the FULL policy.digest
    rule_id: str                        # opportunity.strategy_id.rsplit(":", 1)[-1]
    position_id: str | None             # sells only
    required_quote: Decimal             # buys: amount*price*(1+fee)+quote_unit; sells: 0
    ttl_s: int
    reason_code: str                    # opportunity.reason_code
    notes: tuple[str, ...]              # e.g. ("depth_insufficient",), ("min_amount_rounded_up",)

    def intent_request(self) -> IntentRequest:
        return IntentRequest(
            decision_key=self.decision_key,
            symbol=self.symbol,
            side=self.side,
            order_type="limit",
            amount=dec_text(self.amount),
            policy_digest=self.policy_digest,
            rule_id=self.rule_id,
            price=dec_text(self.price),
        )


@dataclass(frozen=True)
class SizingResult:
    sells: tuple[OrderPlan, ...]
    buys: tuple[OrderPlan, ...]
    skipped: tuple[SkipDecision, ...]
    residual_position_ids: tuple[str, ...]   # positions whose ledger quantity is dust (S6)
    headroom_left: Decimal              # remaining "deployed headroom" after all plans (>= 0)
    free_quote_left: Decimal            # F_rest after all buys


def floor_step(value: Decimal, step: Decimal) -> Decimal:
    """Largest multiple of ``step`` not above ``value`` (``value <= 0`` -> 0)."""
    if value <= 0:
        return ZERO
    return (value // step) * step


def ceil_step(value: Decimal, step: Decimal) -> Decimal:
    """Smallest multiple of ``step`` not below ``value``."""
    floored = floor_step(value, step)
    return floored + step if floored < value else floored


def floor_tick(price: Decimal, tick: Decimal) -> Decimal:
    """Largest multiple of ``tick`` not above ``price`` (a limit price is never rounded up)."""
    return floor_step(price, tick)


class _BookUnusable(Exception):
    """The usable side of a book cannot produce a limit price (skip code in ``code``)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class _RuleValues:
    amount_step: Decimal
    min_amount: Decimal
    min_lot: Decimal                     # effective minimum lot: max(min_amount, amount_step)
    price_tick: Decimal
    quote_unit: Decimal
    fee_rate: Decimal | None


def _decimal(value: object, name: str) -> Decimal:
    """Strict Decimal: a float/bool/non-Decimal is refused, the result must be finite."""
    if isinstance(value, bool) or not isinstance(value, Decimal):
        raise ValueError(f"{name} must be a Decimal")
    try:
        return dec(value)
    except ValueError as exc:
        raise ValueError(f"{name}: {exc}") from exc


def _rules_values(rules: MarketRules) -> _RuleValues:
    try:
        amount_step = dec(rules.amount_step)
        min_amount = dec(rules.min_amount)
        price_tick = dec(rules.price_tick)
        quote_unit = dec(rules.quote_unit)
        fee_rate = None if rules.max_fee_rate is None else abs(dec(rules.max_fee_rate))
    except ValueError as exc:
        raise ValueError(f"market rules: {exc}") from exc
    if amount_step <= 0 or price_tick <= 0:
        raise ValueError("amount_step and price_tick must be positive")
    if min_amount < 0 or quote_unit < 0:
        raise ValueError("min_amount and quote_unit must be non-negative")
    # A zero exchange minimum must not turn into a zero-amount order: the smallest
    # executable lot is always at least one amount step.
    min_lot = max(min_amount, amount_step)
    return _RuleValues(
        amount_step=amount_step,
        min_amount=min_amount,
        min_lot=min_lot,
        price_tick=price_tick,
        quote_unit=quote_unit,
        fee_rate=fee_rate,
    )


def _usable_levels(levels, *, tick: Decimal, descending: bool) -> tuple[tuple[Decimal, Decimal], ...]:
    """Sort, drop non-positive amounts, refuse off-tick prices and merge equal prices."""
    ordered = sorted(((dec(price), dec(amount)) for price, amount in levels),
                     key=lambda level: level[0], reverse=descending)
    usable = [(price, amount) for price, amount in ordered if amount > 0]
    if not usable:
        raise _BookUnusable("book_empty")
    for price, _amount in usable:
        if price <= 0 or price % tick != 0:
            raise _BookUnusable("book_off_tick")
    merged: list[tuple[Decimal, Decimal]] = []
    for price, amount in usable:
        if merged and merged[-1][0] == price:
            merged[-1] = (price, merged[-1][1] + amount)
        else:
            merged.append((price, amount))
    return tuple(merged)


def _skip(opportunity, reason_code: str, side: str) -> SkipDecision:
    return SkipDecision(
        opportunity_id=opportunity.opportunity_id,
        symbol=opportunity.symbol,
        reason_code=reason_code,
        side=side,
    )


def _rule_id(opportunity) -> str:
    return opportunity.strategy_id.rsplit(":", 1)[-1]


def _attempts(sell_attempts: Mapping[str, int] | None, opportunity_id: str) -> int:
    if sell_attempts is None:
        return 0
    value = sell_attempts.get(opportunity_id, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"sell_attempts[{opportunity_id}] must be an int >= 0")
    return value


def size_orders(
    signals: LiveSignals,
    policy_set: LivePolicySet,
    markets: Mapping[str, SymbolMarket],
    held: Mapping[str, HeldPosition],
    funds: Funds,
    *,
    now: float,
    decided_keys: AbstractSet[str] = frozenset(),
    open_buy_symbols: AbstractSet[str] = frozenset(),
    open_sell_symbols: AbstractSet[str] = frozenset(),
    legacy_position_symbols: AbstractSet[str] = frozenset(),
    ambiguous_position_symbols: AbstractSet[str] = frozenset(),
    sell_attempts: Mapping[str, int] | None = None,
    buys_blocked: bool = False,
    settings: SizingSettings = SizingSettings(),
) -> SizingResult:
    # ---- input validation: exact Decimals only -----------------------------------------
    capital = _decimal(funds.capital, "funds.capital")
    deployed = _decimal(funds.deployed, "funds.deployed")
    free_quote = _decimal(funds.free_quote, "funds.free_quote")
    for name, value in (("capital", capital), ("deployed", deployed), ("free_quote", free_quote)):
        if value < 0:
            raise ValueError(f"funds.{name} must be non-negative")

    positions: dict[str, HeldPosition] = {}
    for symbol, position in held.items():
        net_base = _decimal(position.net_base, f"held[{symbol}].net_base")
        free_base = _decimal(position.free_base, f"held[{symbol}].free_base")
        if net_base < 0 or free_base < 0:
            raise ValueError(f"held[{symbol}] amounts must be non-negative")
        entry_digest = position.entry_policy_digest
        if entry_digest is not None and (not isinstance(entry_digest, str) or not entry_digest):
            raise ValueError(f"held[{symbol}].entry_policy_digest must be a non-empty str or None")
        positions[symbol] = position

    last_closes: dict[str, Decimal] = {}
    for symbol, market in markets.items():
        last_closes[symbol] = _decimal(market.last_close, f"markets[{symbol}].last_close")
        values = _rules_values(market.rules)
        for levels, descending in ((market.book.bids, True), (market.book.asks, False)):
            try:
                _usable_levels(levels, tick=values.price_tick, descending=descending)
            except _BookUnusable:
                pass  # a malformed/unusable book becomes a skip, not an input error

    skipped: list[SkipDecision] = []
    residual_ids: list[str] = []
    residual_seen: set[str] = set()
    sells: list[OrderPlan] = []
    # Signed on purpose: when deployed exceeds capital the overshoot must survive, so a
    # sale restores only the headroom that is actually missing (it cannot manufacture
    # fresh headroom).  The negative value is clamped only in the reported result.
    headroom = capital - deployed
    free_rest = free_quote

    # ---- sells first: a planned sale restores INVESTMENT-LIMIT headroom -----------------
    ordered_exits = sorted(signals.exits, key=lambda item: (-item.score, item.opportunity_id))
    exit_symbols: set[str] = set()
    for opportunity in ordered_exits:
        symbol = opportunity.symbol
        if symbol in exit_symbols:
            skipped.append(_skip(opportunity, "duplicate_exit", "sell"))
            continue
        exit_symbols.add(symbol)

        if opportunity.expires_at <= now:
            skipped.append(_skip(opportunity, "expired", "sell"))
            continue
        policy = policy_set.policies.get(symbol)
        if policy is None:
            skipped.append(_skip(opportunity, "no_policy", "sell"))
            continue
        if symbol in ambiguous_position_symbols:
            skipped.append(_skip(opportunity, "ambiguous_position", "sell"))
            continue
        position = positions.get(symbol)
        if position is None:
            skipped.append(_skip(opportunity, "no_position", "sell"))
            continue
        # One open intent per (symbol, side) is a ledger invariant: a sell that is still
        # open (visible-depth cut or an ambiguous cancel) must not be planned twice.
        if symbol in open_sell_symbols:
            skipped.append(_skip(opportunity, "open_sell_exists", "sell"))
            continue
        market = markets.get(symbol)
        if market is None or last_closes[symbol] <= 0:
            skipped.append(_skip(opportunity, "market_unavailable", "sell"))
            continue
        attempts = _attempts(sell_attempts, opportunity.opportunity_id)
        if attempts >= settings.max_sell_attempts:
            skipped.append(_skip(opportunity, "sell_attempts_exhausted", "sell"))
            continue
        # A sell whose decision was already handled must not be planned again; the key
        # includes the attempt number, so a retry with a fresh attempt still goes through.
        # The ledger's sell_uses_entry_policy trigger requires the digest the position was
        # entered under, so both the plan and its key use that digest when it is known.
        digest_used = (position.entry_policy_digest
                       if position.entry_policy_digest is not None else policy.digest)
        decision_key = (
            f"live1:{digest_used[:16]}:{opportunity.opportunity_id}:"
            f"{position.position_id[:8]}:a{attempts}"
        )
        if decision_key in decided_keys:
            skipped.append(_skip(opportunity, "already_decided", "sell"))
            continue

        rules = _rules_values(market.rules)
        net_base = _decimal(position.net_base, f"held[{symbol}].net_base")
        free_base = _decimal(position.free_base, f"held[{symbol}].free_base")
        if net_base <= 0:
            skipped.append(_skip(opportunity, "no_inventory", "sell"))
            continue
        if floor_step(net_base, rules.amount_step) < rules.min_lot:
            if position.position_id not in residual_seen:
                residual_seen.add(position.position_id)
                residual_ids.append(position.position_id)
            skipped.append(_skip(opportunity, "below_min_amount", "sell"))
            continue
        sellable = floor_step(min(net_base, free_base), rules.amount_step)
        if sellable <= 0 or sellable < rules.min_lot:
            skipped.append(_skip(opportunity, "exchange_free_short", "sell"))
            continue

        try:
            bids = _usable_levels(market.book.bids, tick=rules.price_tick, descending=True)
        except _BookUnusable as exc:
            skipped.append(_skip(opportunity, exc.code, "sell"))
            continue

        accumulated = ZERO
        limit = bids[-1][0]
        reached = False
        for price, level_amount in bids:
            accumulated += level_amount
            limit = price
            if accumulated >= sellable:
                reached = True
                break
        if reached:
            amount = sellable
            notes: tuple[str, ...] = ()
        else:
            amount = floor_step(accumulated, rules.amount_step)
            limit = bids[-1][0]
            notes = ("depth_insufficient",)
            if amount <= 0 or amount < rules.min_lot:
                skipped.append(_skip(opportunity, "depth_insufficient", "sell"))
                continue

        notional = amount * limit
        sells.append(OrderPlan(
            decision_key=decision_key,
            opportunity_id=opportunity.opportunity_id,
            symbol=symbol,
            side="sell",
            amount=amount,
            price=limit,
            order_type="limit",
            policy_digest=digest_used,
            rule_id=_rule_id(opportunity),
            position_id=position.position_id,
            required_quote=ZERO,
            ttl_s=settings.order_ttl_s,
            reason_code=opportunity.reason_code,
            notes=tuple(sorted(notes)),
        ))
        # Proceeds can fund replacement buys in the investment-limit sense only (and only
        # once they clear any overshoot); they do NOT raise free_quote, because a limit
        # sell may not fill this cycle.
        headroom = min(capital, headroom + notional)

    # ---- buys second --------------------------------------------------------------------
    buys: list[OrderPlan] = []
    planned_buy_symbols: set[str] = set()
    ordered_entries = sorted(signals.entries, key=lambda item: (-item.score, item.opportunity_id))
    for opportunity in ordered_entries:
        symbol = opportunity.symbol
        if buys_blocked:
            skipped.append(_skip(opportunity, "buys_blocked_safety", "buy"))
            continue
        if opportunity.expires_at <= now:
            skipped.append(_skip(opportunity, "expired", "buy"))
            continue
        policy = policy_set.policies.get(symbol)
        if policy is None:
            skipped.append(_skip(opportunity, "no_policy", "buy"))
            continue
        decision_key = f"live1:{policy.digest[:16]}:{opportunity.opportunity_id}"
        if decision_key in decided_keys:
            skipped.append(_skip(opportunity, "already_decided", "buy"))
            continue
        if symbol in ambiguous_position_symbols:
            skipped.append(_skip(opportunity, "ambiguous_position", "buy"))
            continue
        if symbol in legacy_position_symbols:
            skipped.append(_skip(opportunity, "legacy_position_open", "buy"))
            continue
        if symbol in open_buy_symbols or symbol in planned_buy_symbols:
            skipped.append(_skip(opportunity, "open_buy_exists", "buy"))
            continue
        if capital <= 0:
            # C is the live JPY plus the positions' cost.  Zero means there is no JPY on
            # the exchange at all, so there is nothing to spend on a new buy.
            skipped.append(_skip(opportunity, "no_capital_recorded", "buy"))
            continue
        market = markets.get(symbol)
        if market is None or last_closes[symbol] <= 0:
            skipped.append(_skip(opportunity, "market_unavailable", "buy"))
            continue
        rules = _rules_values(market.rules)
        if rules.fee_rate is None:
            skipped.append(_skip(opportunity, "fee_rate_unknown", "buy"))
            continue
        fee = rules.fee_rate
        try:
            asks = _usable_levels(market.book.asks, tick=rules.price_tick, descending=False)
        except _BookUnusable as exc:
            skipped.append(_skip(opportunity, exc.code, "buy"))
            continue
        premium_cap_price = floor_tick(
            last_closes[symbol] * (ONE + settings.buy_premium_cap_bps / BASIS_POINTS),
            rules.price_tick,
        )
        kept = tuple(level for level in asks if level[0] <= premium_cap_price)
        if not kept:
            skipped.append(_skip(opportunity, "book_far_from_signal", "buy"))
            continue
        if headroom <= 0:
            skipped.append(_skip(opportunity, "total_cap_exhausted", "buy"))
            continue
        if free_rest <= rules.quote_unit:
            skipped.append(_skip(opportunity, "insufficient_funds", "buy"))
            continue

        policy_fraction = dec(policy.max_opportunity_fraction)
        cap = capital * min(policy_fraction, opportunity.max_notional_fraction)
        hard_cap = capital * policy_fraction
        funding_cap = (free_rest - rules.quote_unit) / (ONE + fee)
        target = min(cap, headroom, funding_cap)

        accumulated = ZERO
        limit = kept[-1][0]
        reached = False
        for price, level_amount in kept:
            accumulated += price * level_amount
            limit = price
            if accumulated >= target:
                reached = True
                break
        notes = ()
        if reached:
            amount0 = floor_step(target / limit, rules.amount_step)
            visible = sum((amount for price, amount in kept if price <= limit), ZERO)
            amount0 = min(amount0, floor_step(visible, rules.amount_step))
        else:
            limit = kept[-1][0]
            visible = sum((amount for _price, amount in kept), ZERO)
            amount0 = floor_step(min(target / limit, visible), rules.amount_step)
            notes = ("depth_insufficient",)

        executable_min = ceil_step(rules.min_lot, rules.amount_step)
        if amount0 >= executable_min:
            amount = amount0
            plan_limit = limit
        else:
            # Owner decision: round up to the exchange minimum when the target is smaller.
            amount = executable_min
            supplied = ZERO
            limit1 = kept[-1][0]
            enough = False
            for price, level_amount in kept:
                supplied += level_amount
                limit1 = price
                if supplied >= executable_min:
                    enough = True
                    break
            if not enough:
                skipped.append(_skip(opportunity, "depth_insufficient", "buy"))
                continue
            notional_min = executable_min * limit1
            if notional_min > hard_cap:
                skipped.append(_skip(opportunity, "below_min_amount", "buy"))
                continue
            if notional_min > headroom:
                skipped.append(_skip(opportunity, "total_cap_exhausted", "buy"))
                continue
            if notional_min * (ONE + fee) + rules.quote_unit > free_rest:
                skipped.append(_skip(opportunity, "insufficient_funds", "buy"))
                continue
            plan_limit = limit1
            notes = notes + ("min_amount_rounded_up",)

        notional = amount * plan_limit
        required_quote = notional * (ONE + fee) + rules.quote_unit
        if required_quote > free_rest:  # by construction this cannot happen
            raise AssertionError(
                f"sizing exceeded the funding cap for {opportunity.opportunity_id}"
            )
        buys.append(OrderPlan(
            decision_key=decision_key,
            opportunity_id=opportunity.opportunity_id,
            symbol=symbol,
            side="buy",
            amount=amount,
            price=plan_limit,
            order_type="limit",
            policy_digest=policy.digest,
            rule_id=_rule_id(opportunity),
            position_id=None,
            required_quote=required_quote,
            ttl_s=settings.order_ttl_s,
            reason_code=opportunity.reason_code,
            notes=tuple(sorted(notes)),
        ))
        free_rest -= required_quote
        headroom -= notional
        planned_buy_symbols.add(symbol)

    return SizingResult(
        sells=tuple(sells),
        buys=tuple(buys),
        skipped=tuple(skipped),
        residual_position_ids=tuple(residual_ids),
        headroom_left=max(ZERO, headroom),  # report only: the internal value stays signed
        free_quote_left=free_rest,
    )

"""Capital allocation and fail-closed risk gates for paper crypto trading."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import time
from typing import Mapping, Sequence

from .models import (
    AllocationDecision,
    AllocationResult,
    MarketInfo,
    Opportunity,
    SkipDecision,
    TradingValidationError,
    as_decimal,
)


ZERO = Decimal("0")
DEFAULT_FRACTION = Decimal("0.30")


@dataclass(frozen=True)
class CapitalPolicy:
    max_opportunity_fraction: Decimal = DEFAULT_FRACTION
    max_total_deployed_fraction: Decimal = DEFAULT_FRACTION

    def __post_init__(self) -> None:
        for name in ("max_opportunity_fraction", "max_total_deployed_fraction"):
            value = as_decimal(getattr(self, name), name)
            if value <= 0 or value > 1:
                raise TradingValidationError(f"{name} must be in (0, 1]")
            object.__setattr__(self, name, value)


def _skip(opportunity: Opportunity, reason_code: str) -> SkipDecision:
    return SkipDecision(
        opportunity_id=opportunity.opportunity_id,
        symbol=opportunity.symbol,
        reason_code=reason_code,
    )


def _positive(mapping: Mapping[str, Decimal], key: str) -> Decimal | None:
    if key not in mapping:
        return None
    value = as_decimal(mapping[key], key)
    return value if value > 0 else None


def _round_down(amount: Decimal, step: Decimal | None) -> Decimal:
    if step is None:
        return amount
    if amount <= 0:
        return ZERO
    return (amount // step) * step


def _round_up(amount: Decimal, step: Decimal | None) -> Decimal:
    if step is None or amount <= 0:
        return amount
    rounded = (amount // step) * step
    return rounded if rounded >= amount else rounded + step


def _market_min_amount(market: MarketInfo, price: Decimal) -> Decimal | None:
    """Smallest base amount that satisfies the market's amount and cost floors."""
    required: Decimal | None = None
    if market.min_amount is not None and market.min_amount > 0:
        required = market.min_amount
    if market.min_cost is not None and market.min_cost > 0 and price > 0:
        cost_amount = market.min_cost / price
        required = cost_amount if required is None else max(required, cost_amount)
    return required


def allocate_opportunities(
    opportunities: Sequence[Opportunity],
    *,
    markets: Mapping[str, MarketInfo],
    prices: Mapping[str, Decimal],
    quote_to_reference: Mapping[str, Decimal],
    available_quote: Mapping[str, Decimal],
    capital_reference: Decimal,
    deployed_reference: Decimal,
    available_base: Mapping[str, Decimal] | None = None,
    policy: CapitalPolicy | None = None,
    now: float | None = None,
) -> AllocationResult:
    """Allocate opportunities without exceeding capital or funding-asset limits.

    The allocator never invents FX/crypto conversion routes. A non-reference
    quote asset must have both an explicit valuation rate and an available
    balance. Inventory-aware sells are processed first so released capacity can
    fund replacement buys in the same pass.
    """
    policy = policy or CapitalPolicy()
    capital = as_decimal(capital_reference, "capital_reference")
    deployed = as_decimal(deployed_reference, "deployed_reference")
    if capital < 0 or deployed < 0:
        raise TradingValidationError("capital and deployed reference values must be non-negative")

    total_limit = capital * policy.max_total_deployed_fraction
    remaining_reference = max(ZERO, total_limit - deployed)
    remaining_quote: dict[str, Decimal] = {}
    for asset, raw in available_quote.items():
        value = as_decimal(raw, f"available_quote[{asset}]")
        if value < 0:
            raise TradingValidationError("available quote balances must be non-negative")
        remaining_quote[str(asset)] = value

    remaining_base: dict[str, Decimal] = {}
    for symbol, raw in (available_base or {}).items():
        value = as_decimal(raw, f"available_base[{symbol}]")
        if value < 0:
            raise TradingValidationError("available base balances must be non-negative")
        remaining_base[str(symbol)] = value

    timestamp = time.time() if now is None else float(now)
    # Sells first: releasing inventory lowers deployment and funds new buys, so
    # a position that exits this cycle can be replaced in the same cycle.
    ordered = sorted(
        opportunities,
        key=lambda item: (0 if item.side == "sell" else 1, -item.score, item.opportunity_id),
    )
    decisions: list[AllocationDecision] = []
    skipped: list[SkipDecision] = []

    for opportunity in ordered:
        if opportunity.expires_at <= timestamp:
            skipped.append(_skip(opportunity, "expired"))
            continue
        market = markets.get(opportunity.symbol)
        if market is None:
            skipped.append(_skip(opportunity, "market_missing"))
            continue
        if not market.spot or not market.active:
            skipped.append(_skip(opportunity, "market_inactive"))
            continue
        if not market.market_order_enabled:
            skipped.append(_skip(opportunity, "market_order_disabled"))
            continue
        price = _positive(prices, opportunity.symbol)
        if price is None:
            skipped.append(_skip(opportunity, "price_missing"))
            continue
        quote_rate = _positive(quote_to_reference, market.quote)
        if quote_rate is None:
            skipped.append(_skip(opportunity, "quote_unvalued"))
            continue

        if opportunity.side == "sell":
            held = remaining_base.get(opportunity.symbol, ZERO)
            if held <= 0:
                skipped.append(_skip(opportunity, "no_inventory"))
                continue
            amount = _round_down(held, market.amount_step)
            if amount <= 0 or (market.min_amount is not None and amount < market.min_amount):
                skipped.append(_skip(opportunity, "below_min_amount"))
                continue
            quote_notional = amount * price
            if market.min_cost is not None and quote_notional < market.min_cost:
                skipped.append(_skip(opportunity, "below_min_cost"))
                continue
            reference_notional = quote_notional * quote_rate
            decisions.append(
                AllocationDecision(
                    opportunity_id=opportunity.opportunity_id,
                    strategy_id=opportunity.strategy_id,
                    symbol=opportunity.symbol,
                    side=opportunity.side,
                    quote=market.quote,
                    amount=amount,
                    price=price,
                    quote_notional=quote_notional,
                    reference_notional=reference_notional,
                    reason_code=opportunity.reason_code,
                )
            )
            remaining_base[opportunity.symbol] = held - amount
            # Sale proceeds restore funding cash, but same-cycle replacement buys
            # must never exceed the configured total deployment ceiling. A
            # profitable exit can be worth more than the capacity it originally
            # occupied, so cap the released reference budget at total_limit.
            remaining_reference = min(total_limit, remaining_reference + reference_notional)
            remaining_quote[market.quote] = (
                remaining_quote.get(market.quote, ZERO) + quote_notional
            )
            continue

        if opportunity.side != "buy":
            skipped.append(_skip(opportunity, "unsupported_side"))
            continue
        quote_balance = remaining_quote.get(market.quote, ZERO)
        if quote_balance <= 0:
            skipped.append(_skip(opportunity, "quote_unavailable"))
            continue
        if remaining_reference <= 0:
            skipped.append(_skip(opportunity, "total_cap_exhausted"))
            continue

        opportunity_fraction = min(
            policy.max_opportunity_fraction,
            opportunity.max_notional_fraction,
        )
        reference_cap = capital * opportunity_fraction
        hard_cap = capital * policy.max_opportunity_fraction
        funding_cap = quote_balance * quote_rate
        target_reference = min(reference_cap, remaining_reference, funding_cap)
        if target_reference <= 0:
            skipped.append(_skip(opportunity, "total_cap_exhausted"))
            continue

        # Exchange minimums are hard execution constraints. When the
        # fraction-sized order would fall below the minimum, size up to the
        # minimum (rounded up to the step), as long as it still fits the hard
        # per-opportunity cap and the remaining/funding limits. A minimum that
        # cannot fit is attributed to the binding limit so the dashboard shows
        # the real reason instead of always "below_min_amount".
        min_amount_required = _market_min_amount(market, price)
        min_reference_required = (
            ZERO if min_amount_required is None else min_amount_required * price * quote_rate
        )
        if min_reference_required > 0:
            if min_reference_required > hard_cap:
                skipped.append(_skip(opportunity, "below_min_amount"))
                continue
            if min_reference_required > remaining_reference:
                skipped.append(_skip(opportunity, "total_cap_exhausted"))
                continue
            if min_reference_required > funding_cap:
                skipped.append(_skip(opportunity, "quote_unavailable"))
                continue
        target_quote = target_reference / quote_rate
        raw_amount = target_quote / price
        amount = _round_down(raw_amount, market.amount_step)
        if min_amount_required is not None and amount < min_amount_required:
            amount = _round_up(min_amount_required, market.amount_step)
        if amount <= 0:
            skipped.append(_skip(opportunity, "below_min_amount"))
            continue
        if market.min_amount is not None and amount < market.min_amount:
            skipped.append(_skip(opportunity, "below_min_amount"))
            continue
        quote_notional = amount * price
        if market.min_cost is not None and quote_notional < market.min_cost:
            skipped.append(_skip(opportunity, "below_min_cost"))
            continue

        reference_notional = quote_notional * quote_rate
        if reference_notional <= 0 or reference_notional > remaining_reference:
            skipped.append(_skip(opportunity, "total_cap_exhausted"))
            continue
        decisions.append(
            AllocationDecision(
                opportunity_id=opportunity.opportunity_id,
                strategy_id=opportunity.strategy_id,
                symbol=opportunity.symbol,
                side=opportunity.side,
                quote=market.quote,
                amount=amount,
                price=price,
                quote_notional=quote_notional,
                reference_notional=reference_notional,
                reason_code=opportunity.reason_code,
            )
        )
        remaining_reference -= reference_notional
        remaining_quote[market.quote] = quote_balance - quote_notional

    return AllocationResult(decisions=tuple(decisions), skipped=tuple(skipped))

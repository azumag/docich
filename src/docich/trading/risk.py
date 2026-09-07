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


def allocate_opportunities(
    opportunities: Sequence[Opportunity],
    *,
    markets: Mapping[str, MarketInfo],
    prices: Mapping[str, Decimal],
    quote_to_reference: Mapping[str, Decimal],
    available_quote: Mapping[str, Decimal],
    capital_reference: Decimal,
    deployed_reference: Decimal,
    policy: CapitalPolicy | None = None,
    now: float | None = None,
) -> AllocationResult:
    """Allocate buy opportunities without exceeding capital or funding-asset limits.

    The allocator never invents FX/crypto conversion routes.  A non-reference
    quote asset must have both an explicit valuation rate and an available
    balance.  Sell opportunities are intentionally rejected in this first
    slice until inventory-aware exit handling is implemented.
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

    timestamp = time.time() if now is None else float(now)
    ordered = sorted(opportunities, key=lambda item: (-item.score, item.opportunity_id))
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
        if opportunity.side != "buy":
            skipped.append(_skip(opportunity, "unsupported_side"))
            continue
        price = _positive(prices, opportunity.symbol)
        if price is None:
            skipped.append(_skip(opportunity, "price_missing"))
            continue
        quote_rate = _positive(quote_to_reference, market.quote)
        if quote_rate is None:
            skipped.append(_skip(opportunity, "quote_unvalued"))
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
        funding_cap = quote_balance * quote_rate
        target_reference = min(reference_cap, remaining_reference, funding_cap)
        if target_reference <= 0:
            skipped.append(_skip(opportunity, "total_cap_exhausted"))
            continue

        target_quote = target_reference / quote_rate
        raw_amount = target_quote / price
        amount = _round_down(raw_amount, market.amount_step)
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

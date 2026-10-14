"""Depth-aware paper diagnostics for multi-leg crypto routes."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import math
from typing import Mapping, Sequence

from .arbitrage import ArbitrageLeg, ArbitrageRoute
from .models import TradingValidationError, as_decimal

D = Decimal
ZERO = D("0")


@dataclass(frozen=True)
class DepthLevel:
    price: Decimal
    amount: Decimal

    def __post_init__(self) -> None:
        price = as_decimal(self.price, "depth price")
        amount = as_decimal(self.amount, "depth amount")
        if price <= 0 or amount <= 0:
            raise TradingValidationError("depth price/amount must be positive")
        object.__setattr__(self, "price", price)
        object.__setattr__(self, "amount", amount)


@dataclass(frozen=True)
class DepthBook:
    symbol: str
    bids: tuple[DepthLevel, ...]
    asks: tuple[DepthLevel, ...]
    as_of: float

    def __post_init__(self) -> None:
        if not self.symbol or not self.bids or not self.asks:
            raise TradingValidationError("depth book must have symbol and both sides")
        if not math.isfinite(float(self.as_of)):
            raise TradingValidationError("depth book timestamp must be finite")
        if any(a.price < b.price for a, b in zip(self.bids, self.bids[1:])):
            raise TradingValidationError("depth bids must be best-first")
        if any(a.price > b.price for a, b in zip(self.asks, self.asks[1:])):
            raise TradingValidationError("depth asks must be best-first")


@dataclass(frozen=True)
class DepthLegSimulation:
    symbol: str
    from_asset: str
    to_asset: str
    side: str
    input_amount: Decimal
    filled_input: Decimal
    gross_output: Decimal
    traded_base_amount: Decimal
    fee_paid_base: Decimal
    fee_paid_quote: Decimal
    output_amount: Decimal
    levels_used: int
    complete: bool


@dataclass(frozen=True)
class DepthRouteSimulation:
    route_id: str
    start_asset: str
    start_amount: Decimal
    final_amount: Decimal | None
    net_edge_bps: Decimal | None
    complete: bool
    failed_leg_symbol: str | None
    legs: tuple[DepthLegSimulation, ...]


def _rotated_legs(route: ArbitrageRoute, start_asset: str) -> tuple[ArbitrageLeg, ...]:
    legs = route.legs
    for index, leg in enumerate(legs):
        if leg.from_asset == start_asset:
            return legs[index:] + legs[:index]
    raise TradingValidationError(f"start asset {start_asset} is not in arbitrage route")


def _simulate_leg(leg: ArbitrageLeg, book: DepthBook, input_amount: Decimal) -> DepthLegSimulation:
    requested = as_decimal(input_amount, "depth leg input")
    if requested <= 0:
        raise TradingValidationError("depth leg input must be positive")
    remaining = requested
    filled = ZERO
    gross = ZERO
    traded_base = ZERO
    fee_base_total = ZERO
    fee_quote_total = ZERO
    levels_used = 0
    quote_fee = as_decimal(leg.fee_rate_quote, "fee_rate_quote")
    base_fee = as_decimal(leg.fee_rate_base, "fee_rate_base")
    levels: Sequence[DepthLevel] = book.bids if leg.side == "sell" else book.asks
    for level in levels:
        if remaining <= 0:
            break
        if leg.side == "sell":
            input_factor = D("1") + base_fee
            level_input_capacity = level.amount * input_factor
            take_input = min(remaining, level_input_capacity)
            executed_base = take_input / input_factor
            level_gross = executed_base * level.price
            level_fee_base = executed_base * base_fee
            level_fee_quote = level_gross * quote_fee
            level_output = level_gross - level_fee_quote
        elif leg.side == "buy":
            input_factor = D("1") + quote_fee
            level_input_capacity = level.amount * level.price * input_factor
            take_input = min(remaining, level_input_capacity)
            executed_base = take_input / (level.price * input_factor)
            level_gross = executed_base
            level_fee_base = executed_base * base_fee
            level_fee_quote = executed_base * level.price * quote_fee
            level_output = level_gross - level_fee_base
        else:
            raise TradingValidationError(f"unsupported arbitrage side: {leg.side}")
        if take_input > 0:
            filled += take_input
            remaining -= take_input
            gross += level_gross
            traded_base += executed_base
            fee_base_total += level_fee_base
            fee_quote_total += level_fee_quote
            levels_used += 1
    output = (
        gross - fee_quote_total if leg.side == "sell"
        else gross - fee_base_total
    )
    return DepthLegSimulation(
        symbol=leg.symbol,
        from_asset=leg.from_asset,
        to_asset=leg.to_asset,
        side=leg.side,
        input_amount=requested,
        filled_input=filled,
        gross_output=gross,
        traded_base_amount=traded_base,
        fee_paid_base=fee_base_total,
        fee_paid_quote=fee_quote_total,
        output_amount=output,
        levels_used=levels_used,
        complete=remaining <= 0,
    )


def simulate_route_depth(
    route: ArbitrageRoute,
    books: Mapping[str, DepthBook],
    *,
    start_amount: Decimal,
    start_asset: str | None = None,
) -> DepthRouteSimulation:
    asset = str(start_asset or route.start_asset)
    amount = as_decimal(start_amount, "start_amount")
    if amount <= 0:
        raise TradingValidationError("start_amount must be positive")
    legs = _rotated_legs(route, asset)
    executions: list[DepthLegSimulation] = []
    current = amount
    for leg in legs:
        book = books.get(leg.symbol)
        if book is None:
            return DepthRouteSimulation(route.route_id, asset, amount, None, None, False, leg.symbol, tuple(executions))
        execution = _simulate_leg(leg, book, current)
        executions.append(execution)
        if not execution.complete:
            return DepthRouteSimulation(route.route_id, asset, amount, None, None, False, leg.symbol, tuple(executions))
        current = execution.output_amount
    edge = (current / amount - D("1")) * D("10000")
    return DepthRouteSimulation(route.route_id, asset, amount, current, edge, True, None, tuple(executions))

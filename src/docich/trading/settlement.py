"""Constraint-aware paper settlement for multi-leg crypto routes."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import math
from typing import Mapping

from .arbitrage import ArbitrageLeg, ArbitrageRoute
from .depth import DepthBook
from .models import MarketInfo, TradingValidationError, as_decimal

D = Decimal
ZERO = D("0")
SETTLEMENT_MODEL_VERSION = "multileg-v1"
_ALLOWED_CIRCUIT_MODES = {"NONE", "CIRCUIT_BREAK", "FULL_RANGE_CIRCUIT_BREAK", "RESUMPTION", "LISTING"}


@dataclass(frozen=True)
class CircuitBreakStatus:
    symbol: str
    mode: str
    fee_type: str | None
    as_of: float
    fetched_at: float | None = None

    def __post_init__(self) -> None:
        symbol = str(self.symbol).strip()
        mode = str(self.mode).strip().upper()
        if not symbol or mode not in _ALLOWED_CIRCUIT_MODES:
            raise TradingValidationError("circuit status symbol/mode is invalid")
        if not math.isfinite(float(self.as_of)):
            raise TradingValidationError("circuit status timestamp must be finite")
        fetched_at = float(self.as_of) if self.fetched_at is None else float(self.fetched_at)
        if not math.isfinite(fetched_at):
            raise TradingValidationError("circuit status fetched_at must be finite")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "fetched_at", fetched_at)


@dataclass(frozen=True)
class SettlementLeg:
    symbol: str
    side: str
    input_amount: Decimal
    order_base_amount: Decimal
    consumed_input: Decimal
    residual_input: Decimal
    output_amount: Decimal
    levels_used: int
    fee_paid_base: Decimal
    fee_paid_quote: Decimal


@dataclass(frozen=True)
class MultiLegSettlement:
    route_id: str
    start_asset: str
    start_amount: Decimal
    final_amount: Decimal | None
    net_edge_bps: Decimal | None
    complete: bool
    failed_leg_symbol: str | None
    failure_reason: str | None
    residuals: Mapping[str, Decimal]
    legs: tuple[SettlementLeg, ...]


def settlement_observation_id(
    route: ArbitrageRoute,
    settlement: MultiLegSettlement,
    depth_books: Mapping[str, DepthBook],
    circuit_statuses: Mapping[str, CircuitBreakStatus],
    markets: Mapping[str, MarketInfo],
) -> str:
    """Return deterministic identity for one constrained paper observation."""
    symbols = sorted({leg.symbol for leg in route.legs})
    payload = {
        "model_version": SETTLEMENT_MODEL_VERSION,
        "route_id": route.route_id,
        "start_asset": settlement.start_asset,
        "start_amount": str(settlement.start_amount),
        "books": {
            symbol: {
                "as_of": depth_books[symbol].as_of,
                "bids": [[str(level.price), str(level.amount)] for level in depth_books[symbol].bids],
                "asks": [[str(level.price), str(level.amount)] for level in depth_books[symbol].asks],
            }
            for symbol in symbols if symbol in depth_books
        },
        "circuit": {
            symbol: {
                "mode": circuit_statuses[symbol].mode,
                "fee_type": circuit_statuses[symbol].fee_type,
                "as_of": circuit_statuses[symbol].as_of,
            }
            for symbol in symbols if symbol in circuit_statuses
        },
        "markets": {
            symbol: {
                "base": markets[symbol].base,
                "quote": markets[symbol].quote,
                "amount_step": None if markets[symbol].amount_step is None else str(markets[symbol].amount_step),
                "min_amount": None if markets[symbol].min_amount is None else str(markets[symbol].min_amount),
                "min_cost": None if markets[symbol].min_cost is None else str(markets[symbol].min_cost),
                "fee_base": None if markets[symbol].taker_fee_rate_base is None else str(markets[symbol].taker_fee_rate_base),
                "fee_quote": None if markets[symbol].taker_fee_rate_quote is None else str(markets[symbol].taker_fee_rate_quote),
                "market_order_enabled": bool(markets[symbol].market_order_enabled),
            }
            for symbol in symbols if symbol in markets
        },
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return SETTLEMENT_MODEL_VERSION + ":" + hashlib.sha256(raw).hexdigest()[:32]


def _rotated_legs(route: ArbitrageRoute, start_asset: str) -> tuple[ArbitrageLeg, ...]:
    for index, leg in enumerate(route.legs):
        if leg.from_asset == start_asset:
            return route.legs[index:] + route.legs[:index]
    raise TradingValidationError(f"start asset {start_asset} is not in arbitrage route")


def _round_down(value: Decimal, step: Decimal | None) -> Decimal:
    if step is None:
        return value
    return (value // step) * step


def _max_affordable_base(leg: ArbitrageLeg, book: DepthBook, input_amount: Decimal) -> Decimal:
    base_fee = as_decimal(leg.fee_rate_base, "fee_rate_base")
    quote_fee = as_decimal(leg.fee_rate_quote, "fee_rate_quote")
    if leg.side == "sell":
        available_for_order = input_amount / (D("1") + base_fee)
        return min(available_for_order, sum(level.amount for level in book.bids))
    if leg.side != "buy":
        raise TradingValidationError(f"unsupported settlement side: {leg.side}")
    remaining_quote = input_amount
    base_total = ZERO
    for level in book.asks:
        quote_per_base = level.price * (D("1") + quote_fee)
        take = min(level.amount, remaining_quote / quote_per_base)
        if take <= 0:
            break
        base_total += take
        remaining_quote -= take * quote_per_base
    return base_total


def _execute_base_order(leg: ArbitrageLeg, book: DepthBook, base_amount: Decimal) -> tuple[Decimal, Decimal, Decimal, Decimal, int, bool]:
    remaining = base_amount
    gross_quote = ZERO
    levels_used = 0
    levels = book.asks if leg.side == "buy" else book.bids
    for level in levels:
        if remaining <= 0:
            break
        take = min(remaining, level.amount)
        if take > 0:
            remaining -= take
            gross_quote += take * level.price
            levels_used += 1
    if remaining > 0:
        return ZERO, ZERO, ZERO, ZERO, levels_used, False
    base_fee = base_amount * as_decimal(leg.fee_rate_base, "fee_rate_base")
    quote_fee = gross_quote * as_decimal(leg.fee_rate_quote, "fee_rate_quote")
    if leg.side == "buy":
        consumed = gross_quote + quote_fee
        output = base_amount - base_fee
    else:
        consumed = base_amount + base_fee
        output = gross_quote - quote_fee
    return consumed, output, base_fee, quote_fee, levels_used, True


def _failure(route: ArbitrageRoute, start_asset: str, start_amount: Decimal, symbol: str, reason: str, residuals: dict[str, Decimal], legs: list[SettlementLeg]) -> MultiLegSettlement:
    return MultiLegSettlement(route.route_id, start_asset, start_amount, None, None, False, symbol, reason, dict(residuals), tuple(legs))


def simulate_multileg_settlement(
    route: ArbitrageRoute,
    books: Mapping[str, DepthBook],
    markets: Mapping[str, MarketInfo],
    circuit_statuses: Mapping[str, CircuitBreakStatus],
    *,
    start_amount: Decimal,
    start_asset: str | None = None,
    now: float,
    max_status_age_seconds: float = 5.0,
    max_book_age_seconds: float = 2.0,
) -> MultiLegSettlement:
    asset = str(start_asset or route.start_asset)
    amount = as_decimal(start_amount, "start_amount")
    if amount <= 0:
        raise TradingValidationError("start_amount must be positive")
    legs = _rotated_legs(route, asset)
    # Gate the entire route before the first synthetic execution.
    for leg in legs:
        market = markets.get(leg.symbol)
        if market is None or not market.active or not market.spot:
            return _failure(route, asset, amount, leg.symbol, "market_unavailable", {}, [])
        if market.amount_step is None or market.min_amount is None:
            return _failure(route, asset, amount, leg.symbol, "market_constraints_missing", {}, [])
        if not market.market_order_enabled:
            return _failure(route, asset, amount, leg.symbol, "market_order_disabled", {}, [])
        status = circuit_statuses.get(leg.symbol)
        if status is None:
            return _failure(route, asset, amount, leg.symbol, "circuit_status_missing", {}, [])
        age = float(now) - float(status.fetched_at)
        if age < -1 or age > max_status_age_seconds:
            return _failure(route, asset, amount, leg.symbol, "circuit_status_stale", {}, [])
        if status.mode != "NONE":
            return _failure(route, asset, amount, leg.symbol, f"circuit_break:{status.mode}", {}, [])
        if status.fee_type != "NORMAL":
            return _failure(route, asset, amount, leg.symbol, f"fee_type:{status.fee_type or 'MISSING'}", {}, [])
        book = books.get(leg.symbol)
        if book is None:
            return _failure(route, asset, amount, leg.symbol, "depth_missing", {}, [])
        book_age = float(now) - float(book.as_of)
        if book_age < -1 or book_age > max_book_age_seconds:
            return _failure(route, asset, amount, leg.symbol, "depth_stale", {}, [])

    current = amount
    residuals: dict[str, Decimal] = {}
    executions: list[SettlementLeg] = []
    for leg in legs:
        market = markets[leg.symbol]
        book = books[leg.symbol]
        raw_base = _max_affordable_base(leg, book, current)
        order_base = _round_down(raw_base, market.amount_step)
        if order_base <= 0 or (market.min_amount is not None and order_base < market.min_amount):
            return _failure(route, asset, amount, leg.symbol, "below_min_amount", residuals, executions)
        consumed, output, fee_base, fee_quote, used, complete = _execute_base_order(leg, book, order_base)
        if not complete:
            return _failure(route, asset, amount, leg.symbol, "insufficient_depth", residuals, executions)
        if market.min_cost is not None:
            gross_quote = consumed - fee_quote if leg.side == "buy" else output + fee_quote
            if gross_quote < market.min_cost:
                return _failure(route, asset, amount, leg.symbol, "below_min_cost", residuals, executions)
        residual = current - consumed
        if residual < 0:
            return _failure(route, asset, amount, leg.symbol, "insufficient_input", residuals, executions)
        if residual > 0:
            residuals[leg.from_asset] = residuals.get(leg.from_asset, ZERO) + residual
        executions.append(SettlementLeg(leg.symbol, leg.side, current, order_base, consumed, residual, output, used, fee_base, fee_quote))
        current = output

    final = current + residuals.get(asset, ZERO)
    edge = (final / amount - D("1")) * D("10000")
    return MultiLegSettlement(route.route_id, asset, amount, final, edge, True, None, None, dict(residuals), tuple(executions))

"""Cross-sectional relative-value opportunity generation for paper trading."""
from __future__ import annotations

from decimal import Decimal
from statistics import median
from typing import Mapping

from .market_data import MarketFrame
from .models import MarketInfo, Opportunity

D = Decimal

def scan_relative_value_opportunities(
    frames: Mapping[str, MarketFrame],
    markets: Mapping[str, MarketInfo],
    *,
    now: float,
    lookback: int = 6,
    min_group_size: int = 3,
    lag_threshold_bps: Decimal = D("300"),
    max_notional_fraction: Decimal = D("0.15"),
) -> tuple[Opportunity, ...]:
    groups: dict[str, list[str]] = {}
    for symbol, market in markets.items():
        if market.active and market.spot and symbol in frames:
            groups.setdefault(market.quote, []).append(symbol)

    opportunities: list[Opportunity] = []
    for symbols in groups.values():
        eligible = [symbol for symbol in symbols if len(frames[symbol].closes) >= lookback + 1]
        if len(eligible) < min_group_size:
            continue
        returns: dict[str, Decimal] = {}
        for symbol in eligible:
            closes = frames[symbol].closes
            start = closes[-(lookback + 1)]
            returns[symbol] = (closes[-1] / start - D("1")) * D("10000")
        benchmark = D(str(median([float(value) for value in returns.values()])))
        for symbol in sorted(eligible):
            frame = frames[symbol]
            residual = returns[symbol] - benchmark
            if residual > -lag_threshold_bps:
                continue
            if frame.closes[-1] <= frame.closes[-2]:
                continue
            edge = -residual
            opportunities.append(Opportunity(
                opportunity_id=f"relative-value-v1:{symbol}:{int(frame.as_of)}",
                strategy_id="relative-value-v1",
                symbol=symbol,
                side="buy",
                score=min(D("1"), edge / D("1000")),
                expected_edge_bps=edge / D("2"),
                max_notional_fraction=max_notional_fraction,
                expires_at=float(now) + frame.timeframe_seconds * 2,
                reason_code="relative_value_lag",
            ))
    return tuple(sorted(opportunities, key=lambda item: (item.symbol, item.opportunity_id)))

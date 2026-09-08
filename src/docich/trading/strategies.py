"""Deterministic paper-only signal generation and diversification."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import math
from statistics import fmean, pstdev
from typing import Mapping, Sequence

from .market_data import MarketFrame
from .models import Opportunity, SkipDecision, TradingValidationError, as_decimal


D = Decimal


@dataclass(frozen=True)
class StrategyPolicy:
    momentum_lookback: int = 6
    momentum_threshold_bps: Decimal = D("300")
    mean_reversion_lookback: int = 10
    mean_reversion_z: Decimal = D("-1.5")
    max_notional_fraction: Decimal = D("0.15")

    def __post_init__(self) -> None:
        if self.momentum_lookback < 2 or self.mean_reversion_lookback < 3:
            raise TradingValidationError("strategy lookbacks are too short")
        for name in ("momentum_threshold_bps", "max_notional_fraction", "mean_reversion_z"):
            object.__setattr__(self, name, as_decimal(getattr(self, name), name))
        if self.momentum_threshold_bps <= 0:
            raise TradingValidationError("momentum_threshold_bps must be positive")
        if self.mean_reversion_z >= 0:
            raise TradingValidationError("mean_reversion_z must be negative")
        if self.max_notional_fraction <= 0 or self.max_notional_fraction > 1:
            raise TradingValidationError("max_notional_fraction must be in (0, 1]")


@dataclass(frozen=True)
class StrategySelectionResult:
    selected: tuple[Opportunity, ...]
    rejected: tuple[SkipDecision, ...]


def _opportunity_id(strategy_id: str, symbol: str, as_of: float) -> str:
    return f"{strategy_id}:{symbol}:{int(as_of)}"


def _score(value: float) -> Decimal:
    return D(str(min(1.0, max(0.0, value))))


def scan_opportunities(
    frames: Mapping[str, MarketFrame],
    *,
    now: float,
    policy: StrategyPolicy | None = None,
) -> tuple[Opportunity, ...]:
    policy = policy or StrategyPolicy()
    opportunities: list[Opportunity] = []
    for symbol in sorted(frames):
        frame = frames[symbol]
        closes = frame.closes
        expires_at = float(now) + frame.timeframe_seconds * 2

        if len(closes) >= policy.momentum_lookback + 1:
            start = closes[-(policy.momentum_lookback + 1)]
            momentum_bps = (closes[-1] / start - D("1")) * D("10000")
            if momentum_bps >= policy.momentum_threshold_bps:
                strategy_id = "momentum-v1"
                opportunities.append(
                    Opportunity(
                        opportunity_id=_opportunity_id(strategy_id, symbol, frame.as_of),
                        strategy_id=strategy_id,
                        symbol=symbol,
                        side="buy",
                        score=_score(float(momentum_bps / D("1000"))),
                        expected_edge_bps=momentum_bps / D("2"),
                        max_notional_fraction=policy.max_notional_fraction,
                        expires_at=expires_at,
                        reason_code="momentum_breakout",
                    )
                )

        if len(closes) >= policy.mean_reversion_lookback:
            window = [float(value) for value in closes[-policy.mean_reversion_lookback:]]
            mean = fmean(window)
            stddev = pstdev(window)
            if stddev > 0:
                zscore = (window[-1] - mean) / stddev
                if zscore <= float(policy.mean_reversion_z):
                    strategy_id = "mean-reversion-v1"
                    edge_bps = D(str((mean / window[-1] - 1.0) * 10000.0))
                    opportunities.append(
                        Opportunity(
                            opportunity_id=_opportunity_id(strategy_id, symbol, frame.as_of),
                            strategy_id=strategy_id,
                            symbol=symbol,
                            side="buy",
                            score=_score(abs(zscore) / 3.0),
                            expected_edge_bps=edge_bps,
                            max_notional_fraction=policy.max_notional_fraction,
                            expires_at=expires_at,
                            reason_code="mean_reversion_discount",
                        )
                    )
    return tuple(sorted(opportunities, key=lambda item: (item.strategy_id, item.symbol, item.opportunity_id)))


def _returns(frame: MarketFrame) -> list[float]:
    return [
        float(current / previous - D("1"))
        for previous, current in zip(frame.closes, frame.closes[1:])
        if previous > 0
    ]


def _pearson(left: MarketFrame, right: MarketFrame) -> float | None:
    a = _returns(left)
    b = _returns(right)
    count = min(len(a), len(b))
    if count < 3:
        return None
    a, b = a[-count:], b[-count:]
    mean_a, mean_b = fmean(a), fmean(b)
    da = [x - mean_a for x in a]
    db = [x - mean_b for x in b]
    denom = math.sqrt(sum(x*x for x in da) * sum(y*y for y in db))
    if denom == 0:
        return None
    return sum(x*y for x, y in zip(da, db)) / denom


def _skip(opportunity: Opportunity, reason: str) -> SkipDecision:
    return SkipDecision(opportunity.opportunity_id, opportunity.symbol, reason)


def select_diversified_opportunities(
    opportunities: Sequence[Opportunity],
    frames: Mapping[str, MarketFrame],
    *,
    max_pair_correlation: Decimal = D("0.85"),
) -> StrategySelectionResult:
    threshold = as_decimal(max_pair_correlation, "max_pair_correlation")
    if threshold < 0 or threshold > 1:
        raise TradingValidationError("max_pair_correlation must be between 0 and 1")

    selected: list[Opportunity] = []
    rejected: list[SkipDecision] = []
    seen_symbols: set[str] = set()
    ordered = sorted(opportunities, key=lambda item: (-item.score, item.opportunity_id))
    for opportunity in ordered:
        if opportunity.symbol in seen_symbols:
            rejected.append(_skip(opportunity, "duplicate_symbol"))
            continue
        frame = frames.get(opportunity.symbol)
        if frame is None:
            rejected.append(_skip(opportunity, "correlation_unknown"))
            continue
        correlated = False
        unknown = False
        for existing in selected:
            other = frames.get(existing.symbol)
            if other is None:
                unknown = True
                break
            correlation = _pearson(frame, other)
            if correlation is None:
                unknown = True
                break
            if correlation >= float(threshold):
                correlated = True
                break
        if correlated:
            rejected.append(_skip(opportunity, "correlated_exposure"))
            continue
        if unknown:
            rejected.append(_skip(opportunity, "correlation_unknown"))
            continue
        selected.append(opportunity)
        seen_symbols.add(opportunity.symbol)
    return StrategySelectionResult(tuple(selected), tuple(rejected))

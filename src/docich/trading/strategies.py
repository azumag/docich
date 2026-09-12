"""Deterministic paper-only signal generation and diversification."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import math
from statistics import fmean, pstdev
from typing import Mapping, Sequence

from .market_data import MarketFrame
from .models import Opportunity, SkipDecision, TradingValidationError, as_decimal
from .strategy_lab import (
    built_in_reason_context,
    scan_experiment_entries,
    scan_experiment_exits,
)
from .strategy_runtime import (
    get_active_experiment,
    register_reason_context,
    register_reason_contexts,
)


D = Decimal
MOMENTUM_MIN_THRESHOLD_BPS = D("100")
MOMENTUM_VOLATILITY_MULTIPLIER = 2.0
MOMENTUM_VOLATILITY_BARS = 12


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


def _adaptive_momentum_threshold_bps(
    closes: Sequence[Decimal], policy: StrategyPolicy
) -> Decimal:
    """Scale the built-in momentum gate to recent realized volatility.

    ``momentum_threshold_bps`` remains the reviewed/tunable ceiling. Low-volatility
    markets can use a lower gate, but never below 100 bps unless the persisted
    policy itself is already stricter/lower. This avoids permanently starving BTC
    while keeping volatile altcoins near the existing 300 bps default.
    """
    ceiling = policy.momentum_threshold_bps
    floor = min(ceiling, MOMENTUM_MIN_THRESHOLD_BPS)
    returns_bps = [
        float((current / previous - D("1")) * D("10000"))
        for previous, current in zip(closes, closes[1:])
        if previous > 0
    ]
    sample = returns_bps[-MOMENTUM_VOLATILITY_BARS:]
    if len(sample) < 3:
        return ceiling
    sigma = pstdev(sample)
    if not math.isfinite(sigma) or sigma <= 0:
        return floor
    horizon_sigma = sigma * math.sqrt(float(policy.momentum_lookback))
    adaptive = D(str(horizon_sigma * MOMENTUM_VOLATILITY_MULTIPLIER))
    return min(ceiling, max(floor, adaptive))


def scan_opportunities(
    frames: Mapping[str, MarketFrame],
    *,
    now: float,
    policy: StrategyPolicy | None = None,
) -> tuple[Opportunity, ...]:
    policy = policy or StrategyPolicy()
    experiment = get_active_experiment()
    if experiment is not None:
        result = scan_experiment_entries(frames, experiment, now=now)
        register_reason_contexts(result.reason_contexts)
        return result.opportunities

    opportunities: list[Opportunity] = []
    for symbol in sorted(frames):
        frame = frames[symbol]
        closes = frame.closes
        expires_at = float(now) + frame.timeframe_seconds * 2

        if len(closes) >= policy.momentum_lookback + 1:
            start = closes[-(policy.momentum_lookback + 1)]
            momentum_bps = (closes[-1] / start - D("1")) * D("10000")
            momentum_threshold = _adaptive_momentum_threshold_bps(closes, policy)
            if momentum_bps >= momentum_threshold:
                strategy_id = "momentum-v1"
                opportunities.append(
                    Opportunity(
                        opportunity_id=_opportunity_id(strategy_id, symbol, frame.as_of),
                        strategy_id=strategy_id,
                        symbol=symbol,
                        side="buy",
                        score=_score(float(momentum_bps / (momentum_threshold * D("2")))),
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
    for opportunity in opportunities:
        momentum_threshold = policy.momentum_threshold_bps
        if opportunity.reason_code == "momentum_breakout":
            frame = frames.get(opportunity.symbol)
            if frame is not None:
                momentum_threshold = _adaptive_momentum_threshold_bps(frame.closes, policy)
        context = built_in_reason_context(
            opportunity,
            frames,
            {},
            now=now,
            momentum_lookback=policy.momentum_lookback,
            momentum_threshold_bps=momentum_threshold,
            mean_reversion_lookback=policy.mean_reversion_lookback,
            mean_reversion_z=policy.mean_reversion_z,
            take_profit=EXIT_TAKE_PROFIT,
            stop_loss=EXIT_STOP_LOSS,
            max_hold_s=EXIT_MAX_HOLD_S,
        )
        register_reason_context(opportunity.opportunity_id, context)
    return tuple(sorted(opportunities, key=lambda item: (item.strategy_id, item.symbol, item.opportunity_id)))


EXIT_TAKE_PROFIT = D("0.01")
EXIT_STOP_LOSS = D("0.03")
EXIT_MAX_HOLD_S = 6 * 3600


def scan_exit_opportunities(
    frames: Mapping[str, MarketFrame],
    cost_basis: Mapping[str, object],
    *,
    now: float,
    take_profit: Decimal = EXIT_TAKE_PROFIT,
    stop_loss: Decimal = EXIT_STOP_LOSS,
    max_hold_s: float = EXIT_MAX_HOLD_S,
) -> tuple[Opportunity, ...]:
    """SELL opportunities that release inventory (take-profit/stop-loss/max hold)."""
    experiment = get_active_experiment()
    if experiment is not None:
        result = scan_experiment_exits(frames, cost_basis, experiment, now=now)
        register_reason_contexts(result.reason_contexts)
        return result.opportunities

    take_profit = as_decimal(take_profit, "take_profit")
    stop_loss = as_decimal(stop_loss, "stop_loss")
    opportunities: list[Opportunity] = []
    for symbol in sorted(cost_basis):
        entry = cost_basis[symbol]
        if not isinstance(entry, (tuple, list)) or len(entry) < 3:
            continue
        average = as_decimal(entry[1], "average_price")
        if average <= 0:
            continue
        frame = frames.get(str(symbol))
        if frame is None:
            continue
        price = frame.last_price
        change = price / average - D("1")
        reason: str | None = None
        if change >= take_profit:
            reason = "take_profit"
        elif change <= -stop_loss:
            reason = "stop_loss"
        elif float(now) - float(entry[2]) >= float(max_hold_s):
            reason = "max_hold"
        if reason is None:
            continue
        strategy_id = "exit-v1"
        opportunity = Opportunity(
            opportunity_id=_opportunity_id(strategy_id, str(symbol), frame.as_of),
            strategy_id=strategy_id,
            symbol=str(symbol),
            side="sell",
            score=_score(min(1.0, abs(float(change)) / 0.05 + 0.1)),
            expected_edge_bps=(change * D("10000")).copy_abs(),
            max_notional_fraction=D("1"),
            expires_at=float(now) + frame.timeframe_seconds * 2,
            reason_code=reason,
        )
        opportunities.append(opportunity)
        context = built_in_reason_context(
            opportunity,
            frames,
            cost_basis,
            now=now,
            momentum_lookback=StrategyPolicy().momentum_lookback,
            momentum_threshold_bps=StrategyPolicy().momentum_threshold_bps,
            mean_reversion_lookback=StrategyPolicy().mean_reversion_lookback,
            mean_reversion_z=StrategyPolicy().mean_reversion_z,
            take_profit=take_profit,
            stop_loss=stop_loss,
            max_hold_s=max_hold_s,
        )
        register_reason_context(opportunity.opportunity_id, context)
    return tuple(sorted(opportunities, key=lambda item: (item.symbol, item.opportunity_id)))


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
    return SkipDecision(
        opportunity.opportunity_id,
        opportunity.symbol,
        reason,
        side=opportunity.side,
    )


def select_diversified_opportunities(
    opportunities: Sequence[Opportunity],
    frames: Mapping[str, MarketFrame],
    *,
    max_pair_correlation: Decimal | None = None,
) -> StrategySelectionResult:
    experiment = get_active_experiment()
    selected_threshold = (
        experiment.max_pair_correlation
        if max_pair_correlation is None and experiment is not None
        else D("0.85") if max_pair_correlation is None
        else max_pair_correlation
    )
    threshold = as_decimal(selected_threshold, "max_pair_correlation")
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
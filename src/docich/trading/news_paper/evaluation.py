"""News PAPER comparative evaluation.

Compares price-only vs news+price strategies under identical conditions.
Exports read-only evidence with live_eligible=False.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
from typing import Any, Optional

D = Decimal


def decimal(value) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("boolean is not money")
    result = D(str(value))
    if not result.is_finite():
        raise ValueError("non-finite number")
    return result


@dataclass(frozen=True)
class EvaluationManifest:
    """Immutable manifest binding all evaluation inputs and parameters."""
    # Versioned contracts from N01/N04/N05/N06
    manifest_version: str = "1"
    ledger_revision: str = ""
    evidence_digest: str = ""
    classification_version: str = ""
    price_version: str = ""
    risk_version: str = ""
    cost_version: str = ""
    execution_version: str = ""
    policy_version: str = ""
    model_version: str = ""
    # Evaluation window
    eval_start_ts: float = 0.0
    eval_end_ts: float = 0.0
    warmup_days: int = 0
    # Comparison arms
    arm_a_label: str = "price_only"
    arm_b_label: str = "news_plus_price"
    # Cost/risk/fill parameters (must match for both arms)
    limits_digest: str = ""
    policy_digest: str = ""
    # Holdout / significance
    holdout_start_ts: float = 0.0
    min_independent_events: int = 0
    min_coverage_fraction: float = 0.8
    # Cost accounting
    llm_cost_per_call_usd: float = 0.0
    api_cost_per_call_usd: float = 0.0
    cost_allocation_method: str = "pro_rata"  # or "fixed"
    # Random seed for reproducibility
    rng_seed: int = 0

    def digest(self) -> str:
        """SHA-256 of manifest for reproducibility verification."""
        data = asdict(self)
        return hashlib.sha256(
            json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "EvaluationManifest":
        return cls(**data)

    def validate(self) -> list[str]:
        """Validate manifest constraints. Returns list of violations."""
        violations = []
        if self.eval_end_ts <= self.eval_start_ts:
            violations.append("eval_end_ts must be > eval_start_ts")
        if self.min_independent_events < 0:
            violations.append("min_independent_events must be >= 0")
        if not 0 <= self.min_coverage_fraction <= 1:
            violations.append("min_coverage_fraction must be in [0, 1]")
        if self.llm_cost_per_call_usd < 0 or self.api_cost_per_call_usd < 0:
            violations.append("cost_per_call_usd must be >= 0")
        # Required version fields must be non-empty for primary evaluation
        required_versions = [
            ("ledger_revision", self.ledger_revision),
            ("evidence_digest", self.evidence_digest),
            ("classification_version", self.classification_version),
            ("price_version", self.price_version),
            ("risk_version", self.risk_version),
            ("cost_version", self.cost_version),
            ("execution_version", self.execution_version),
            ("policy_version", self.policy_version),
            ("model_version", self.model_version),
        ]
        for name, val in required_versions:
            if not val:
                violations.append(f"required version field missing: {name}")
        return violations


@dataclass(frozen=True)
class FillRecord:
    """Normalized fill record for evaluation."""
    kind: str  # "open" or "close"
    symbol: str
    side: int  # 1 long, -1 short
    qty: Decimal
    price: Decimal
    fee: Decimal
    financing: Decimal = D("0")
    net_pnl_jpy: Decimal = D("0")
    ts: float = 0.0
    reason: str = ""
    policy_version: str = ""
    order_ref: str = ""
    # For partial fills
    partial: bool = False
    original_qty: Optional[Decimal] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["qty"] = str(self.qty)
        d["price"] = str(self.price)
        d["fee"] = str(self.fee)
        d["financing"] = str(self.financing)
        d["net_pnl_jpy"] = str(self.net_pnl_jpy)
        if self.original_qty is not None:
            d["original_qty"] = str(self.original_qty)
        return d


@dataclass(frozen=True)
class PositionEpisode:
    """Completed position episode (open + full close)."""
    symbol: str
    side: int
    open_ts: float
    close_ts: float
    open_price: Decimal
    close_price: Decimal
    qty: Decimal
    open_fee: Decimal
    close_fee: Decimal
    financing: Decimal
    net_pnl_jpy: Decimal
    hold_seconds: float
    win: bool

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("open_price", "close_price", "qty", "open_fee", "close_fee",
                  "financing", "net_pnl_jpy"):
            d[k] = str(d[k])
        return d


@dataclass(frozen=True)
class LatencyBreakdown:
    """Latency breakdown for news-to-fill pipeline."""
    published_to_fetched_ms: Optional[float] = None
    fetched_to_available_ms: Optional[float] = None
    available_to_classification_ms: Optional[float] = None
    classification_to_decision_ms: Optional[float] = None
    decision_to_fill_ms: Optional[float] = None
    # Event-level total latency (published_at to filled_at)
    total_ms_median: Optional[float] = None
    total_ms_p95: Optional[float] = None
    # Stage-level percentiles
    stage_median_ms: dict[str, Optional[float]] = field(default_factory=dict)
    stage_p95_ms: dict[str, Optional[float]] = field(default_factory=dict)
    missing_stages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class NewsUtilization:
    """News utilization metrics."""
    unique_eligible_events: int = 0
    events_used_for_signal: int = 0
    events_used_for_decision: int = 0
    raw_events: int = 0
    invalid_events: int = 0
    duplicate_events: int = 0
    held_rate: float = 0.0
    rejected_rate: float = 0.0
    observation_evidence_missing: bool = False
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    observation_missing_seconds: float = 0.0
    unknown_match: int = 0
    human_match: int = 0
    partial_fills: int = 0
    canceled_fills: int = 0
    max_exposure_jpy: Decimal = D("0")
    cost_allocation_method: str = "pro_rata"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["max_exposure_jpy"] = str(self.max_exposure_jpy)
        return d


@dataclass(frozen=True)
class CostBreakdown:
    """Cost accounting breakdown."""
    commission_jpy: Decimal = D("0")
    slippage_jpy: Decimal = D("0")
    financing_jpy: Decimal = D("0")
    llm_api_cost_jpy: Decimal = D("0")
    data_api_cost_jpy: Decimal = D("0")
    total_trading_cost_jpy: Decimal = D("0")
    total_operating_cost_jpy: Decimal = D("0")
    # Exit cost parameters (explicit rates, not total JPY)
    exit_commission_bps: Decimal = D("0")
    exit_slippage_bps: Decimal = D("0")
    cost_allocation_method: str = "pro_rata"

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("commission_jpy", "slippage_jpy", "financing_jpy",
                  "llm_api_cost_jpy", "data_api_cost_jpy",
                  "total_trading_cost_jpy", "total_operating_cost_jpy",
                  "exit_commission_bps", "exit_slippage_bps"):
            d[k] = str(d[k])
        return d


@dataclass(frozen=True)
class PolicyTracking:
    """Policy development tracking."""
    policies_tested: int = 0
    train_events: int = 0
    val_events: int = 0
    holdout_events: int = 0
    horizon_crossing_events: int = 0
    independent_events: int = 0
    synthetic_historical_results: bool = False
    forward_saved_classification: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ArmMetrics:
    """Metrics for one evaluation arm (A or B)."""
    label: str
    # Equity & PnL
    equity_jpy: Decimal = D("0")
    realized_pnl_jpy: Decimal = D("0")
    unrealized_pnl_jpy: Optional[Decimal] = None
    estimated_exit_cost_jpy: Optional[Decimal] = None
    # Risk
    max_drawdown_fraction: Decimal = D("0")
    max_drawdown_peak_ts: float = 0.0
    max_drawdown_trough_ts: float = 0.0
    # Win rate
    completed_episodes: int = 0
    wins: int = 0
    win_rate: float = 0.0
    open_positions: int = 0
    # Turnover
    turnover: Optional[float] = None
    avg_equity_jpy: Decimal = D("0")
    # Costs
    cost_breakdown: CostBreakdown = field(default_factory=CostBreakdown)
    # Latency
    latency: LatencyBreakdown = field(default_factory=LatencyBreakdown)
    # News utilization
    news_util: NewsUtilization = field(default_factory=NewsUtilization)
    # Policy tracking
    policy_tracking: PolicyTracking = field(default_factory=PolicyTracking)
    # Missing/partial handling
    missing_fills: int = 0
    partial_fills: int = 0
    unresolved_positions: int = 0
    data_gaps_seconds: float = 0.0
    # Reproducibility
    evaluation_digest: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["equity_jpy"] = str(self.equity_jpy)
        d["realized_pnl_jpy"] = str(self.realized_pnl_jpy)
        d["unrealized_pnl_jpy"] = str(self.unrealized_pnl_jpy) if self.unrealized_pnl_jpy is not None else None
        d["estimated_exit_cost_jpy"] = str(self.estimated_exit_cost_jpy) if self.estimated_exit_cost_jpy is not None else None
        d["max_drawdown_fraction"] = str(self.max_drawdown_fraction)
        d["avg_equity_jpy"] = str(self.avg_equity_jpy)
        # Convert nested objects
        d["cost_breakdown"] = self.cost_breakdown.to_dict()
        d["latency"] = self.latency.to_dict()
        d["news_util"] = self.news_util.to_dict()
        d["policy_tracking"] = self.policy_tracking.to_dict()
        return d


@dataclass(frozen=True)
class ComparisonGate:
    """Gate to detect incomparable conditions between arms."""
    cost_risk_fill_match: bool = True
    synthetic_mixed: bool = False
    conditions: dict[str, Any] = field(default_factory=dict)
    violations: list[str] = field(default_factory=list)

    def check(self, arm_a: ArmMetrics, arm_b: ArmMetrics) -> "ComparisonGate":
        violations = []
        if arm_a.cost_breakdown.cost_allocation_method != arm_b.cost_breakdown.cost_allocation_method:
            violations.append("cost_allocation_method mismatch")
        if arm_a.news_util.cost_allocation_method != arm_b.news_util.cost_allocation_method:
            violations.append("news cost_allocation_method mismatch")
        # Check synthetic/historical mixing
        if arm_a.policy_tracking.synthetic_historical_results or arm_b.policy_tracking.synthetic_historical_results:
            violations.append("synthetic_historical_results present in primary evaluation")
        if arm_a.policy_tracking.forward_saved_classification != arm_b.policy_tracking.forward_saved_classification:
            violations.append("forward_saved_classification mismatch")
        return ComparisonGate(
            cost_risk_fill_match=len(violations) == 0,
            synthetic_mixed=any("synthetic" in v for v in violations),
            conditions={
                "cost_allocation_a": arm_a.cost_breakdown.cost_allocation_method,
                "cost_allocation_b": arm_b.cost_breakdown.cost_allocation_method,
            },
            violations=violations
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class EvaluationResult:
    """Complete evaluation result with both arms and comparison."""
    manifest: EvaluationManifest
    arm_a: ArmMetrics
    arm_b: ArmMetrics
    comparison_gate: ComparisonGate
    live_eligible: bool = False
    review_due_ts: float = 0.0
    evaluated_at: float = 0.0
    # Reproducibility
    manifest_digest: str = ""
    run_command: str = ""
    pass_fail_skip: str = "skip"
    remaining_items: int = 0
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["manifest"] = self.manifest.to_dict()
        d["arm_a"] = self.arm_a.to_dict()
        d["arm_b"] = self.arm_b.to_dict()
        d["comparison_gate"] = self.comparison_gate.to_dict()
        return d

    def to_export_dict(self) -> dict:
        """Export dict without secrets, raw text, or unauthorized data."""
        base = self.to_dict()
        # Remove any potentially sensitive fields
        for arm in ("arm_a", "arm_b"):
            if arm in base and "news_util" in base[arm]:
                base[arm]["news_util"].pop("rejection_reasons", None)
        return base


def compute_drawdown(equity_series: list[tuple[float, Decimal]]) -> tuple[Decimal, float, float]:
    """Compute max drawdown from equity time series.

    Returns (max_drawdown_fraction, peak_ts, trough_ts).
    Does not assume worst drawdown in unobserved intervals.
    """
    if not equity_series:
        return D("0"), 0.0, 0.0

    peak = D("0")
    peak_ts = 0.0
    max_dd = D("0")
    max_dd_peak_ts = 0.0
    max_dd_trough_ts = 0.0

    for ts, equity in equity_series:
        if equity > peak:
            peak = equity
            peak_ts = ts
        elif peak > 0:
            dd = (peak - equity) / peak
            if dd > max_dd:
                max_dd = dd
                max_dd_peak_ts = peak_ts
                max_dd_trough_ts = ts

    return max_dd, max_dd_peak_ts, max_dd_trough_ts


def compute_win_rate(episodes: list[PositionEpisode]) -> tuple[int, int, float]:
    """Compute win rate from completed episodes only.

    Partial fills and open positions are excluded from denominator.
    """
    if not episodes:
        return 0, 0, 0.0
    wins = sum(1 for e in episodes if e.win)
    return len(episodes), wins, wins / len(episodes)


def compute_turnover(fills: list[FillRecord], avg_equity: Decimal) -> Optional[float]:
    """Compute turnover = sum of buy+sell notional / avg equity.

    Returns None if denominator is zero or insufficient data.
    """
    if avg_equity <= 0:
        return None
    if not fills:
        return None
    notional = D("0")
    for f in fills:
        notional += f.qty * f.price
    return float(notional / avg_equity)


def _percentile(vals: list[float], p: float) -> Optional[float]:
    """Compute percentile p (0-1) from sorted values."""
    if not vals:
        return None
    vals = sorted(vals)
    idx = int(len(vals) * p)
    return vals[min(idx, len(vals) - 1)]


def compute_latency_breakdown(events: list[dict]) -> LatencyBreakdown:
    """Compute median/p95 latency breakdown from event timestamps.

    Expects events with: published_at, fetched_at, available_at,
    classified_at, decided_at, filled_at (all optional).
    """
    stages = [
        ("published_to_fetched_ms", "published_at", "fetched_at"),
        ("fetched_to_available_ms", "fetched_at", "available_at"),
        ("available_to_classification_ms", "available_at", "classified_at"),
        ("classification_to_decision_ms", "classified_at", "decided_at"),
        ("decision_to_fill_ms", "decided_at", "filled_at"),
    ]

    missing = set()
    stage_durations = {name: [] for name, _, _ in stages}
    total_durations = []

    for ev in events:
        event_missing = False
        for name, start_k, end_k in stages:
            if start_k in ev and end_k in ev and ev[start_k] is not None and ev[end_k] is not None:
                stage_durations[name].append((ev[end_k] - ev[start_k]) * 1000)
            else:
                missing.add(name)
                event_missing = True
        # Compute event-level total latency (published_at to filled_at)
        if "published_at" in ev and "filled_at" in ev and ev["published_at"] is not None and ev["filled_at"] is not None:
            total_durations.append((ev["filled_at"] - ev["published_at"]) * 1000)
        elif not event_missing:
            if "published_at" not in ev or ev["published_at"] is None:
                missing.add("published_at")
            if "filled_at" not in ev or ev["filled_at"] is None:
                missing.add("filled_at")

    stage_median = {}
    stage_p95 = {}
    for name, _, _ in stages:
        stage_median[name] = _percentile(stage_durations[name], 0.5)
        stage_p95[name] = _percentile(stage_durations[name], 0.95)

    total_ms_median = _percentile(total_durations, 0.5)
    total_ms_p95 = _percentile(total_durations, 0.95)

    return LatencyBreakdown(
        published_to_fetched_ms=stage_median.get("published_to_fetched_ms"),
        fetched_to_available_ms=stage_median.get("fetched_to_available_ms"),
        available_to_classification_ms=stage_median.get("available_to_classification_ms"),
        classification_to_decision_ms=stage_median.get("classification_to_decision_ms"),
        decision_to_fill_ms=stage_median.get("decision_to_fill_ms"),
        total_ms_median=total_ms_median,
        total_ms_p95=total_ms_p95,
        stage_median_ms=stage_median,
        stage_p95_ms=stage_p95,
        missing_stages=sorted(list(missing))
    )


def _build_episodes_from_fills(fills: list[FillRecord]) -> list[PositionEpisode]:
    """Build completed position episodes from fills.

    Processes fills chronologically per symbol/side. Accumulates net position
    and cumulative PnL. Only creates episode when position fully closes (qty=0).
    """
    episodes = []
    from collections import defaultdict

    # Group all fills by symbol and side, sorted by timestamp
    fills_by_key = defaultdict(list)
    for f in fills:
        key = (f.symbol, f.side)
        fills_by_key[key].append(f)

    for key, key_fills in fills_by_key.items():
        symbol, side = key
        key_fills.sort(key=lambda f: f.ts)

        # Track open position: remaining_qty, cumulative_pnl, first_open_ts, first_open_price
        remaining_qty = D("0")
        cum_pnl = D("0")
        first_open_ts = 0.0
        first_open_price = D("0")
        open_fee_total = D("0")
        financing_total = D("0")
        close_fee_total = D("0")
        position_opened = False

        for f in key_fills:
            if f.kind == "open":
                if not position_opened:
                    # New episode starts
                    first_open_ts = f.ts
                    first_open_price = f.price
                    open_fee_total = f.fee
                    position_opened = True
                remaining_qty += f.qty
            elif f.kind == "close":
                if remaining_qty <= 0:
                    # Close without matching open - skip
                    continue
                close_qty = min(f.qty, remaining_qty)
                # Accumulate PnL and fees
                cum_pnl += f.net_pnl_jpy
                close_fee_total += f.fee
                financing_total += f.financing
                remaining_qty -= close_qty

                # If position fully closed, record episode
                if remaining_qty == 0 and position_opened:
                    episodes.append(PositionEpisode(
                        symbol=symbol,
                        side=side,
                        open_ts=first_open_ts,
                        close_ts=f.ts,
                        open_price=first_open_price,
                        close_price=f.price,
                        qty=close_qty,  # This is the final close qty; total qty was accumulated
                        open_fee=open_fee_total,
                        close_fee=close_fee_total,
                        financing=financing_total,
                        net_pnl_jpy=cum_pnl,
                        hold_seconds=f.ts - first_open_ts,
                        win=cum_pnl > 0
                    ))
                    # Reset for next episode
                    position_opened = False
                    cum_pnl = D("0")
                    close_fee_total = D("0")
                    financing_total = D("0")
                    open_fee_total = D("0")
                    first_open_ts = 0.0
                    first_open_price = D("0")

    return episodes


def _compute_eval_digest(
    fills: list[FillRecord],
    equity_series: list[tuple[float, Decimal]],
    positions: dict[str, dict],
    news_events: list[dict],
    arm: ArmMetrics,
) -> str:
    """Compute evaluation result digest."""
    eval_data = {
        "equity": str(arm.equity_jpy),
        "realized": str(arm.realized_pnl_jpy),
        "unrealized": str(arm.unrealized_pnl_jpy) if arm.unrealized_pnl_jpy is not None else None,
        "exit_cost": str(arm.estimated_exit_cost_jpy) if arm.estimated_exit_cost_jpy is not None else None,
        "dd": str(arm.max_drawdown_fraction),
        "episodes": arm.completed_episodes,
        "wins": arm.wins,
        "turnover": arm.turnover,
        "cost_allocation_method": arm.cost_breakdown.cost_allocation_method,
        "commission_jpy": str(arm.cost_breakdown.commission_jpy),
        "slippage_jpy": str(arm.cost_breakdown.slippage_jpy),
        "financing_jpy": str(arm.cost_breakdown.financing_jpy),
        "llm_api_cost_jpy": str(arm.cost_breakdown.llm_api_cost_jpy),
        "data_api_cost_jpy": str(arm.cost_breakdown.data_api_cost_jpy),
        "total_trading_cost_jpy": str(arm.cost_breakdown.total_trading_cost_jpy),
        "total_operating_cost_jpy": str(arm.cost_breakdown.total_operating_cost_jpy),
        "exit_commission_bps": str(arm.cost_breakdown.exit_commission_bps),
        "exit_slippage_bps": str(arm.cost_breakdown.exit_slippage_bps),
        "held_rate": arm.news_util.held_rate,
        "rejected_rate": arm.news_util.rejected_rate,
        "unique_eligible": arm.news_util.unique_eligible_events,
        "latency_missing_stages": arm.latency.missing_stages,
        "latency_stage_median": arm.latency.stage_median_ms,
        "latency_stage_p95": arm.latency.stage_p95_ms,
        "latency_total_median": arm.latency.total_ms_median,
        "latency_total_p95": arm.latency.total_ms_p95,
        "policy_independent_events": arm.policy_tracking.independent_events,
        "policy_policies_tested": arm.policy_tracking.policies_tested,
        "policy_train_events": arm.policy_tracking.train_events,
        "policy_val_events": arm.policy_tracking.val_events,
        "policy_holdout_events": arm.policy_tracking.holdout_events,
        "policy_synthetic": arm.policy_tracking.synthetic_historical_results,
        "policy_forward_saved": arm.policy_tracking.forward_saved_classification,
    }
    return hashlib.sha256(
        json.dumps(eval_data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]


def build_arm_metrics(
    fills: list[FillRecord],
    equity_series: list[tuple[float, Decimal]],
    positions: dict[str, dict],
    latency_events: list[dict],
    news_events: list[dict],
    cost_breakdown: CostBreakdown,
    policy_tracking: PolicyTracking,
    label: str,
    avg_equity: Decimal,
) -> ArmMetrics:
    """Build ArmMetrics from raw evaluation data."""
    # Position episodes - only fully closed positions with chronological accumulation
    episodes = _build_episodes_from_fills(fills)

    completed_episodes, wins, win_rate = compute_win_rate(episodes)

    # Drawdown
    if not equity_series:
        # Track as limitation: empty equity series is unverifiable
        pass
    max_dd, dd_peak_ts, dd_trough_ts = compute_drawdown(equity_series)

    # Turnover
    turnover = compute_turnover(fills, avg_equity)

    # Latency
    latency = compute_latency_breakdown(latency_events)

    # News utilization - deduplicate by event_id for rates
    # Denominator: unique eligible event IDs
    unique_eligible = {e.get("event_id") for e in news_events if e.get("eligible", False) and e.get("event_id")}
    unique_eligible_count = len(unique_eligible)

    # Numerators: intersect with eligible set
    used_signal = {e.get("event_id") for e in news_events if e.get("used_for_signal", False) and e.get("event_id")} & unique_eligible
    used_decision = {e.get("event_id") for e in news_events if e.get("used_for_decision", False) and e.get("event_id")} & unique_eligible
    held_events = {e.get("event_id") for e in news_events if e.get("held", False) and e.get("event_id")} & unique_eligible
    rejected_events = {e.get("event_id") for e in news_events if e.get("rejected", False) and e.get("event_id")} & unique_eligible

    raw = len(news_events)
    invalid = len([e for e in news_events if not e.get("valid", True)])
    duplicate = len([e for e in news_events if e.get("duplicate", False)])

    rejection_reasons = {}
    for e in news_events:
        if e.get("rejected", False):
            reason = e.get("rejection_reason", "unknown")
            rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
    obs_missing = sum(e.get("observation_gap_seconds", 0) for e in news_events)
    observation_evidence_missing = any("observation_gap_seconds" not in e for e in news_events)
    unknown_match = len([e for e in news_events if e.get("match_type") == "unknown"])
    human_match = len([e for e in news_events if e.get("match_type") == "human"])
    partial = len([f for f in fills if f.partial])
    canceled = len([f for f in fills if f.reason == "canceled"])
    max_exposure = D("0")
    for pos in positions.values():
        if "mark" in pos and "qty" in pos:
            try:
                exposure = decimal(pos["mark"]) * decimal(pos["qty"])
                max_exposure = max(max_exposure, exposure)
            except (ValueError, ArithmeticError):
                pass  # Skip invalid mark

    news_util = NewsUtilization(
        unique_eligible_events=unique_eligible_count,
        events_used_for_signal=len(used_signal),
        events_used_for_decision=len(used_decision),
        raw_events=raw,
        invalid_events=invalid,
        duplicate_events=duplicate,
        held_rate=len(held_events) / unique_eligible_count if unique_eligible_count > 0 else 0.0,
        rejected_rate=len(rejected_events) / unique_eligible_count if unique_eligible_count > 0 else 0.0,
        observation_evidence_missing=observation_evidence_missing,
        rejection_reasons=rejection_reasons,
        observation_missing_seconds=obs_missing,
        unknown_match=unknown_match,
        human_match=human_match,
        partial_fills=partial,
        canceled_fills=canceled,
        max_exposure_jpy=max_exposure,
    )

    # Missing/partial tracking
    missing_fills = len([f for f in fills if f.kind == "close" and f.partial and f.original_qty and f.original_qty > f.qty])
    partial_fills = len([f for f in fills if f.partial])
    unresolved = len(positions)
    data_gaps = sum(e.get("data_gap_seconds", 0) for e in news_events)

    # Unrealized PnL - None if any position missing mark or invalid
    unrealized_pnl = None
    estimated_exit_cost = None
    has_missing_mark = False
    for pos in positions.values():
        if "mark" not in pos or "qty" not in pos or "entry" not in pos:
            has_missing_mark = True
            break
        if pos.get("mark") is None:
            has_missing_mark = True
            break
        try:
            decimal(pos["mark"])
            decimal(pos["qty"])
            decimal(pos["entry"])
        except (ValueError, ArithmeticError, Exception):
            has_missing_mark = True
            break

    if not has_missing_mark and positions:
        try:
            unrealized_pnl = sum(
                (decimal(pos["mark"]) - decimal(pos["entry"])) * decimal(pos["qty"]) * Decimal(pos.get("side", "1"))
                for pos in positions.values()
            )
            # Estimated exit cost using explicit bps rates from cost_breakdown
            exit_comm_bps = cost_breakdown.exit_commission_bps
            exit_slippage_bps = cost_breakdown.exit_slippage_bps
            if exit_comm_bps > 0 or exit_slippage_bps > 0:
                estimated_exit_cost = sum(
                    decimal(pos["qty"]) * decimal(pos["mark"]) *
                    (exit_comm_bps + exit_slippage_bps) / 10000
                    for pos in positions.values()
                )
        except (ValueError, ArithmeticError):
            unrealized_pnl = None
            estimated_exit_cost = None

    # Compute evaluation digest for reproducibility
    eval_digest = _compute_eval_digest(fills, equity_series, positions, news_events, 
        ArmMetrics(label=label, equity_jpy=equity_series[-1][1] if equity_series else D("0"),
                   realized_pnl_jpy=sum(f.net_pnl_jpy for f in fills if f.kind == "close"),
                   unrealized_pnl_jpy=unrealized_pnl,
                   estimated_exit_cost_jpy=estimated_exit_cost,
                   max_drawdown_fraction=max_dd,
                   completed_episodes=completed_episodes,
                   wins=wins,
                   win_rate=win_rate,
                   open_positions=len(positions),
                   turnover=turnover,
                   avg_equity_jpy=avg_equity,
                   cost_breakdown=cost_breakdown,
                   latency=latency,
                   news_util=news_util,
                   policy_tracking=policy_tracking))

    metrics = ArmMetrics(
        label=label,
        equity_jpy=equity_series[-1][1] if equity_series else D("0"),
        realized_pnl_jpy=sum(f.net_pnl_jpy for f in fills if f.kind == "close"),
        unrealized_pnl_jpy=unrealized_pnl,
        estimated_exit_cost_jpy=estimated_exit_cost,
        max_drawdown_fraction=max_dd,
        max_drawdown_peak_ts=dd_peak_ts,
        max_drawdown_trough_ts=dd_trough_ts,
        completed_episodes=completed_episodes,
        wins=wins,
        win_rate=win_rate,
        open_positions=len(positions),
        turnover=turnover,
        avg_equity_jpy=avg_equity,
        cost_breakdown=cost_breakdown,
        latency=latency,
        news_util=news_util,
        policy_tracking=policy_tracking,
        missing_fills=missing_fills,
        partial_fills=partial_fills,
        unresolved_positions=unresolved,
        data_gaps_seconds=data_gaps,
        evaluation_digest=eval_digest,
    )
    return metrics


def _compute_digests(
    fills: list[FillRecord],
    equity_series: list[tuple[float, Decimal]],
    positions: dict[str, dict],
    news_events: list[dict],
    result_arm: ArmMetrics,
) -> dict[str, str]:
    """Compute normalized digests for reproducibility verification."""
    # Normalize fills
    fill_data = [f.to_dict() for f in sorted(fills, key=lambda x: (x.ts, x.symbol, x.kind))]
    fill_digest = hashlib.sha256(
        json.dumps(fill_data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]

    # Normalize equity series
    equity_data = [[str(ts), str(eq)] for ts, eq in sorted(equity_series)]
    equity_digest = hashlib.sha256(
        json.dumps(equity_data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]

    # Normalize positions (sorted by symbol)
    pos_data = []
    for symbol in sorted(positions.keys()):
        pos = positions[symbol]
        pos_data.append({symbol: {k: str(v) for k, v in pos.items()}})
    position_digest = hashlib.sha256(
        json.dumps(pos_data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]

    # Normalize news events (sorted by event_id then timestamp)
    news_data = []
    for e in sorted(news_events, key=lambda x: (x.get("event_id", ""), x.get("observed_at", 0))):
        news_data.append({k: str(v) for k, v in e.items()})
    news_digest = hashlib.sha256(
        json.dumps(news_data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]

    # Evaluation result digest
    eval_data = {
        "equity": str(result_arm.equity_jpy),
        "realized": str(result_arm.realized_pnl_jpy),
        "unrealized": str(result_arm.unrealized_pnl_jpy) if result_arm.unrealized_pnl_jpy is not None else None,
        "exit_cost": str(result_arm.estimated_exit_cost_jpy) if result_arm.estimated_exit_cost_jpy is not None else None,
        "dd": str(result_arm.max_drawdown_fraction),
        "episodes": result_arm.completed_episodes,
        "wins": result_arm.wins,
        "turnover": result_arm.turnover,
        "held_rate": result_arm.news_util.held_rate,
        "rejected_rate": result_arm.news_util.rejected_rate,
        "unique_eligible": result_arm.news_util.unique_eligible_events,
        # Cost breakdown (explicit rates for reproducibility) - must match _compute_eval_digest
        "cost_allocation_method": result_arm.cost_breakdown.cost_allocation_method,
        "commission_jpy": str(result_arm.cost_breakdown.commission_jpy),
        "slippage_jpy": str(result_arm.cost_breakdown.slippage_jpy),
        "financing_jpy": str(result_arm.cost_breakdown.financing_jpy),
        "llm_api_cost_jpy": str(result_arm.cost_breakdown.llm_api_cost_jpy),
        "data_api_cost_jpy": str(result_arm.cost_breakdown.data_api_cost_jpy),
        "total_trading_cost_jpy": str(result_arm.cost_breakdown.total_trading_cost_jpy),
        "total_operating_cost_jpy": str(result_arm.cost_breakdown.total_operating_cost_jpy),
        "exit_commission_bps": str(result_arm.cost_breakdown.exit_commission_bps),
        "exit_slippage_bps": str(result_arm.cost_breakdown.exit_slippage_bps),
        # Latency (stage medians and totals)
        "latency_total_median_ms": result_arm.latency.total_ms_median,
        "latency_total_p95_ms": result_arm.latency.total_ms_p95,
        "latency_stage_medians": {k: str(v) if v is not None else None for k, v in result_arm.latency.stage_median_ms.items()},
        "latency_stage_p95": {k: str(v) if v is not None else None for k, v in result_arm.latency.stage_p95_ms.items()},
        "latency_missing_stages": sorted(result_arm.latency.missing_stages),
        # Policy tracking
        "policy_independent_events": result_arm.policy_tracking.independent_events,
        "policy_policies_tested": result_arm.policy_tracking.policies_tested,
        "policy_train_events": result_arm.policy_tracking.train_events,
        "policy_val_events": result_arm.policy_tracking.val_events,
        "policy_holdout_events": result_arm.policy_tracking.holdout_events,
        "policy_synthetic": result_arm.policy_tracking.synthetic_historical_results,
        "policy_forward_saved": result_arm.policy_tracking.forward_saved_classification,
    }
    eval_digest = hashlib.sha256(
        json.dumps(eval_data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]

    return {
        "fill_digest": fill_digest,
        "equity_digest": equity_digest,
        "position_digest": position_digest,
        "news_digest": news_digest,
        "evaluation_digest": eval_digest,
    }


def run_evaluation(
    manifest: EvaluationManifest,
    arm_a_fills: list[FillRecord],
    arm_a_equity_series: list[tuple[float, Decimal]],
    arm_a_positions: dict[str, dict],
    arm_a_latency_events: list[dict],
    arm_a_news_events: list[dict],
    arm_b_fills: list[FillRecord],
    arm_b_equity_series: list[tuple[float, Decimal]],
    arm_b_positions: dict[str, dict],
    arm_b_latency_events: list[dict],
    arm_b_news_events: list[dict],
    cost_breakdown_a: CostBreakdown,
    cost_breakdown_b: CostBreakdown,
    policy_tracking: PolicyTracking,
    avg_equity_a: Decimal,
    avg_equity_b: Decimal,
    run_command: str,
) -> EvaluationResult:
    """Run complete evaluation and produce result."""
    # Validate manifest first
    manifest_violations = manifest.validate()
    if manifest_violations:
        # Return early with skip and violations recorded
        arm_a = ArmMetrics(label=manifest.arm_a_label)
        arm_b = ArmMetrics(label=manifest.arm_b_label)
        comparison_gate = ComparisonGate()
        return EvaluationResult(
            manifest=manifest,
            arm_a=arm_a,
            arm_b=arm_b,
            comparison_gate=comparison_gate,
            live_eligible=False,
            review_due_ts=manifest.eval_end_ts,
            evaluated_at=__import__("time").time(),
            manifest_digest=manifest.digest(),
            run_command=run_command,
            pass_fail_skip="skip",
            remaining_items=len(manifest_violations),
            limitations=["primary_evidence_only", "live_eligible_false", "no_existing_policy_writes"]
            + manifest_violations
        )

    arm_a = build_arm_metrics(
        arm_a_fills, arm_a_equity_series, arm_a_positions,
        arm_a_latency_events, arm_a_news_events, cost_breakdown_a,
        policy_tracking, manifest.arm_a_label, avg_equity_a
    )
    arm_b = build_arm_metrics(
        arm_b_fills, arm_b_equity_series, arm_b_positions,
        arm_b_latency_events, arm_b_news_events, cost_breakdown_b,
        policy_tracking, manifest.arm_b_label, avg_equity_b
    )

    comparison_gate = ComparisonGate().check(arm_a, arm_b)

    # Check manifest constraints against actual data
    remaining_items = 0
    limitations = ["primary_evidence_only", "live_eligible_false", "no_existing_policy_writes"]
    limitations += comparison_gate.violations or []

    # Check min_independent_events against PolicyTracking.independent_events (not unique_eligible)
    tracked_independent = policy_tracking.independent_events
    if manifest.min_independent_events > 0:
        if tracked_independent < manifest.min_independent_events:
            remaining_items += 1
            limitations.append(f"policy_tracking.independent_events ({tracked_independent}) < min ({manifest.min_independent_events})")

    # Check empty equity series (unverifiable)
    if not arm_a_equity_series or not arm_b_equity_series:
        remaining_items += 1
        limitations.append("empty_equity_series_unverifiable")

    # Check min_coverage_fraction - if we can't compute coverage, mark as unknown/remaining
    # Coverage requires actual observation window data; here we use data_gaps/observation_missing as proxy
    # If coverage fraction is required but we have gaps, it's a remaining item
    if manifest.min_coverage_fraction > 0:
        # We can't compute actual coverage without full observation data
        # Mark as unknown/remaining unless we have explicit coverage data
        # Need actual observation data: unique_eligible_events > 0 AND data_gaps_seconds < window_duration
        # For min_coverage_fraction >= 1.0 (100%), any gap (observation_missing or data_gaps) should fail
        window_duration = manifest.eval_end_ts - manifest.eval_start_ts
        if manifest.min_coverage_fraction >= 1.0:
            # 100% coverage requires zero gaps
            has_coverage_data = (
                arm_a.news_util.unique_eligible_events > 0 and
                arm_b.news_util.unique_eligible_events > 0 and
                not arm_a.news_util.observation_evidence_missing and
                not arm_b.news_util.observation_evidence_missing and
                arm_a.data_gaps_seconds == 0 and
                arm_b.data_gaps_seconds == 0 and
                arm_a.news_util.observation_missing_seconds == 0 and
                arm_b.news_util.observation_missing_seconds == 0
            )
        else:
            # Partial coverage: gaps must fit within (1 - coverage) * window_duration
            _allowed = (D(str(1.0)) - D(str(manifest.min_coverage_fraction))) * D(str(window_duration))
            allowed_gap_a = float(_allowed)
            allowed_gap_b = float(_allowed)
            has_coverage_data = (
                arm_a.news_util.unique_eligible_events > 0 and
                arm_b.news_util.unique_eligible_events > 0 and
                not arm_a.news_util.observation_evidence_missing and
                not arm_b.news_util.observation_evidence_missing and
                arm_a.data_gaps_seconds <= allowed_gap_a and
                arm_b.data_gaps_seconds <= allowed_gap_b and
                arm_a.news_util.observation_missing_seconds <= allowed_gap_a and
                arm_b.news_util.observation_missing_seconds <= allowed_gap_b
            )
        if not has_coverage_data:
            remaining_items += 1
            limitations.append("coverage_fraction_unverifiable (missing observation data or full window gaps)")

    # Check for empty required version fields in manifest (already validated but double-check)
    required_fields = [
        ("ledger_revision", manifest.ledger_revision),
        ("evidence_digest", manifest.evidence_digest),
        ("classification_version", manifest.classification_version),
        ("price_version", manifest.price_version),
        ("risk_version", manifest.risk_version),
        ("cost_version", manifest.cost_version),
        ("execution_version", manifest.execution_version),
        ("policy_version", manifest.policy_version),
        ("model_version", manifest.model_version),
    ]
    for name, val in required_fields:
        if not val:
            remaining_items += 1
            limitations.append(f"manifest missing required version: {name}")

    manifest_digest = manifest.digest()
    evaluated_at = __import__("time").time()

    result = EvaluationResult(
        manifest=manifest,
        arm_a=arm_a,
        arm_b=arm_b,
        comparison_gate=comparison_gate,
        live_eligible=False,
        review_due_ts=manifest.eval_end_ts,
        evaluated_at=evaluated_at,
        manifest_digest=manifest_digest,
        run_command=run_command,
        pass_fail_skip="pass" if comparison_gate.cost_risk_fill_match and remaining_items == 0 else "fail",
        remaining_items=remaining_items,
        limitations=limitations
    )
    return result


def export_evaluation(result: EvaluationResult, output_path: Path) -> None:
    """Write evaluation result to file (read-only export, no secrets)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        json.dump(result.to_export_dict(), f, ensure_ascii=False, indent=2, sort_keys=True)


def verify_reproducibility(
    manifest: EvaluationManifest,
    arm_a_fills_prev: list[FillRecord],
    arm_a_equity_prev: list[tuple[float, Decimal]],
    arm_a_positions_prev: dict[str, dict],
    arm_a_news_prev: list[dict],
    arm_a_arm_metrics_prev: ArmMetrics,
    arm_a_fills_curr: list[FillRecord],
    arm_a_equity_curr: list[tuple[float, Decimal]],
    arm_a_positions_curr: dict[str, dict],
    arm_a_news_curr: list[dict],
    arm_a_arm_metrics_curr: ArmMetrics,
    arm_b_fills_prev: list[FillRecord],
    arm_b_equity_prev: list[tuple[float, Decimal]],
    arm_b_positions_prev: dict[str, dict],
    arm_b_news_prev: list[dict],
    arm_b_arm_metrics_prev: ArmMetrics,
    arm_b_fills_curr: list[FillRecord],
    arm_b_equity_curr: list[tuple[float, Decimal]],
    arm_b_positions_curr: dict[str, dict],
    arm_b_news_curr: list[dict],
    arm_b_arm_metrics_curr: ArmMetrics,
) -> dict:
    """Verify that re-run with same manifest produces identical digests.

    Includes fills, equity, positions, news events, and evaluation results.
    """
    prev_a = _compute_digests(arm_a_fills_prev, arm_a_equity_prev, arm_a_positions_prev, arm_a_news_prev, arm_a_arm_metrics_prev)
    curr_a = _compute_digests(arm_a_fills_curr, arm_a_equity_curr, arm_a_positions_curr, arm_a_news_curr, arm_a_arm_metrics_curr)
    prev_b = _compute_digests(arm_b_fills_prev, arm_b_equity_prev, arm_b_positions_prev, arm_b_news_prev, arm_b_arm_metrics_prev)
    curr_b = _compute_digests(arm_b_fills_curr, arm_b_equity_curr, arm_b_positions_curr, arm_b_news_curr, arm_b_arm_metrics_curr)

    return {
        "manifest_digest": manifest.digest(),
        "arm_a": {
            "fill_digest_match": prev_a["fill_digest"] == curr_a["fill_digest"],
            "equity_digest_match": prev_a["equity_digest"] == curr_a["equity_digest"],
            "position_digest_match": prev_a["position_digest"] == curr_a["position_digest"],
            "news_digest_match": prev_a["news_digest"] == curr_a["news_digest"],
            "evaluation_digest_match": prev_a["evaluation_digest"] == curr_a["evaluation_digest"],
        },
        "arm_b": {
            "fill_digest_match": prev_b["fill_digest"] == curr_b["fill_digest"],
            "equity_digest_match": prev_b["equity_digest"] == curr_b["equity_digest"],
            "position_digest_match": prev_b["position_digest"] == curr_b["position_digest"],
            "news_digest_match": prev_b["news_digest"] == curr_b["news_digest"],
            "evaluation_digest_match": prev_b["evaluation_digest"] == curr_b["evaluation_digest"],
        },
        "reproducible": (
            prev_a["fill_digest"] == curr_a["fill_digest"] and
            prev_a["equity_digest"] == curr_a["equity_digest"] and
            prev_a["position_digest"] == curr_a["position_digest"] and
            prev_a["news_digest"] == curr_a["news_digest"] and
            prev_a["evaluation_digest"] == curr_a["evaluation_digest"] and
            prev_b["fill_digest"] == curr_b["fill_digest"] and
            prev_b["equity_digest"] == curr_b["equity_digest"] and
            prev_b["position_digest"] == curr_b["position_digest"] and
            prev_b["news_digest"] == curr_b["news_digest"] and
            prev_b["evaluation_digest"] == curr_b["evaluation_digest"]
        ),
        "scope": "full (ledger + news + evaluation)"
    }
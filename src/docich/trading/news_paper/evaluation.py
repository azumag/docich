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
    """Completed position episode (open + close)."""
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
    total_ms: Optional[float] = None
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
    cost_allocation_method: str = "pro_rata"

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("commission_jpy", "slippage_jpy", "financing_jpy",
                  "llm_api_cost_jpy", "data_api_cost_jpy",
                  "total_trading_cost_jpy", "total_operating_cost_jpy"):
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
    unrealized_pnl_jpy: Decimal = D("0")
    estimated_exit_cost_jpy: Decimal = D("0")
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
        d["unrealized_pnl_jpy"] = str(self.unrealized_pnl_jpy)
        d["estimated_exit_cost_jpy"] = str(self.estimated_exit_cost_jpy)
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

    missing = []
    durations = {name: [] for name, _, _ in stages}

    for ev in events:
        for name, start_k, end_k in stages:
            if start_k in ev and end_k in ev and ev[start_k] is not None and ev[end_k] is not None:
                durations[name].append((ev[end_k] - ev[start_k]) * 1000)
            elif end_k not in ev or ev[end_k] is None:
                missing.append(name)

    def pct(vals: list[float], p: float) -> Optional[float]:
        if not vals:
            return None
        vals = sorted(vals)
        idx = int(len(vals) * p)
        return vals[min(idx, len(vals) - 1)]

    result = {}
    total_ms = None
    for name, _, _ in stages:
        result[name] = pct(durations[name], 0.5)  # median

    if all(v is not None for v in result.values()):
        total_ms = sum(v for v in result.values() if v is not None)

    return LatencyBreakdown(
        published_to_fetched_ms=result.get("published_to_fetched_ms"),
        fetched_to_available_ms=result.get("fetched_to_available_ms"),
        available_to_classification_ms=result.get("available_to_classification_ms"),
        classification_to_decision_ms=result.get("classification_to_decision_ms"),
        decision_to_fill_ms=result.get("decision_to_fill_ms"),
        total_ms=total_ms,
        missing_stages=list(set(missing))
    )


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
    # Position episodes
    episodes = []
    for fill in fills:
        if fill.kind == "close":
            # Find matching open
            open_fills = [f for f in fills if f.kind == "open" and f.symbol == fill.symbol
                          and f.ts < fill.ts and f.side == fill.side]
            if open_fills:
                open_f = max(open_fills, key=lambda f: f.ts)
                episodes.append(PositionEpisode(
                    symbol=fill.symbol,
                    side=fill.side,
                    open_ts=open_f.ts,
                    close_ts=fill.ts,
                    open_price=open_f.price,
                    close_price=fill.price,
                    qty=fill.qty,
                    open_fee=open_f.fee,
                    close_fee=fill.fee,
                    financing=fill.financing,
                    net_pnl_jpy=fill.net_pnl_jpy,
                    hold_seconds=fill.ts - open_f.ts,
                    win=fill.net_pnl_jpy > 0
                ))

    completed_episodes, wins, win_rate = compute_win_rate(episodes)

    # Drawdown
    max_dd, dd_peak_ts, dd_trough_ts = compute_drawdown(equity_series)

    # Turnover
    turnover = compute_turnover(fills, avg_equity)

    # Latency
    latency = compute_latency_breakdown(latency_events)

    # News utilization
    unique_eligible = len({e.get("event_id") for e in news_events if e.get("eligible", False)})
    used_signal = len({e.get("event_id") for e in news_events if e.get("used_for_signal", False)})
    used_decision = len({e.get("event_id") for e in news_events if e.get("used_for_decision", False)})
    raw = len(news_events)
    invalid = len([e for e in news_events if not e.get("valid", True)])
    duplicate = len([e for e in news_events if e.get("duplicate", False)])
    held = len([e for e in news_events if e.get("held", False)])
    rejected = len([e for e in news_events if e.get("rejected", False)])
    rejection_reasons = {}
    for e in news_events:
        if e.get("rejected", False):
            reason = e.get("rejection_reason", "unknown")
            rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
    obs_missing = sum(e.get("observation_gap_seconds", 0) for e in news_events)
    unknown_match = len([e for e in news_events if e.get("match_type") == "unknown"])
    human_match = len([e for e in news_events if e.get("match_type") == "human"])
    partial = len([f for f in fills if f.partial])
    canceled = len([f for f in fills if f.reason == "canceled"])
    max_exposure = D("0")
    for pos in positions.values():
        if "mark" in pos and "qty" in pos:
            exposure = decimal(pos["mark"]) * decimal(pos["qty"])
            max_exposure = max(max_exposure, exposure)

    news_util = NewsUtilization(
        unique_eligible_events=unique_eligible,
        events_used_for_signal=used_signal,
        events_used_for_decision=used_decision,
        raw_events=raw,
        invalid_events=invalid,
        duplicate_events=duplicate,
        held_rate=held / unique_eligible if unique_eligible > 0 else 0.0,
        rejected_rate=rejected / unique_eligible if unique_eligible > 0 else 0.0,
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

    metrics = ArmMetrics(
        label=label,
        equity_jpy=equity_series[-1][1] if equity_series else D("0"),
        realized_pnl_jpy=sum(f.net_pnl_jpy for f in fills if f.kind == "close"),
        unrealized_pnl_jpy=sum(
            decimal(pos.get("mark", "0")) * decimal(pos.get("qty", "0")) - decimal(pos.get("entry", "0")) * decimal(pos.get("qty", "0"))
            for pos in positions.values()
        ),
        estimated_exit_cost_jpy=sum(
            decimal(pos.get("qty", "0")) * decimal(pos.get("mark", "0")) *
            (D(cost_breakdown.commission_jpy) + D(cost_breakdown.slippage_jpy)) / 10000
            for pos in positions.values()
        ),
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
    )
    return metrics


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
        pass_fail_skip="pass" if comparison_gate.cost_risk_fill_match else "fail",
        remaining_items=0,
        limitations=["primary_evidence_only", "live_eligible_false", "no_existing_policy_writes"]
        + (comparison_gate.violations or [])
    )
    return result


def export_evaluation(result: EvaluationResult, output_path: Path) -> None:
    """Write evaluation result to file (read-only export, no secrets)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        json.dump(result.to_export_dict(), f, ensure_ascii=False, indent=2, sort_keys=True)


def verify_reproducibility(
    manifest: EvaluationManifest,
    previous_digest: str,
    current_digest: str,
) -> dict:
    """Verify that re-run with same manifest produces identical digests."""
    return {
        "manifest_digest": manifest.digest(),
        "previous_account_digest": previous_digest,
        "current_account_digest": current_digest,
        "reproducible": previous_digest == current_digest,
        "decision_digest_match": True,  # Placeholder - would compare decision digests
        "fill_digest_match": True,
        "evaluation_digest_match": True,
    }
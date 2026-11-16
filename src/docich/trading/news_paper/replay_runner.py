"""N04b replay runner: real N05/N06 execution with A/B conditions and digests.

The N04a foundation (``replay.py``) validates a manifest and exposes a
deterministic point-in-time view.  This module is the N04b integration layer:
it drives a scenario through the real ``PaperManager`` -> N06 execution model
-> N05 ``NewsLedger`` path and produces normalized decision / fill / account
digests.

The runner deliberately owns no strategy and no wall clock:

* the manifest clock's ``fence_at`` (epoch seconds) is the only time origin;
* every step supplies its own observation time;
* A and B arms share one scenario and differ only by the explicit news-filter
  flag (and, for B, the injected classification delay).  ``ab_common_conditions``
  refuses any other configuration difference.

All values are fixture inputs.  Nothing here is wired to live data, a broker,
an LLM or the network.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from .execution import (
    CONTRACT_MAX_ERROR_MS,
    EXECUTION_MODEL_VERSION,
    ClockQuality,
    InstrumentConstraints,
    MarketObservation,
)
from .ledger import NewsLedger
from .manager import DecisionRequest, ManagerPolicy, PaperManager
from .replay import Manifest, ReplayError
from .risk import RiskPolicy

UTC = timezone.utc


def _canonical(value: Any) -> Any:
    """Return a JSON-safe, order-stable representation of fixture values."""
    if hasattr(value, "__dataclass_fields__"):
        return _canonical(asdict(value))
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ReplayError("naive timestamp in replay data", code="TIMEZONE_MISSING")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    return str(value)


def _digest(payload: Any) -> str:
    encoded = json.dumps(
        _canonical(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ReplayError(f"{name} must be timezone-aware", code="TIMEZONE_MISSING",
                          stage="scenario")
    return value.astimezone(UTC)


def _positive(value: Any, name: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception as exc:  # noqa: BLE001 - fixed-code contract boundary
        raise ReplayError(f"{name} is not a decimal", code="TYPE_INVALID",
                          stage="conditions") from exc
    if not result.is_finite() or result <= 0:
        raise ReplayError(f"{name} must be positive", code="VALUE_OUT_OF_RANGE",
                          stage="conditions")
    return result


@dataclass(frozen=True)
class ReplayConditions:
    """Everything that must be identical between the A and B arms.

    Fee/slippage/lot/tick live in ``constraints``; extra execution latency lives
    in ``manager_policy.execution_delay_ms``; the next-observation model version
    is explicit.  ``news_filter`` and B's ``classification_delay_ms`` are the
    only permitted arm differences.
    """

    initial_capital: Decimal
    universe: tuple[str, ...]
    warmup_seconds: int
    window_start: datetime
    window_end: datetime
    risk_policy: RiskPolicy
    constraints: tuple[tuple[str, InstrumentConstraints], ...]
    manager_policy: ManagerPolicy
    execution_model_version: str = EXECUTION_MODEL_VERSION
    account_id: str = "paper_news_replay"
    quote_currency: str = "JPY"
    news_filter: bool = False
    classification_delay_ms: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "initial_capital",
                           _positive(self.initial_capital, "initial_capital"))
        universe = tuple(self.universe)
        if not universe or len(set(universe)) != len(universe):
            raise ReplayError("universe must be non-empty and unique",
                              code="VALUE_OUT_OF_RANGE", stage="conditions")
        object.__setattr__(self, "universe", universe)
        if not isinstance(self.warmup_seconds, int) or isinstance(self.warmup_seconds, bool) \
                or self.warmup_seconds < 0:
            raise ReplayError("warmup_seconds must be >= 0", code="VALUE_OUT_OF_RANGE",
                              stage="conditions")
        object.__setattr__(self, "window_start", _aware(self.window_start, "window_start"))
        object.__setattr__(self, "window_end", _aware(self.window_end, "window_end"))
        if self.window_end <= self.window_start:
            raise ReplayError("window_end must be after window_start",
                              code="VALUE_OUT_OF_RANGE", stage="conditions")
        if self.execution_model_version != EXECUTION_MODEL_VERSION:
            raise ReplayError("unsupported execution model", code="SCHEMA_VERSION_UNSUPPORTED",
                              stage="conditions")
        if not isinstance(self.news_filter, bool):
            raise ReplayError("news_filter must be boolean", code="TYPE_INVALID",
                              stage="conditions")
        if not isinstance(self.classification_delay_ms, int) \
                or isinstance(self.classification_delay_ms, bool) \
                or self.classification_delay_ms < 0:
            raise ReplayError("classification_delay_ms must be >= 0",
                              code="VALUE_OUT_OF_RANGE", stage="conditions")
        if not self.news_filter and self.classification_delay_ms:
            raise ReplayError("classification delay requires a news filter",
                              code="VALUE_OUT_OF_RANGE", stage="conditions")
        constraints = tuple(self.constraints)
        assets = tuple(asset for asset, _ in constraints)
        if len(set(assets)) != len(assets) or set(assets) != set(universe):
            raise ReplayError("constraints must cover the universe exactly once",
                              code="VALUE_OUT_OF_RANGE", stage="conditions")
        for asset, constraint in constraints:
            if not isinstance(asset, str) or not asset:
                raise ReplayError("bad asset", code="VALUE_OUT_OF_RANGE", stage="conditions")
            if not isinstance(constraint, InstrumentConstraints):
                raise ReplayError("bad instrument constraints", code="TYPE_INVALID",
                                  stage="conditions")
        object.__setattr__(self, "constraints", constraints)
        if not isinstance(self.risk_policy, RiskPolicy):
            raise ReplayError("risk_policy is required", code="TYPE_INVALID", stage="conditions")
        if not isinstance(self.manager_policy, ManagerPolicy):
            raise ReplayError("manager_policy is required", code="TYPE_INVALID",
                              stage="conditions")
        if not (isinstance(self.account_id, str) and self.account_id
                and isinstance(self.quote_currency, str) and self.quote_currency):
            raise ReplayError("account/quote currency required",
                              code="VALUE_OUT_OF_RANGE", stage="conditions")

    def constraint_for(self, asset: str) -> InstrumentConstraints:
        for key, value in self.constraints:
            if key == asset:
                return value
        raise ReplayError(f"asset outside universe: {asset}", code="VALUE_OUT_OF_RANGE",
                          stage="conditions")

    def model_conditions(self) -> "ReplayConditions":
        """Return the shared model view with arm-only fields neutralized."""
        return replace(self, news_filter=False, classification_delay_ms=0)

    def to_dict(self) -> dict:
        return _canonical(self)


@dataclass(frozen=True)
class ReplayArm:
    arm: str
    conditions: ReplayConditions

    def __post_init__(self) -> None:
        if self.arm not in {"A", "B"}:
            raise ReplayError("arm must be A or B", code="VALUE_OUT_OF_RANGE")
        if self.arm == "A" and self.conditions.news_filter:
            raise ReplayError("arm A cannot apply a news filter",
                              code="VALUE_OUT_OF_RANGE", stage="conditions")
        if self.arm == "B" and not self.conditions.news_filter:
            raise ReplayError("arm B must apply a news filter",
                              code="VALUE_OUT_OF_RANGE", stage="conditions")


@dataclass(frozen=True)
class ReplayStep:
    """One deterministic action in a scenario.

    A decision step submits a candidate.  An observation step processes the
    pending order created by ``receipt_id``.  ``news_eligible=False`` is the
    fixture-level B news filter; arm A ignores it.
    """

    at: datetime
    decision: DecisionRequest | None = None
    receipt_id: str | None = None
    observations: tuple[MarketObservation, ...] = ()
    news_eligible: bool | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "at", _aware(self.at, "at"))
        has_decision = self.decision is not None
        has_observations = bool(self.observations)
        if has_decision == has_observations:
            raise ReplayError("step must be exactly one of decision/observation",
                              code="VALUE_OUT_OF_RANGE", stage="scenario")
        if has_decision:
            if self.receipt_id is not None:
                raise ReplayError("decision step must not carry receipt_id",
                                  code="VALUE_OUT_OF_RANGE", stage="scenario")
            if not isinstance(self.decision, DecisionRequest):
                raise ReplayError("bad decision request", code="TYPE_INVALID",
                                  stage="scenario")
            if self.news_eligible is not None and not isinstance(self.news_eligible, bool):
                raise ReplayError("news_eligible must be boolean or None",
                                  code="TYPE_INVALID", stage="scenario")
        else:
            if not isinstance(self.receipt_id, str) or not self.receipt_id:
                raise ReplayError("observation step requires receipt_id",
                                  code="VALUE_OUT_OF_RANGE", stage="scenario")
            if self.news_eligible is not None:
                raise ReplayError("news_eligible belongs to decision steps",
                                  code="VALUE_OUT_OF_RANGE", stage="scenario")
            for observation in self.observations:
                if not isinstance(observation, MarketObservation):
                    raise ReplayError("bad observation", code="TYPE_INVALID",
                                      stage="scenario")


@dataclass(frozen=True)
class ReplayScenario:
    steps: tuple[ReplayStep, ...]

    def __post_init__(self) -> None:
        steps = tuple(self.steps)
        if not steps:
            raise ReplayError("scenario must have at least one step",
                              code="VALUE_OUT_OF_RANGE", stage="scenario")
        for step in steps:
            if not isinstance(step, ReplayStep):
                raise ReplayError("bad scenario step", code="TYPE_INVALID", stage="scenario")
        object.__setattr__(self, "steps", steps)

    def ordered(self) -> tuple[ReplayStep, ...]:
        return tuple(sorted(self.steps, key=lambda step: step.at))

    def digest(self) -> str:
        return _digest(self)


@dataclass(frozen=True)
class ReplayRun:
    arm: str
    run_id: str
    scenario_id: str
    manifest_digest: str
    conditions_digest: str
    decision_digest: str
    fill_digest: str
    account_digest: str
    decision_count: int
    fill_count: int
    actions: tuple[dict, ...]
    fills: tuple[dict, ...]
    account: dict

    def to_dict(self) -> dict:
        return _canonical(self)


@dataclass(frozen=True)
class ReproCheck:
    matches: bool
    first: ReplayRun
    second: ReplayRun
    mismatches: tuple[str, ...]

    def to_dict(self) -> dict:
        return _canonical({
            "matches": self.matches,
            "mismatches": self.mismatches,
            "first": self.first,
            "second": self.second,
        })


@dataclass(frozen=True)
class CommonConditionsEvidence:
    common_model_digest: str
    manifest_common_digest: str
    scenario_digest: str
    manifest_versions: dict
    conditions: dict
    differences: dict

    def to_dict(self) -> dict:
        return _canonical(self)


@dataclass(frozen=True)
class N04bResult:
    common: CommonConditionsEvidence
    arm_a: ReproCheck
    arm_b: ReproCheck
    passed: bool

    def to_dict(self) -> dict:
        return _canonical(self)


def _manifest_fence(manifest: Manifest) -> datetime:
    fence = manifest.header.clock.get("fence_at") if isinstance(manifest.header.clock, dict) else None
    if isinstance(fence, bool) or not isinstance(fence, (int, float)):
        raise ReplayError("manifest clock.fence_at must be numeric", code="TYPE_INVALID",
                          stage="header.clock")
    try:
        return datetime.fromtimestamp(float(fence), tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise ReplayError("manifest clock.fence_at is not a valid epoch",
                          code="VALUE_OUT_OF_RANGE", stage="header.clock") from exc


def _manifest_signature(manifest: Manifest) -> str:
    header = asdict(manifest.header)
    header.pop("arm", None)
    chunks = [
        {"seq": chunk.seq, "filename": chunk.filename, "sha256": chunk.sha256,
         "record_count": chunk.record_count}
        for chunk in manifest.chunks
    ]
    return _digest({"header": header, "chunks": chunks})


class _StepClock:
    def __init__(self, start: datetime) -> None:
        self._now = start

    def set(self, moment: datetime) -> None:
        if moment < self._now:
            raise ReplayError("replay clock cannot go back", code="VALUE_OUT_OF_RANGE",
                              stage="scenario")
        self._now = moment

    def __call__(self) -> datetime:
        return self._now


def _request_for(arm: ReplayArm, manifest: Manifest, step: ReplayStep) -> tuple[DecisionRequest, datetime]:
    request = step.decision
    assert request is not None
    header = manifest.header
    if request.run_id != header.run_id or request.scenario_id != header.scenario_id:
        raise ReplayError("decision run/scenario does not match manifest",
                          code="ACCOUNT_SCOPE_MISMATCH", stage="scenario")
    if request.account_id != arm.conditions.account_id:
        raise ReplayError("decision account does not match conditions",
                          code="ACCOUNT_SCOPE_MISMATCH", stage="scenario")
    if request.asset not in arm.conditions.universe:
        raise ReplayError("decision asset outside universe", code="VALUE_OUT_OF_RANGE",
                          stage="scenario")
    if request.constraints != arm.conditions.constraint_for(request.asset):
        raise ReplayError("decision constraints do not match the arm conditions",
                          code="ACCOUNT_SCOPE_MISMATCH", stage="scenario")
    at = step.at
    if arm.conditions.news_filter and step.news_eligible is not None \
            and arm.conditions.classification_delay_ms:
        delay = timedelta(milliseconds=arm.conditions.classification_delay_ms)
        at = at + delay
        request = replace(
            request,
            decision_at=request.decision_at + delay,
            manager_seen_at=request.manager_seen_at + delay,
        )
    if not (arm.conditions.window_start <= at <= arm.conditions.window_end):
        raise ReplayError("decision outside the replay window", code="VALUE_OUT_OF_RANGE",
                          stage="scenario")
    if not (arm.conditions.window_start <= request.decision_at <= arm.conditions.window_end):
        raise ReplayError("decision timestamp outside the replay window",
                          code="VALUE_OUT_OF_RANGE", stage="scenario")
    if request.manager_seen_at > request.decision_at:
        raise ReplayError("manager_seen_at must not follow decision_at",
                          code="VALUE_OUT_OF_RANGE", stage="scenario")
    if not (request.decision_at <= request.expires_at):
        raise ReplayError("decision expires before it is made", code="VALUE_OUT_OF_RANGE",
                          stage="scenario")
    return request, at


def _rows(ledger: NewsLedger, table: str, columns: tuple[str, ...],
          order_by: str) -> list[dict]:
    sql = f"SELECT {', '.join(columns)} FROM {table} ORDER BY {order_by}"
    rows = ledger._conn.execute(sql).fetchall()
    return [dict(zip(columns, row)) for row in rows]


def _ledger_snapshot(ledger: NewsLedger) -> tuple[list[dict], list[dict], list[dict]]:
    receipts = _rows(ledger, "decision_receipts", (
        "receipt_id", "run_id", "scenario_id", "account_id", "decision_at",
        "manager_seen_at", "input_cutoff_sequence", "signal_refs", "market_refs",
        "decision_quote", "policy_version", "risk_policy_version",
        "execution_model_version", "status", "reason_codes", "order_ref",
        "supersedes_receipt_id", "receipt_committed_at", "abandoned_at",
    ), "receipt_id")
    orders = _rows(ledger, "paper_orders", (
        "order_id", "receipt_id", "run_id", "scenario_id", "account_id", "asset",
        "side", "requested_quantity", "filled_quantity", "remaining_quantity",
        "time_in_force", "status", "accepted_at", "not_before_at", "expires_at",
        "reservation_id", "event_use_id", "execution_model_version",
    ), "order_id")
    fills = _rows(ledger, "fills", (
        "fill_id", "order_id", "receipt_id", "account_id", "asset", "side",
        "quantity", "price", "fee_jpy", "slippage_bps", "quote_ref", "level_index",
        "market_event_at", "market_available_at", "filled_at", "partial",
        "execution_model_version",
    ), "fill_id")
    cancels = _rows(ledger, "cancel_events", (
        "cancel_id", "order_id", "quantity", "reason_code", "canceled_at",
    ), "cancel_id")
    accounts = _rows(ledger, "accounts", (
        "account_id", "run_id", "scenario_id", "quote_currency", "initial_cash",
        "cash", "revision", "created_at",
    ), "account_id")
    positions = _rows(ledger, "positions", (
        "account_id", "asset", "quantity", "avg_cost",
    ), "account_id, asset")
    reservations = _rows(ledger, "reservations", (
        "reservation_id", "account_id", "order_id", "kind", "asset", "quantity",
        "amount", "created_at", "released_at", "release_reason",
    ), "reservation_id")
    valuations = _rows(ledger, "valuations", (
        "valuation_id", "account_id", "as_of", "cash", "positions_value",
        "total_equity", "stale",
    ), "valuation_id")
    decisions = {"receipts": receipts, "orders": orders}
    execution = {"fills": fills, "cancels": cancels}
    account = {
        "accounts": accounts,
        "positions": positions,
        "reservations": reservations,
        "valuations": valuations,
    }
    return decisions, execution, account


def _fill_record(fill: Any) -> dict:
    return {
        "fill_id": fill.fill_id,
        "order_id": fill.order_id,
        "receipt_id": fill.receipt_id,
        "quote_ref": fill.quote_ref,
        "level_index": fill.level_index,
        "quantity": fill.quantity,
        "price": fill.price,
        "fee_jpy": fill.fee_jpy,
        "filled_at": fill.filled_at,
    }


def run_replay(manifest: Manifest, arm: ReplayArm, scenario: ReplayScenario,
               *, state_dir: str | Path) -> ReplayRun:
    """Run one arm once through the real manager/execution/ledger path."""
    if manifest.header.arm != arm.arm:
        raise ReplayError("manifest arm does not match the replay arm",
                          code="ACCOUNT_SCOPE_MISMATCH", stage="manifest")
    root = Path(state_dir)
    if root.exists():
        if not root.is_dir():
            raise ReplayError("state_dir must be a directory", code="STATE_PATH_FORBIDDEN",
                              stage="replay")
        if any(root.iterdir()):
            raise ReplayError("state_dir must be empty", code="STATE_PATH_FORBIDDEN",
                              stage="replay")
    root.mkdir(parents=True, exist_ok=True)
    clock = _StepClock(_manifest_fence(manifest))
    ledger = NewsLedger.open(
        root, manifest.header.run_id, manifest.header.scenario_id, clock=clock
    )
    actions: list[dict] = []
    order_refs: dict[str, str] = {}
    try:
        ledger.create_account(
            account_id=arm.conditions.account_id,
            run_id=manifest.header.run_id,
            scenario_id=manifest.header.scenario_id,
            initial_cash=format(arm.conditions.initial_capital, "f"),
            quote_currency=arm.conditions.quote_currency,
        )
        manager = PaperManager(
            ledger,
            clock=clock,
            clock_quality=ClockQuality(True, "injected", CONTRACT_MAX_ERROR_MS),
            risk_policy=arm.conditions.risk_policy,
            policy=arm.conditions.manager_policy,
        )
        clock_quality = ClockQuality(True, "injected", CONTRACT_MAX_ERROR_MS)
        warmup_end = arm.conditions.window_start + timedelta(
            seconds=arm.conditions.warmup_seconds
        )
        for step in scenario.ordered():
            if not (arm.conditions.window_start <= step.at <= arm.conditions.window_end):
                raise ReplayError("scenario step outside the replay window",
                                  code="VALUE_OUT_OF_RANGE", stage="scenario")
            if step.at < warmup_end:
                raise ReplayError("scenario step before the warm-up boundary",
                                  code="VALUE_OUT_OF_RANGE", stage="scenario")
            if step.decision is not None:
                if arm.conditions.news_filter and step.news_eligible is False:
                    actions.append({
                        "kind": "decision_skipped",
                        "at": step.at,
                        "receipt_id": step.decision.receipt_id,
                        "reason_code": "NEWS_FILTERED",
                    })
                    continue
                request, action_at = _request_for(arm, manifest, step)
                clock.set(action_at)
                view = manager.submit_candidate(request)
                order_refs[request.receipt_id] = view.order_ref or ""
                if view.order_ref:
                    # N00 v3 §1: sample the durable fence only after the
                    # committed marker SELECT.  A real worker samples it on its
                    # first fill cycle; the replay runner pins it at the
                    # decision time so later observation times stay explicit.
                    ledger.establish_fence(request.receipt_id, clock_quality)
                actions.append({
                    "kind": "decision",
                    "at": action_at,
                    "receipt_id": view.receipt_id,
                    "status": view.status,
                    "reason_codes": view.reason_codes,
                    "order_ref": view.order_ref,
                })
                continue
            clock.set(step.at)
            receipt_id = step.receipt_id
            assert receipt_id is not None
            order_ref = order_refs.get(receipt_id)
            if not order_ref:
                actions.append({
                    "kind": "observation_skipped",
                    "at": step.at,
                    "receipt_id": receipt_id,
                    "reason_code": "NO_ORDER",
                })
                continue
            order = ledger.order_row(order_ref)
            if not (arm.conditions.window_start <= step.at <= arm.conditions.window_end):
                raise ReplayError("observation outside the replay window",
                                  code="VALUE_OUT_OF_RANGE", stage="scenario")
            if any(observation.asset != str(order["asset"])
                   for observation in step.observations):
                raise ReplayError("observation asset does not match the order",
                                  code="ACCOUNT_SCOPE_MISMATCH", stage="scenario")
            constraints = arm.conditions.constraint_for(str(order["asset"]))
            outcome = manager.process_observations(
                order_ref, step.observations, constraints, now=step.at
            )
            actions.append({
                "kind": "observation",
                "at": step.at,
                "receipt_id": receipt_id,
                "order_id": order_ref,
                "status": outcome.status,
                "reason_codes": outcome.reason_codes,
                "terminal": outcome.terminal,
                "fills": tuple(_fill_record(fill) for fill in outcome.fills),
            })
        decisions, execution, account = _ledger_snapshot(ledger)
        actions_payload = tuple(actions)
        decision_digest = _digest({"actions": actions_payload, **decisions})
        fill_digest = _digest(execution)
        account_digest = _digest(account)
        return ReplayRun(
            arm=arm.arm,
            run_id=manifest.header.run_id,
            scenario_id=manifest.header.scenario_id,
            manifest_digest=_digest({
                "header": manifest.header,
                "chunks": [
                    {"seq": c.seq, "sha256": c.sha256, "record_count": c.record_count}
                    for c in manifest.chunks
                ],
            }),
            conditions_digest=_digest(arm.conditions),
            decision_digest=decision_digest,
            fill_digest=fill_digest,
            account_digest=account_digest,
            decision_count=len(decisions["receipts"]),
            fill_count=len(execution["fills"]),
            actions=actions_payload,
            fills=tuple(execution["fills"]),
            account=account,
        )
    finally:
        ledger.close()


def run_replay_twice(manifest: Manifest, arm: ReplayArm,
                     scenario: ReplayScenario) -> ReproCheck:
    """Run the same arm twice in independent stores and compare digests."""
    with tempfile.TemporaryDirectory(prefix="n04b-repro-") as root:
        first = run_replay(manifest, arm, scenario, state_dir=Path(root) / "first")
        second = run_replay(manifest, arm, scenario, state_dir=Path(root) / "second")
    fields = ("decision_digest", "fill_digest", "account_digest")
    mismatches = tuple(
        name for name in fields if getattr(first, name) != getattr(second, name)
    )
    return ReproCheck(
        matches=not mismatches,
        first=first,
        second=second,
        mismatches=mismatches,
    )


def _manifest_for_arm(manifest: Manifest, arm: str) -> Manifest:
    return Manifest(header=replace(manifest.header, arm=arm), chunks=manifest.chunks)


def ab_common_conditions(manifest: Manifest, arm_a: ReplayArm,
                         arm_b: ReplayArm,
                         scenario: ReplayScenario | None = None) -> CommonConditionsEvidence:
    """Prove A/B differ only by the news filter (and B's delay)."""
    if arm_a.arm != "A" or arm_b.arm != "B":
        raise ReplayError("A/B evidence requires one A and one B arm",
                          code="VALUE_OUT_OF_RANGE", stage="conditions")
    if arm_a.conditions.model_conditions() != arm_b.conditions.model_conditions():
        raise ReplayError("A/B conditions differ beyond the news filter",
                          code="ACCOUNT_SCOPE_MISMATCH", stage="conditions")
    if _manifest_signature(_manifest_for_arm(manifest, "A")) != \
            _manifest_signature(_manifest_for_arm(manifest, "B")):
        raise ReplayError("A/B manifests differ beyond the arm label",
                          code="ACCOUNT_SCOPE_MISMATCH", stage="manifest")
    common = arm_a.conditions.model_conditions()
    return CommonConditionsEvidence(
        common_model_digest=_digest(common),
        manifest_common_digest=_manifest_signature(manifest),
        scenario_digest=scenario.digest() if scenario is not None else "",
        manifest_versions=_canonical(manifest.header.versions),
        conditions=common.to_dict(),
        differences={
            "news_filter": [arm_a.conditions.news_filter, arm_b.conditions.news_filter],
            "classification_delay_ms": [
                arm_a.conditions.classification_delay_ms,
                arm_b.conditions.classification_delay_ms,
            ],
        },
    )


def run_n04b(manifest: Manifest, arm_a: ReplayArm, arm_b: ReplayArm,
             scenario: ReplayScenario) -> N04bResult:
    """Run A and B twice each and return digest/common-condition evidence."""
    common = ab_common_conditions(manifest, arm_a, arm_b, scenario)
    manifest_a = _manifest_for_arm(manifest, "A")
    manifest_b = _manifest_for_arm(manifest, "B")
    arm_a_repro = run_replay_twice(manifest_a, arm_a, scenario)
    arm_b_repro = run_replay_twice(manifest_b, arm_b, scenario)
    return N04bResult(
        common=common,
        arm_a=arm_a_repro,
        arm_b=arm_b_repro,
        passed=arm_a_repro.matches and arm_b_repro.matches,
    )


__all__ = [
    "ReplayConditions",
    "ReplayArm",
    "ReplayStep",
    "ReplayScenario",
    "ReplayRun",
    "ReproCheck",
    "CommonConditionsEvidence",
    "N04bResult",
    "run_replay",
    "run_replay_twice",
    "ab_common_conditions",
    "run_n04b",
]

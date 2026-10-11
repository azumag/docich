"""Bounded PAPER experiment assessment, draining and pending activation.

The trading worker owns the cheap periodic check. Candidate generation is a
separate operation: neither an unavailable LLM nor a missing corner can keep
a proven losing experiment opening positions. All callers hold the same short
file lock through observation and their local fill/state writes, never through
network or AI work. No live execution or generated Python is involved.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import math
from pathlib import Path
from typing import Mapping

from .strategy_lab import (
    EXPERIMENT_FILENAME, PENDING_FILENAME, StrategyExperiment, _atomic_json,
    experiment_to_payload, load_pending_experiment, load_strategy_experiment,
    persist_evaluation, save_pending_experiment, save_strategy_experiment,
)
from .strategy_metrics import evaluate_strategy_experiment

CONTROL_FILENAME = "paper_experiment_control.json"
MIN_EXPERIMENT_CLOSED_SELLS = 20
MIN_EXPERIMENT_AGE_S = 48 * 3600
EARLY_STOP_CLOSED_SELLS = 8
EARLY_STOP_PROFIT_FACTOR = Decimal("0.75")
DRAIN_REASONS = frozenset({"early-stop", "sample-complete", "max-age", "initial-experiment"})
CONTROL_STATUSES = frozenset({"active", "draining", "waiting-candidate", "blocked", "legacy"})
CONTROL_REASONS = DRAIN_REASONS | {
    "invalid-active", "evaluation-unavailable", "control-invalid", "control-error",
    "collecting", "pending-activated", "no-experiment",
}


@contextmanager
def experiment_lock(trading_dir):
    """Serialize the worker's local decision/fill phase with candidate writes."""
    target = Path(trading_dir)
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (target / ".paper-experiment.lock").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def experiment_key(spec: StrategyExperiment | None) -> str:
    if spec is None:
        return "legacy"
    payload = json.dumps(experiment_to_payload(spec), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def same_experiment(left: StrategyExperiment, right: StrategyExperiment) -> bool:
    lhs, rhs = experiment_to_payload(left), experiment_to_payload(right)
    lhs.pop("activated_at", None)
    rhs.pop("activated_at", None)
    return lhs == rhs


def usable_pending_experiment(trading_dir, active) -> StrategyExperiment | None:
    """An experiment id cannot identify different rules at one fill boundary."""
    pending = load_pending_experiment(trading_dir)
    if (active is not None and pending is not None
            and active.experiment_id == pending.experiment_id
            and not same_experiment(active, pending)):
        return None
    return pending


def rotation_reason(active, evaluation: Mapping[str, object] | None, *, now: float) -> str | None:
    """Preserve the reviewed sample/age gates, requiring usable observations."""
    if active is None:
        return "initial-experiment"
    if not evaluation or evaluation.get("status") != "ok":
        return None
    if (evaluation.get("experiment_id") != active.experiment_id
            or evaluation.get("activated_at") != active.activated_at):
        return None
    try:
        stamp = float(now)
        age = stamp - float(active.activated_at)
        count = evaluation.get("closed_sells")
        if type(count) is not int or count < 0 or not math.isfinite(age) or age < 0:
            return None
        pnl = Decimal(str(evaluation.get("realized_pnl_jpy")))
        raw_pf = evaluation.get("profit_factor")
        pf = None if raw_pf is None else Decimal(str(raw_pf))
        if not pnl.is_finite() or (pf is not None and (not pf.is_finite() or pf < 0)):
            return None
    except (ValueError, TypeError, InvalidOperation):
        return None
    if count >= EARLY_STOP_CLOSED_SELLS and pnl < 0 and pf is not None and pf < EARLY_STOP_PROFIT_FACTOR:
        return "early-stop"
    if count >= MIN_EXPERIMENT_CLOSED_SELLS:
        return "sample-complete"
    if age >= MIN_EXPERIMENT_AGE_S:
        return "max-age"
    return None


@dataclass(frozen=True)
class ExperimentStep:
    active: StrategyExperiment | None
    evaluation: dict | None
    entries_allowed: bool
    status: str
    reason_code: str
    activated: bool = False


def _read_control(target: Path) -> dict:
    path = target / CONTROL_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return {"invalid": True}
    if not isinstance(data, dict):
        return {"invalid": True}
    key, stamp = data.get("active_key"), data.get("updated_at")
    valid_key = key == "legacy" or (
        isinstance(key, str) and len(key) == 64 and all(ch in "0123456789abcdef" for ch in key)
    )
    if (data.get("schema_version") != 1 or not valid_key
            or data.get("status") not in CONTROL_STATUSES
            or data.get("reason_code") not in CONTROL_REASONS
            or type(data.get("entries_allowed")) is not bool
            or type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp < 0
            or data.get("drain_reason_code") not in DRAIN_REASONS | {None}):
        return {"invalid": True}
    return data


def _publish(target, active, evaluation, *, now, status, reason, entries, pending, activated=False):
    previous = _read_control(target)
    # A temporarily missing active file does not erase the known activation or
    # its stop latch. Keep its identity as evidence, never as executable rules.
    preserve_identity = (active is None and reason == "invalid-active"
                         and previous.get("active_key") not in (None, "legacy"))
    active_key = previous["active_key"] if preserve_identity else experiment_key(active)
    drain_reason = reason if status in {"draining", "waiting-candidate"} else None
    if previous.get("active_key") == active_key:
        prior_drain = previous.get("drain_reason_code")
        if prior_drain is None and previous.get("status") in {"draining", "waiting-candidate"}:
            prior_drain = previous.get("reason_code")
        if drain_reason is None and prior_drain in DRAIN_REASONS:
            drain_reason = prior_drain
    _atomic_json(target / CONTROL_FILENAME, {
        "schema_version": 1,
        "active_key": active_key,
        "experiment_id": (previous.get("experiment_id") if preserve_identity else
                          None if active is None else active.experiment_id),
        "activated_at": (previous.get("activated_at") if preserve_identity else
                         None if active is None else active.activated_at),
        "updated_at": float(now),
        "status": status,
        "reason_code": reason,
        "drain_reason_code": drain_reason,
        "entries_allowed": entries,
        "pending_available": pending is not None,
        "evaluation_status": None if evaluation is None else evaluation.get("status"),
        "open_position_count": None if evaluation is None else evaluation.get("open_position_count"),
    })
    return ExperimentStep(active, evaluation, entries, status, reason, activated)


def assess_experiment(trading_dir, *, capital_jpy: object, now: float) -> ExperimentStep:
    """Evaluate/drain/advance once; the caller must hold ``experiment_lock``.

    A failed strategy remains paused when no next candidate exists. Completed
    non-losing samples can continue gathering evidence until there is a next
    candidate. A latched drain continues exits under the old strategy; changing
    its rules while it still owns inventory is forbidden.
    """
    target = Path(trading_dir)
    if isinstance(now, bool) or not math.isfinite(float(now)) or float(now) < 0:
        raise ValueError("invalid experiment clock")
    active = load_strategy_experiment(target)
    pending = usable_pending_experiment(target, active)
    previous = _read_control(target)
    if (previous.get("invalid") or (previous.get("active_key") == experiment_key(active)
                                     and previous.get("reason_code") == "control-invalid")):
        return _publish(target, active, None, now=now, status="blocked", reason="control-invalid",
                        entries=False, pending=pending)
    if active is None and ((target / EXPERIMENT_FILENAME).exists()
                           or previous.get("active_key") not in (None, "legacy")
                           or previous.get("reason_code") == "invalid-active"):
        return _publish(target, None, None, now=now, status="blocked", reason="invalid-active",
                        entries=False, pending=pending)
    if active is None and pending is None:
        return ExperimentStep(None, None, True, "legacy", "no-experiment")
    if active is not None and pending is not None and same_experiment(active, pending):
        # An already-consumed candidate must not reset its evaluation clock.
        (target / PENDING_FILENAME).unlink(missing_ok=True)
        pending = None
    observed = active if active is not None else replace(pending, activated_at=float(now))
    evaluation = evaluate_strategy_experiment(target, observed, capital_jpy=capital_jpy, now=now)
    if active is not None:
        persist_evaluation(target, evaluation, active)
    if (evaluation.get("status") != "ok"
            or evaluation.get("inventory_complete") is not True):
        return _publish(target, active, evaluation, now=now, status="blocked", reason="evaluation-unavailable",
                        entries=False, pending=pending)
    reason = rotation_reason(active, evaluation, now=now)
    prior_drain = previous.get("drain_reason_code")
    if prior_drain is None and previous.get("status") in {"draining", "waiting-candidate"}:
        prior_drain = previous.get("reason_code")
    draining = previous.get("active_key") == experiment_key(active) and prior_drain is not None
    if draining:
        reason = prior_drain
        if reason not in DRAIN_REASONS:
            return _publish(target, active, evaluation, now=now, status="blocked", reason="control-invalid",
                            entries=False, pending=pending)
    must_drain = draining or reason == "early-stop" or (reason is not None and pending is not None)
    if not must_drain:
        return _publish(target, active, evaluation, now=now, status="active", reason=reason or "collecting",
                        entries=True, pending=pending)
    if evaluation.get("open_position_count") != 0:
        return _publish(target, active, evaluation, now=now, status="draining", reason=reason,
                        entries=False, pending=pending)
    if pending is None:
        return _publish(target, active, evaluation, now=now, status="waiting-candidate", reason=reason,
                        entries=False, pending=None)
    save_strategy_experiment(target, pending, activated_at=float(now))
    (target / PENDING_FILENAME).unlink(missing_ok=True)
    active = load_strategy_experiment(target)
    if active is None:
        raise ValueError("activated experiment is unreadable")
    evaluation = evaluate_strategy_experiment(target, active, capital_jpy=capital_jpy, now=now)
    persist_evaluation(target, evaluation, active)
    if (evaluation.get("status") != "ok"
            or evaluation.get("inventory_complete") is not True):
        return _publish(target, active, evaluation, now=now, status="blocked", reason="evaluation-unavailable",
                        entries=False, pending=None, activated=True)
    return _publish(target, active, evaluation, now=now, status="active", reason="pending-activated",
                    entries=True, pending=None, activated=True)


def stage_candidate(trading_dir, candidate, *, expected_key: str, capital_jpy, now: float) -> dict:
    """Commit a validated candidate against its original baseline, under lock."""
    target = Path(trading_dir)
    active = load_strategy_experiment(target)
    if experiment_key(active) != expected_key:
        return {"status": "skipped", "changed": False, "reason": "baseline-changed"}
    control = _read_control(target)
    if control.get("invalid") or control.get("reason_code") == "control-invalid":
        return {"status": "skipped", "changed": False, "reason": "control-invalid"}
    if active is None and ((target / EXPERIMENT_FILENAME).exists()
                           or control.get("active_key") not in (None, "legacy")
                           or control.get("reason_code") == "invalid-active"):
        return {"status": "skipped", "changed": False, "reason": "invalid-active"}
    if active is not None and same_experiment(active, candidate):
        return {"status": "improved", "changed": False, "activated": False,
                "decision": "unchanged", "reason_code": "no-change"}
    if active is not None and active.experiment_id == candidate.experiment_id:
        return {"status": "rejected", "changed": False, "activated": False,
                "reason_code": "candidate-invalid", "reason": "experiment-id-reused"}
    pending = usable_pending_experiment(target, active)
    if pending is not None and not same_experiment(pending, candidate):
        # Keep the first validated waiting candidate rather than replacing it
        # after every failed/slow evaluation window.
        return {"status": "skipped", "changed": False, "reason": "pending-exists"}
    # A brand-new installation has no account to drain. Saving a PAPER spec
    # here cannot execute an order or create an account.
    if (active is None and not (target / "paper.sqlite3").exists()
            and not (target / CONTROL_FILENAME).exists()):
        save_strategy_experiment(target, candidate, activated_at=now)
        (target / PENDING_FILENAME).unlink(missing_ok=True)
        return {"status": "improved", "kind": "strategy-experiment", "changed": True,
                "activated": True, "activated_from": "generated", "pending_queued": False,
                "experiment": experiment_to_payload(load_strategy_experiment(target))}
    save_pending_experiment(target, candidate, proposed_at=now)
    step = assess_experiment(target, capital_jpy=capital_jpy, now=now)
    return {"status": "improved", "kind": "strategy-experiment", "changed": True,
            "activated": step.activated,
            "activated_from": "generated" if step.activated else None,
            "pending": not step.activated, "pending_queued": not step.activated,
            "experiment": experiment_to_payload(step.active if step.activated else candidate),
            "control_status": step.status, "reason_code": step.reason_code}

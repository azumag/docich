"""Gate for the scheduled (timer-driven, once a day) crypto PAPER improvement.

The end-of-corner trigger in ``paper_corner.py`` is unchanged. This module only
decides, cheaply and without any AI call, whether the independent systemd timer
should enter the existing ``run_paper_improve`` path on this tick. Overlap with
the corner-triggered job stays prevented by the existing singleflight lock and
the cross-corner improve lane inside ``run_paper_improve``.

Gate order (first match wins, every skip makes no AI call):

1. ``disabled``        emergency stop file ``paper_improve_scheduled.disabled``
2. ``already-running`` ``paper_improve_status.json`` is ``running`` and fresh (<70 min)
3. ``pending-exists``  a usable validated pending experiment is already queued
4. ``daily-limit``     an improve attempt started within the last 20 hours
5. otherwise ``run``

The daily limit is judged from ``paper_improve_status.json`` ``started_at``
only (no separate state), so a corner-triggered run counts too and a failure
simply waits for the next day. Statuses that never reach the AI
(``skipped`` such as ``lane-busy`` / ``no-agents``, and ``dry-run``) do not count.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

DISABLED_FILENAME = "paper_improve_scheduled.disabled"
STATUS_FILENAME = "paper_improve_status.json"
RUNNING_FRESH_S = 70 * 60
DAILY_LIMIT_S = 20 * 60 * 60
_NOT_AN_ATTEMPT = frozenset({"skipped", "dry-run"})


@dataclass(frozen=True)
class GateDecision:
    run: bool
    reason: str
    detail: str = ""

    def payload(self) -> dict[str, object]:
        out: dict[str, object] = {"decision": "run" if self.run else "skip", "reason": self.reason}
        if self.detail:
            out["detail"] = self.detail
        return out


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number == number and abs(number) != float("inf") else None


def _default_pending_loader(trading_dir) -> bool:
    from .experiment_control import usable_pending_experiment
    from .strategy_lab import load_strategy_experiment

    return usable_pending_experiment(trading_dir, load_strategy_experiment(trading_dir)) is not None


def evaluate_gate(
    trading_dir,
    *,
    now: float | None = None,
    pending_exists: Callable[[Path], bool] | None = None,
) -> GateDecision:
    """Pure decision: reads local state only, never calls an AI, never writes."""
    target = Path(trading_dir)
    moment = time.time() if now is None else float(now)

    if (target / DISABLED_FILENAME).exists():
        return GateDecision(False, "disabled", f"emergency stop file {DISABLED_FILENAME} exists")

    status = _read_json(target / STATUS_FILENAME)
    updated_at = _finite(status.get("updated_at"))
    age = None if updated_at is None else moment - updated_at

    if status.get("status") == "running" and age is not None and -RUNNING_FRESH_S < age < RUNNING_FRESH_S:
        return GateDecision(False, "already-running", f"running status updated {int(max(age, 0))}s ago")

    try:
        pending = (pending_exists or _default_pending_loader)(target)
    except Exception:
        pending = False  # unreadable state: let the improve path classify it
    if pending:
        return GateDecision(False, "pending-exists", "a validated pending experiment is queued")

    started_at = _finite(status.get("started_at"))
    if (status.get("status") not in _NOT_AN_ATTEMPT and started_at is not None
            and -DAILY_LIMIT_S < moment - started_at < DAILY_LIMIT_S):
        return GateDecision(
            False, "daily-limit",
            f"last attempt ({status.get('status')}) started {int(max(moment - started_at, 0))}s ago, "
            f"limit {DAILY_LIMIT_S}s",
        )

    return GateDecision(True, "run")


def run_scheduled(
    trading_dir,
    *,
    run: Callable[[], Mapping[str, object]],
    dry_run: bool = False,
    now: float | None = None,
    pending_exists: Callable[[Path], bool] | None = None,
) -> dict:
    """Apply the gate, then call ``run`` (the shared improve path) at most once.

    ``dry_run`` bypasses the gate: it calls no AI and does not count as an attempt.
    """
    if dry_run:
        return {**dict(run()), "scheduled": True, "gate": {"decision": "run", "reason": "dry-run"}}
    decision = evaluate_gate(trading_dir, now=now, pending_exists=pending_exists)
    if not decision.run:
        return {"status": "skipped", "reason": decision.reason, "scheduled": True,
                "gate": decision.payload()}
    summary = dict(run())
    summary["scheduled"] = True
    summary["gate"] = decision.payload()
    return summary

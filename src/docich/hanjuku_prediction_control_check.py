"""Read-only confirmation that one fixed prediction control is actionable (#2216).

Run by ``ops/vm_actions/control_hanjuku_prediction.sh check`` *before* the
workflow is allowed to `apply`. It never writes the ledger, never calls Twitch
and never changes the round: it reports the fixed codes the operator needs to
decide, using only the bounded ledger projection.

The output carries no token, remote payload, viewer name or ledger body. Only
the operation name, fixed outcome codes and the bounded chapter/threshold
integers cross the boundary.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

from . import hanjuku_predictions as predictions
from .config import load_global

OPERATIONS = ("cancel", "sync")

EXIT_OK = 0
EXIT_USAGE = 64
EXIT_REFUSED = 65
EXIT_UNREADABLE = 75


def inspect(g, operation, *, now=None):
    """Fixed, read-only classification; never mutates the durable ledger."""
    if operation not in OPERATIONS:
        raise ValueError("unsupported_operation")
    root = Path(g.state_dir)
    now = time.time() if now is None else now
    report = {"operation": operation, "actionable": False, "reason": None,
              "mode": None, "error": None, "status": None, "target": None,
              "middle": None, "next_poll_at": None, "retry_delay_seconds": None}
    try:
        seed = predictions.config(g)[1]
    except Exception:
        report["reason"] = "configuration_unavailable"
        return report
    try:
        state = predictions._load_recoverable(root, seed)
    except Exception:
        # `apply` refuses the same ledger with `ledger_unreadable`, so `check`
        # must not report it as eligible: a target whose ownership cannot be
        # verified is not a recoverable target. check and apply agree.
        report["reason"] = "ledger_unreadable"
        return report

    row = state.get("round") if isinstance(state.get("round"), dict) else None
    report["mode"] = state.get("mode")
    report["error"] = state.get("error")
    report["next_poll_at"] = state.get("next_poll_at")
    if isinstance(report["next_poll_at"], (int, float)):
        report["retry_delay_seconds"] = max(0, min(86400, int(report["next_poll_at"] - now)))
    if row is None:
        report["reason"] = "no_round"
        return report
    report["status"] = row.get("status")
    report["target"] = row.get("target")
    report["middle"] = row.get("middle")
    if row.get("status") in predictions.DONE:
        report["reason"] = "round_already_terminal"
        return report
    if not isinstance(row.get("id"), str) or not row["id"]:
        report["reason"] = "round_not_remote"
        return report
    report.update(actionable=True, reason="reviewed_round_live")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(prog="docich hanjuku-prediction-control-check")
    parser.add_argument("--config", metavar="PATH")
    parser.add_argument("operation", choices=list(OPERATIONS))
    args = parser.parse_args(argv)
    try:
        g = load_global(Path(__file__).resolve().parents[2],
                        Path(args.config) if args.config else None)
    except Exception:
        print("docich: prediction control configuration unavailable", file=sys.stderr)
        return EXIT_UNREADABLE
    report = inspect(g, args.operation)
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return EXIT_OK if report["actionable"] else EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())

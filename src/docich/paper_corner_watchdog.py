"""Independent liveness watchdog for the scheduled PAPER corner.

The normal scheduled runner owns narration and the happy-path restore. This
watchdog runs in a separate systemd timer so a wedged runner cannot keep the
PAPER dashboard on screen indefinitely. The corner has no fixed duration, so an
active run is judged by narration progress: if no segment is delivered within
the stall window, the durable scheduled state proves the run is wedged. It only
intervenes when the canonical program view is still PAPER. Manual PAPER runs are
explicitly excluded.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import time
from pathlib import Path
from typing import Callable

from .adapters.program import PAPER_VIEW_NAME
from .config import ConfigError, load_global
from .paper_corner import PaperCornerError
from .paper_corner_fast import FastPaperCornerManager
from .paper_corner_manual import MANUAL_STATE_FILE
from .paper_corner_restore import restore

WATCHDOG_GRACE_S = 30
STARTING_TIMEOUT_S = 300
# The corner has no fixed duration, so an active run is judged by liveness:
# narration must make progress within this window or the runner is treated as
# wedged and the scheduled view is restored. This is a watchdog staleness
# threshold, not a cap on how long a healthy corner may run.
ACTIVE_STALL_S = 1800


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _manual_corner_active(g) -> bool:
    path = Path(g.state_dir) / MANUAL_STATE_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    return isinstance(data, dict) and data.get("status") in {"starting", "active", "restoring"}


def _deadline_reason(
    state: object,
    *,
    now: float,
    today: str,
    active_game: str | None,
    manual_active: bool,
) -> str | None:
    if active_game != PAPER_VIEW_NAME or manual_active or not isinstance(state, dict):
        return None
    if state.get("date") != today:
        # Do not guess ownership from an old scheduled run. A manual PAPER run
        # has its own state and is excluded above; stale-date anomalies remain
        # visible to the owner-only emergency restore rather than auto-switching.
        return None

    status = state.get("status")
    if status == "active":
        base = (
            _finite_number(state.get("last_progress_at"))
            or _finite_number(state.get("started_at"))
            or _finite_number(state.get("requested_at"))
        )
        if base is not None and now >= base + ACTIVE_STALL_S + WATCHDOG_GRACE_S:
            return "active-stalled"
        return None

    if status == "starting":
        requested_at = _finite_number(state.get("requested_at"))
        if requested_at is not None and now >= requested_at + STARTING_TIMEOUT_S:
            return "starting-timeout"
        return None

    if status in {"failed", "restoring", "completed"}:
        # A terminal/restore state may briefly coexist with PAPER while the
        # normal hand-off is finishing. The 30-second timer cadence itself is
        # the grace period; if PAPER is still current when observed, take over.
        return f"stuck-{status}"

    return None


def check(
    config_path: Path,
    *,
    now_fn: Callable[[], float] = time.time,
    restore_fn: Callable[[Path], dict[str, object]] = restore,
) -> dict[str, object]:
    now = float(now_fn())
    g = load_global(_repo_root(), config_path)
    manager = FastPaperCornerManager(g, clock=lambda: now)
    state = manager._read_state()
    today = dt.datetime.fromtimestamp(now, manager.tz).date().isoformat()
    reason = _deadline_reason(
        state,
        now=now,
        today=today,
        active_game=manager._active_game(),
        manual_active=_manual_corner_active(g),
    )
    if reason is None:
        return {"status": "ok", "action": "none"}

    result = restore_fn(config_path)
    return {
        "status": "restored",
        "action": "restore-scheduled",
        "reason": reason,
        "result": result.get("result") if isinstance(result, dict) else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docich-paper-corner-watchdog")
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = check(args.config)
    except (ConfigError, PaperCornerError, OSError, ValueError) as exc:
        print(f"docich: エラー: {exc}")
        return 2
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

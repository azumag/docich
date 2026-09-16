"""Owner-only recovery for a stuck scheduled PAPER corner.

This module is intentionally narrow: it stops only the scheduled PAPER systemd
oneshot, then asks the existing corner manager to restore the display from its
durable ``previous_game`` state. It never touches the PAPER trading worker,
stream/radio services, credentials, or arbitrary processes.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .adapters.program import PAPER_VIEW_NAME
from .config import ConfigError, load_global
from .paper_corner import PaperCornerError
from .paper_corner_fast import FastPaperCornerManager

SCHEDULED_SERVICE = "docich-paper-corner.service"
STOP_TIMEOUT_S = 15
LOCK_RETRIES = 20
LOCK_RETRY_SLEEP_S = 0.25


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _stop_scheduled_service(*, run=subprocess.run) -> None:
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        result = run(
            ["systemctl", "--user", "stop", SCHEDULED_SERVICE],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=STOP_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PaperCornerError("scheduled PAPER service could not be stopped") from exc
    if result.returncode != 0:
        raise PaperCornerError("scheduled PAPER service stop failed")


def _today(manager: FastPaperCornerManager) -> str:
    return dt.datetime.fromtimestamp(manager.clock(), manager.tz).date().isoformat()


def restore(config_path: Path, *, run=subprocess.run, sleep=time.sleep) -> dict[str, object]:
    """Stop a wedged scheduled runner and restore its recorded previous view."""
    g = load_global(_repo_root(), config_path)
    _stop_scheduled_service(run=run)
    manager = FastPaperCornerManager(g)

    result = "already-running"
    for _ in range(LOCK_RETRIES):
        result = manager.stop()
        if result != "already-running":
            break
        sleep(LOCK_RETRY_SLEEP_S)
    if result == "already-running":
        raise PaperCornerError("scheduled PAPER lock remained busy after service stop")

    current = manager._active_game()
    if current == PAPER_VIEW_NAME and result == "not-active":
        # Fail-safe recovery for a runner that wrote a terminal state before it
        # actually handed the display back. Reuse only today's durable state;
        # never guess a previous game from an older run.
        state = manager._read_state()
        if state.get("date") != _today(manager):
            raise PaperCornerError("stuck PAPER view has no current-day restore state")
        if state.get("previous_game") == PAPER_VIEW_NAME:
            raise PaperCornerError("stuck PAPER view restore target is ambiguous")
        state["status"] = "restoring"
        manager.save(state)
        result = manager.stop()
        current = manager._active_game()

    if result == "already-running" or current == PAPER_VIEW_NAME:
        raise PaperCornerError("scheduled PAPER view is still active after restore")
    if result not in {"completed", "not-active"}:
        raise PaperCornerError("scheduled PAPER restore did not complete")

    return {"status": "restored", "result": result}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docich-paper-corner-restore")
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = restore(args.config)
    except (ConfigError, PaperCornerError, OSError, ValueError) as exc:
        print(f"docich: エラー: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
from .game_switch import atomic_write_json
from .paper_corner import PaperCornerError
from .paper_corner_fast import FastPaperCornerManager
from .paper_corner_manual import MANUAL_STATE_FILE, ManualPaperCornerManager

SCHEDULED_SERVICE = "docich-paper-corner.service"
STOP_TIMEOUT_S = 15
RECOVERY_TIMEOUT_S = 120
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


def _today_corner_state(manager: FastPaperCornerManager) -> tuple[dict, Path] | None:
    """Today's PAPER corner state and its state file, scheduled first.

    Scheduled failures live in ``paper_corner.json``; an operator/manual run
    keeps its own ``paper_corner_manual.json``. Both can strand the canonical
    game-switch the same way, so the bounded recovery must consider either.
    """
    today = _today(manager)
    scheduled = manager._read_state()
    if isinstance(scheduled, dict) and scheduled.get("date") == today:
        return scheduled, Path(manager.path)
    manual_path = Path(manager.g.state_dir) / MANUAL_STATE_FILE
    try:
        manual = json.loads(manual_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    if isinstance(manual, dict) and manual.get("date") == today:
        return manual, manual_path
    return None


def _terminalize_state(state: dict, path: Path, manager: FastPaperCornerManager) -> None:
    """Mark a recovered corner's durable state terminal.

    A stale ``failed`` state makes the next operator/manual start a no-op (its
    recorded previous game is already active), so the recovery closes it out.
    """
    state.update(
        status="completed",
        completed_at=manager.clock(),
        last_error=None,
        end_reason="recovered",
        detail="program-view transition abandoned; previous game restored",
    )
    atomic_write_json(path, state)


def _recover_failed_paper_view(manager: FastPaperCornerManager, *, abandon: bool = False):
    """Reconcile a failed switch before asking the corner manager to restore.

    ``PaperCornerManager._active_game`` intentionally returns ``None`` while
    canonical state is failed. Without this step, the scheduled restore
    cannot see that the failed transition was the PAPER view and calls the
    normal stop path against an unusable canonical state. Only today's
    started corner with a canonical ``previous.game`` of ``paper-view`` is
    eligible; all other states remain fail-closed for the existing recovery
    logic.

    When the synthetic program view itself cannot be restored, ``abandon``
    clears the failed program-view transition and starts the recorded previous
    game directly, so a broken dashboard cannot pin the display black.
    """
    found = _today_corner_state(manager)
    if found is None:
        return None
    state, state_path = found
    if state.get("status") not in {"starting", "active", "restoring", "failed", "completed"}:
        return None
    if not state.get("previous_game") or state.get("previous_game") == PAPER_VIEW_NAME:
        return None

    loaded = manager.store.canonical.load()
    if (
        not isinstance(loaded, tuple)
        or len(loaded) != 2
        or not isinstance(loaded[0], dict)
    ):
        return None
    canonical = loaded[0]
    if canonical.get("phase") != "failed":
        return None
    previous = canonical.get("previous")
    if not isinstance(previous, dict) or previous.get("game") != PAPER_VIEW_NAME:
        return None

    if abandon:
        result = manager.coordinator.recover(
            timeout_s=RECOVERY_TIMEOUT_S, abandon_program_view=True
        )
        if getattr(result, "status", None) != "succeeded":
            detail = getattr(result, "detail", None) or getattr(result, "error_code", None) or "unknown"
            raise PaperCornerError(f"failed program-view transition could not be abandoned: {detail}")
        started = manager.coordinator.start(str(state.get("previous_game")))
        if getattr(started, "status", None) != "succeeded":
            detail = getattr(started, "detail", None) or getattr(started, "error_code", None) or "unknown"
            raise PaperCornerError(f"could not start the recorded previous game: {detail}")
        _terminalize_state(state, state_path, manager)
        return started

    result = manager.coordinator.recover(timeout_s=RECOVERY_TIMEOUT_S)
    if getattr(result, "status", None) not in {"succeeded", "rolled_back"}:
        detail = getattr(result, "detail", None) or getattr(result, "error_code", None) or "unknown"
        raise PaperCornerError(f"canonical PAPER view recovery failed: {detail}")
    if manager._active_game() != PAPER_VIEW_NAME:
        raise PaperCornerError("canonical PAPER view recovery did not restore paper-view")
    return result


def restore(config_path: Path, *, run=subprocess.run, sleep=time.sleep) -> dict[str, object]:
    """Stop a wedged scheduled runner and restore its recorded previous view."""
    g = load_global(_repo_root(), config_path)
    _stop_scheduled_service(run=run)
    manager = FastPaperCornerManager(g)
    try:
        _recover_failed_paper_view(manager)
    except PaperCornerError:
        # The synthetic program view itself cannot be restored (for example a
        # stranded/ownership-mismatched dashboard session). Abandon the failed
        # program-view transition and start the recorded previous game instead
        # of pinning the display black.
        _recover_failed_paper_view(manager, abandon=True)

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
        if not isinstance(state, dict) or state.get("date") != _today(manager):
            # The stranded view is an operator/manual run with its own state.
            # Its recorded previous game is the only safe restore target.
            found = _today_corner_state(manager)
            manual_state = found[0] if found is not None else None
            if manual_state is None or manual_state.get("previous_game") == PAPER_VIEW_NAME:
                raise PaperCornerError("stuck PAPER view has no current-day restore state")
            result = ManualPaperCornerManager(g).stop()
            current = manager._active_game()
        else:
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

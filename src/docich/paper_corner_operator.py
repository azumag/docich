"""Owner-only fixed operations for the bounded PAPER corner/runtime."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .adapters.program import PAPER_VIEW_NAME
from .config import ConfigError, load_global
from .paper_corner import PaperCornerError
from .paper_corner_manual import ManualPaperCornerManager
from .tmux import Tmux

MIN_DURATION = 1
MAX_DURATION = 60
STARTUP_GRACE_SECONDS = 1.0

# Diagnose-only mode deliberately communicates only a fixed category through
# the process exit code. The gateway withholds child stdout/stderr in production,
# so no game-switch detail, path, environment value or log body crosses the VM
# boundary. Keep these stable for the owner-only workflow mapping.
DIAG_EXIT_CODES = {
    "chromium_missing": 41,
    "xvfb_missing": 42,
    "ffplay_missing": 43,
    "xdotool_missing": 44,
    "dashboard_server_timeout": 45,
    "dashboard_window": 46,
    "ownership_mismatch": 47,
    "deadline": 48,
    "other_prepare_failure": 49,
    "not_prepare_failure": 50,
    "state_unreadable": 51,
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docich-paper-corner-operator")
    parser.add_argument("--config", metavar="PATH", required=True)
    parser.add_argument("--duration-minutes", type=int)
    parser.add_argument("--diagnose-only", action="store_true")
    parser.add_argument("--reload-worker", action="store_true")
    return parser


def _classify_prepare_failure(g) -> str:
    """Classify the latest PAPER prepare failure without returning raw detail."""
    try:
        data = json.loads((Path(g.state_dir) / "game_switch.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return "state_unreadable"
    if not isinstance(data, dict):
        return "state_unreadable"
    last = data.get("last_result")
    if not isinstance(last, dict):
        return "not_prepare_failure"
    if last.get("error_code") != "prepare_failed" or last.get("to_game") != PAPER_VIEW_NAME:
        return "not_prepare_failure"
    detail = last.get("detail")
    if not isinstance(detail, str):
        return "other_prepare_failure"
    lowered = detail.lower()
    if "chromium" in lowered and ("見つかりません" in detail or "not found" in lowered):
        return "chromium_missing"
    if "xvfb" in lowered and ("見つかりません" in detail or "not found" in lowered):
        return "xvfb_missing"
    if "ffplay" in lowered and ("見つかりません" in detail or "not found" in lowered):
        return "ffplay_missing"
    if "xdotool" in lowered and ("見つかりません" in detail or "not found" in lowered):
        return "xdotool_missing"
    if "dashboard server" in lowered:
        return "dashboard_server_timeout"
    if "dashboard window" in lowered:
        return "dashboard_window"
    if "ownership" in lowered:
        return "ownership_mismatch"
    if "deadline" in lowered or "timeout" in lowered or "タイムアウト" in detail:
        return "deadline"
    return "other_prepare_failure"


def _diagnose(config_path: Path) -> int:
    g = load_global(_repo_root(), config_path)
    category = _classify_prepare_failure(g)
    return DIAG_EXIT_CODES[category]


def _recover_stale_manual_state(g, duration_minutes: int) -> bool:
    """Recover only a provably stale manual state before a new owner start.

    A previous prepare failure can leave the manual state at ``starting`` even
    though the canonical game-switch transaction rolled back to the recorded
    previous game.  In that exact state it is safe to close the abandoned
    manual session without stopping/restarting anything.  Any ambiguous state
    remains fail-closed.
    """
    manager = ManualPaperCornerManager(g, duration_minutes=duration_minutes)
    state = manager._read_state()
    if state.get("status") not in ("starting", "active"):
        return False
    current = manager._active_game()
    previous = state.get("previous_game")
    if current == PAPER_VIEW_NAME:
        raise PaperCornerError("manual PAPER corner is already active")
    if current != previous:
        raise PaperCornerError("stale manual PAPER state cannot be safely recovered")
    state.update(
        status="completed",
        completed_at=time.time(),
        last_error=None,
        detail="stale manual start recovered before owner retry",
    )
    manager.save(state)
    return True


def reload_worker(config_path: Path, *, tmux: Tmux | None = None, sleep=time.sleep) -> dict[str, object]:
    """Restart only the PAPER trading worker so it imports the reviewed main.

    The worker is paper-only and all balances/positions live in the persistent
    SQLite ledger/state directory, not the process.  No game, stream, radio,
    display, broker credential, or live-order path is touched here.
    """
    g = load_global(_repo_root(), config_path)
    if not getattr(g.trading, "paper_worker_enabled", False):
        raise PaperCornerError("PAPER trading worker is disabled")

    tm = tmux if tmux is not None else Tmux()
    tm.ensure_session()
    existed = tm.has_window("trading")
    if existed:
        tm.kill_window("trading")

    bin_path = str(Path(g.repo_root) / "bin" / "docich")
    tm.new_window(
        "trading",
        [bin_path, "--config", str(g.config_path), "run", "trading"],
    )
    sleep(STARTUP_GRACE_SECONDS)
    if not tm.has_window("trading"):
        raise PaperCornerError("PAPER trading worker did not start")
    try:
        states = tm.pane_states_checked(f"{tm.session}:trading")
    except Exception as exc:
        raise PaperCornerError("PAPER trading worker state cannot be verified") from exc
    if not states or any(getattr(pane, "dead", True) for pane in states):
        raise PaperCornerError("PAPER trading worker exited during startup")
    return {"status": "reloaded" if existed else "started", "worker": "trading"}


def launch(config_path: Path, duration_minutes: int) -> dict[str, object]:
    if type(duration_minutes) is not int or not MIN_DURATION <= duration_minutes <= MAX_DURATION:
        raise PaperCornerError(f"duration_minutes は{MIN_DURATION}-{MAX_DURATION}の整数である必要があります")
    g = load_global(_repo_root(), config_path)
    recovered = _recover_stale_manual_state(g, duration_minutes)
    log_dir = Path(g.state_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(log_dir, 0o700)
    operation_id = f"paper-manual-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    log_path = log_dir / f"{operation_id}.log"
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    argv = [
        sys.executable,
        "-m",
        "docich.paper_corner_manual",
        "--config",
        str(g.config_path),
        "start",
        "--duration-minutes",
        str(duration_minutes),
    ]
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(Path(g.repo_root).parent),
        "LANG": "C.UTF-8",
        "PYTHONPATH": str(Path(g.repo_root) / "src"),
    }
    try:
        with os.fdopen(fd, "ab", closefd=True) as log:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=str(g.repo_root),
                env=env,
                start_new_session=True,
                close_fds=True,
            )
        time.sleep(STARTUP_GRACE_SECONDS)
        if proc.poll() is not None:
            raise PaperCornerError("PAPER manual runner exited during startup")
    except Exception:
        # Preserve the private 0600 log when startup fails.  The owner-only VM
        # gateway intentionally withholds production exec output, so deleting
        # this file would also delete the only evidence needed for diagnosis.
        # The path/content are never emitted to Actions by this operator.
        raise
    return {
        "status": "started",
        "operation_id": operation_id,
        "duration_minutes": duration_minutes,
        "recovered_stale_state": recovered,
        "pid": proc.pid,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config_path = Path(args.config)
        selected = int(bool(args.diagnose_only)) + int(bool(args.reload_worker)) + int(args.duration_minutes is not None)
        if selected != 1:
            raise PaperCornerError("exactly one PAPER operation is required")
        if args.diagnose_only:
            return _diagnose(config_path)
        if args.reload_worker:
            result = reload_worker(config_path)
        else:
            result = launch(config_path, args.duration_minutes)
    except (ConfigError, PaperCornerError, OSError, ValueError) as exc:
        print(f"docich: エラー: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

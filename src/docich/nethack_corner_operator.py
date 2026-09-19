"""Owner-only fixed operations for a bounded manual NetHack corner.

The owner-only VM gateway runs reviewed production commands in the canonical
``/home/ubuntu/docich`` checkout but withholds child stdout/stderr.  This
operator therefore performs exactly one fixed operation per invocation and, for
``--status``, communicates the result category through its process exit code so
the workflow can surface it.

A manual NetHack corner is a long-running oneshot: ``start`` holds the game for
the whole duration.  The operator therefore detaches the reviewed manual runner
and returns after a short startup grace instead of blocking the workflow.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .config import ConfigError, load_global
from .game_switch import atomic_write_json
from .nethack_corner import GAME_NAME, NethackCornerError
from .nethack_corner_manual import ManualNethackCornerManager
from .retro_corner import RetroCornerError

MIN_DURATION = 1
MAX_DURATION = 60
STARTUP_GRACE_SECONDS = 1.0

# Fixed exit-code categories for --status. Keep stable: the workflow maps them
# to a bounded notice and never exposes raw VM output.
STATUS_EXIT_CODES = {
    "idle": 0,
    "terminal": 0,
    "starting": 10,
    "active": 11,
    "failed": 12,
    "unreadable": 13,
}

MANUAL_STATE_FILE = "nethack_corner_manual.json"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docich-nethack-corner-operator")
    parser.add_argument("--config", metavar="PATH", required=True)
    parser.add_argument("--duration-minutes", type=int)
    parser.add_argument("--start", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--recover", action="store_true")
    return parser


def _validate_duration(value: object) -> int:
    if isinstance(value, bool):
        raise NethackCornerError(
            f"duration_minutes は{MIN_DURATION}-{MAX_DURATION}の整数である必要があります"
        )
    try:
        duration = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise NethackCornerError(
            f"duration_minutes は{MIN_DURATION}-{MAX_DURATION}の整数である必要があります"
        )
    if str(duration) != str(value).strip() or not MIN_DURATION <= duration <= MAX_DURATION:
        raise NethackCornerError(
            f"duration_minutes は{MIN_DURATION}-{MAX_DURATION}の整数である必要があります"
        )
    return duration


def _child_env(repo_root: Path) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/home/ubuntu"),
        "LANG": "C.UTF-8",
        "PYTHONPATH": str(repo_root / "src"),
    }


def _manual_argv(config_path: Path, *extra: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "docich.nethack_corner_manual",
        "--config",
        str(config_path),
        *extra,
    ]


def launch(config_path: Path, duration_minutes: object) -> dict[str, object]:
    """Start one bounded manual NetHack corner in a detached session."""
    duration = _validate_duration(duration_minutes)
    g = load_global(_repo_root(), config_path)
    log_dir = Path(g.state_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(log_dir, 0o700)
    operation_id = f"nethack-manual-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    log_path = log_dir / f"{operation_id}.log"
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    argv = _manual_argv(g.config_path, "start", "--duration-minutes", str(duration))
    try:
        with os.fdopen(fd, "ab", closefd=True) as log:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=str(g.repo_root),
                env=_child_env(Path(g.repo_root)),
                start_new_session=True,
                close_fds=True,
            )
        time.sleep(STARTUP_GRACE_SECONDS)
        if proc.poll() is not None:
            # Preserve the private 0600 log as the only startup evidence.
            raise NethackCornerError("NetHack manual runner exited during startup")
    except Exception:
        raise
    return {
        "status": "started",
        "operation_id": operation_id,
        "duration_minutes": duration,
        "pid": proc.pid,
    }


def _run_manual(g, extra: list[str], *, timeout_s: float = 600.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        _manual_argv(g.config_path, *extra),
        capture_output=True,
        text=True,
        cwd=str(g.repo_root),
        env=_child_env(Path(g.repo_root)),
        timeout=timeout_s,
    )


def stop(config_path: Path) -> dict[str, object]:
    """End the active manual NetHack corner and switch back to the old game."""
    g = load_global(_repo_root(), config_path)
    proc = _run_manual(g, ["stop"])
    if proc.returncode != 0:
        # The gateway withholds child stdout/stderr. Map the runner's fixed
        # failure modes to a bounded category so the owner can diagnose a stuck
        # stop without exposing private state, then raise.
        _record_stop_failure(g, proc.returncode, proc.stderr or "")
        raise NethackCornerError("NetHack manual runner stop failed")
    return {"status": "stopped"}


def _record_stop_failure(g, returncode, stderr: str) -> None:
    """Persist a bounded stop-failure category under the manual state dir."""
    lowered = stderr.lower()
    if "docich up が失敗" in stderr or "ディスプレイ" in stderr:
        category = "prepare_runtime_failed"
    elif "game switch" in lowered or "switch" in lowered or "recovery" in lowered:
        category = "switch_back_failed"
    elif "save" in lowered and "確認" in stderr:
        category = "save_boundary_failed"
    else:
        category = "unknown"
    try:
        path = Path(g.state_dir) / "nethack_corner_manual_stop_failure.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            path,
            {"schema_version": 1, "returncode": int(returncode), "category": category},
        )
    except Exception:
        return


def recover(config_path: Path) -> dict[str, object]:
    """Restore the game a failed manual corner could not switch back to.

    A corner can fail after NetHack became the canonical active game (for
    example a round-boundary timeout).  The manual runner's ``stop`` only
    finishes an ``active`` corner, so it no-ops on a ``failed`` one and leaves
    the active game stranded.  This bounded operation switches back only to the
    previous game recorded in the manual state; it cannot choose an arbitrary
    target.
    """
    g = load_global(_repo_root(), config_path)
    manager = ManualNethackCornerManager(g, duration_minutes=MIN_DURATION)
    # Keep the state check, active-game check, and switch in the same manual
    # corner critical section.  Otherwise a new start can move the state from
    # failed -> starting/active after the check and recover would switch the
    # canonical game out from underneath that live corner.
    with manager._locked():
        state = manager._read_state()
        previous = state.get("previous_game")
        current = manager._active_game_reader()
        if current != GAME_NAME:
            return {"status": "noop", "detail": "nethack is not active", "active_game": current}
        if state.get("status") != "failed":
            raise NethackCornerError("recover は failed manual corner にのみ使用できます")
        if not isinstance(previous, str) or not previous or previous == GAME_NAME:
            raise NethackCornerError("restore target gameをmanual stateから特定できません")
        manager._transition_to(current, previous)
    return {"status": "recovered", "from_game": current, "to_game": previous}


def status_category(state_dir: Path) -> str:
    path = state_dir / MANUAL_STATE_FILE
    if not path.exists():
        return "idle"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "unreadable"
    if not isinstance(data, dict):
        return "unreadable"
    value = data.get("status")
    if value in ("starting", "active", "failed"):
        return str(value)
    if value in ("completed", "interrupted"):
        return "terminal"
    return "unreadable"


def status(config_path: Path) -> int:
    """Return a fixed exit-code category for the manual corner state."""
    g = load_global(_repo_root(), config_path)
    return STATUS_EXIT_CODES[status_category(Path(g.state_dir))]


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config_path = Path(args.config)
        selected = (
            int(bool(args.start))
            + int(bool(args.stop))
            + int(bool(args.status))
            + int(bool(args.recover))
        )
        if selected != 1:
            raise NethackCornerError("exactly one NetHack corner operation is required")
        if args.status:
            return status(config_path)
        if args.recover:
            result = recover(config_path)
        elif args.stop:
            result = stop(config_path)
        else:
            result = launch(config_path, args.duration_minutes)
    except (ConfigError, NethackCornerError, RetroCornerError, OSError, ValueError) as exc:
        print(f"docich: エラー: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

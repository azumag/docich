#!/usr/bin/env python3
"""Replace only the reviewed long-lived PAPER trading worker after deployment.

This helper is intentionally narrow: it verifies the existing tmux window and
process identity, waits until the worker is between cycles, replaces only the
`trading` window, then requires a new PID and a fresh PAPER heartbeat.  It does
not touch Soren, games, live trading, secrets, or any other tmux window.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import shlex
import subprocess
import sys
import time

SESSION = "docich"
WINDOW = "trading"
TARGET = f"{SESSION}:{WINDOW}"
MAX_STATUS_BYTES = 128 * 1024


class ActivationError(RuntimeError):
    pass


def _checked_run(argv: list[str], *, runner=subprocess.run):
    result = runner(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        raise ActivationError("tmux_operation_failed")
    return result


def _pane_pid(*, runner=subprocess.run) -> int:
    result = _checked_run(
        ["tmux", "list-panes", "-t", TARGET, "-F", "#{pane_dead}\t#{pane_pid}"],
        runner=runner,
    )
    rows = [row for row in result.stdout.splitlines() if row.strip()]
    if len(rows) != 1:
        raise ActivationError("paper_worker_identity_unavailable")
    fields = rows[0].split("\t")
    if len(fields) != 2 or fields[0] != "0":
        raise ActivationError("paper_worker_not_live")
    try:
        pid = int(fields[1])
    except ValueError as exc:
        raise ActivationError("paper_worker_identity_unavailable") from exc
    if pid <= 1:
        raise ActivationError("paper_worker_identity_unavailable")
    return pid


def _verify_process(pid: int, *, root: Path, config_path: Path, proc_root: Path = Path("/proc")) -> None:
    try:
        raw = (proc_root / str(pid) / "cmdline").read_bytes()
        argv = [item.decode("utf-8", "strict") for item in raw.split(b"\0") if item]
    except (OSError, UnicodeError) as exc:
        raise ActivationError("paper_worker_identity_unavailable") from exc
    docich = str((root / "bin" / "docich").resolve())
    config = str(config_path.resolve())
    joined = " ".join(argv)
    if docich not in joined or config not in joined or "run trading" not in joined:
        raise ActivationError("paper_worker_identity_mismatch")


def _read_status(path: Path) -> tuple[str, float]:
    try:
        stat = path.stat()
        if not path.is_file() or stat.st_size <= 0 or stat.st_size > MAX_STATUS_BYTES:
            raise ActivationError("paper_worker_status_invalid")
        data = json.loads(path.read_text(encoding="utf-8"))
    except ActivationError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise ActivationError("paper_worker_status_invalid") from exc
    state = data.get("worker_state") if isinstance(data, dict) else None
    heartbeat = data.get("heartbeat_at") if isinstance(data, dict) else None
    if not isinstance(state, str) or isinstance(heartbeat, bool) or not isinstance(heartbeat, (int, float)):
        raise ActivationError("paper_worker_status_invalid")
    heartbeat = float(heartbeat)
    if not math.isfinite(heartbeat):
        raise ActivationError("paper_worker_status_invalid")
    return state, heartbeat


def activate(
    root: Path = Path("/home/ubuntu/docich"),
    *,
    proc_root: Path = Path("/proc"),
    runner=subprocess.run,
    sleep_fn=time.sleep,
    time_fn=time.time,
    monotonic_fn=time.monotonic,
) -> None:
    root = Path(root).resolve()
    config_path = root / "config" / "docich.soren-live.toml"
    if not config_path.is_file():
        raise ActivationError("production_config_missing")

    # Load only reviewed docich config/state paths from the deployed checkout.
    sys.path.insert(0, str(root / "src"))
    try:
        from docich.config import load_global
        g = load_global(root, config_path=config_path)
    except Exception as exc:
        raise ActivationError("production_config_invalid") from exc
    if g.trading.paper_worker_enabled is not True:
        raise ActivationError("paper_worker_disabled")
    interval = int(g.trading.interval_s)
    status_path = Path(g.state_dir) / "trading" / "status.json"

    old_pid = _pane_pid(runner=runner)
    _verify_process(old_pid, root=root, config_path=config_path, proc_root=proc_root)

    # Never kill an in-progress cycle. Wait for a bounded idle/degraded point.
    deadline = float(monotonic_fn()) + max(90.0, float(interval) * 2.0)
    while True:
        state, old_heartbeat = _read_status(status_path)
        age = float(time_fn()) - old_heartbeat
        if age < -60 or age > max(180.0, float(interval) * 3.0):
            raise ActivationError("paper_worker_status_stale")
        if state in {"paper_worker_idle", "paper_worker_degraded"}:
            break
        if state != "paper_worker_running" or float(monotonic_fn()) >= deadline:
            raise ActivationError("paper_worker_busy")
        sleep_fn(1.0)

    _checked_run(["tmux", "kill-window", "-t", TARGET], runner=runner)
    command = [str(root / "bin" / "docich"), "--config", str(config_path), "run", "trading"]
    _checked_run(
        ["tmux", "new-window", "-d", "-t", SESSION, "-n", WINDOW, shlex.join(command)],
        runner=runner,
    )

    deadline = float(monotonic_fn()) + max(90.0, float(interval) * 2.0)
    while float(monotonic_fn()) < deadline:
        try:
            new_pid = _pane_pid(runner=runner)
            if new_pid == old_pid:
                raise ActivationError("paper_worker_pid_not_replaced")
            _verify_process(new_pid, root=root, config_path=config_path, proc_root=proc_root)
            state, heartbeat = _read_status(status_path)
            if heartbeat > old_heartbeat and state in {
                "paper_worker_running", "paper_worker_idle", "paper_worker_degraded"
            }:
                return
        except ActivationError:
            pass
        sleep_fn(0.5)
    raise ActivationError("paper_worker_replacement_timeout")


def main() -> int:
    try:
        activate()
        return 0
    except ActivationError:
        return 1
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Interrupt a stuck Soren match runner via the reviewed VM gateway.

The runner's interrupted exit can cause its owning Soren loop to exit; the
existing supervisor then owns game-only respawn. This helper never signals
the display, stream, common workers, or edits tracked files.
"""

import json
import os
from pathlib import Path
import signal
import time


SOREN = Path("/home/ubuntu/soren")
DOCICH = Path("/home/ubuntu/docich")


def _json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _birth_time(proc, pid):
    # /proc/<pid>/stat field 22, counting from the final ')' to tolerate a
    # parenthesized comm. Compare against the marker written just after fork.
    stat = (proc / str(pid) / "stat").read_text(encoding="ascii")
    ticks = int(stat.rsplit(")", 1)[1].split()[19])
    boot = next(int(line.split()[1]) for line in (proc / "stat").read_text().splitlines()
                if line.startswith("btime "))
    return boot + ticks / os.sysconf("SC_CLK_TCK")


def eligible(soren=SOREN, docich=DOCICH, proc=Path("/proc"), now=None):
    """Return the exact runner PID and birth time, or refuse without mutation."""
    now = time.time() if now is None else now
    switch = _json(docich / "run-soren-live/game_switch.json")
    if not switch or (switch.get("active") or {}).get("game") != "sorengame":
        raise ValueError("sorengame is not the active game")
    phase = switch.get("phase")
    if phase == "draining":
        ack = _json(soren / "tmp/state/game_lifecycle/ack.json")
        if (not ack or ack.get("status") != "cancelled"
                or not switch.get("request_id")
                or ack.get("request_id") != switch.get("request_id")):
            raise ValueError("active lifecycle drain must be cancelled")
    elif phase != "ready":
        raise ValueError("game switch is not ready")
    path = soren / "game_state.json"
    state = _json(path)
    if (not state or state.get("state") != "STOP"
            or type(state.get("makeSorenCount")) not in (int, float)
            or state.get("makeSorenCount") != 0):
        raise ValueError("game is not a non-founding STOP")
    if (soren / "tmp/markers/.soviet_created").exists():
        raise ValueError("founding marker is present")
    if not 60 <= now - path.stat().st_mtime <= 86400:
        raise ValueError("STOP is not safely stale")
    if (soren / "tmp/state/soren_loop.paused").exists():
        raise ValueError("Soren loop is paused")
    marker = _json(soren / "tmp/state/main_strategy_runner_active.json")
    if not marker:
        raise ValueError("runner marker is missing")
    pid, started_at = marker.get("pid"), marker.get("started_at")
    if type(pid) is not int or not 1 < pid <= 4194304 or type(started_at) is not int:
        raise ValueError("invalid runner marker")
    if not 0 <= now - started_at <= 86400:
        raise ValueError("runner marker is stale or in the future")
    proc_dir = proc / str(pid)
    argv = (proc_dir / "cmdline").read_bytes().split(b"\0")
    argv = [arg for arg in argv if arg]
    if not (len(argv) == 3 and Path(os.fsdecode(argv[0])).name == "python3"
            and argv[1] == b"-u" and argv[2] == b"strategy_runner.py"):
        raise ValueError("PID is not the match runner")
    if (proc_dir / "cwd").resolve(strict=True) != soren.resolve(strict=True):
        raise ValueError("runner is not in the production Soren root")
    birth = _birth_time(proc, pid)
    if not 0 <= started_at - birth <= 10:
        raise ValueError("runner marker does not match process birth")
    return pid, birth


def main():
    pid, birth = eligible()
    # A pidfd pins this exact process across the final identity check and
    # signal, including if a PID is recycled at the same clock tick.
    with os.fdopen(os.pidfd_open(pid), "rb") as pidfd:
        if abs(_birth_time(Path("/proc"), pid) - birth) > 0.01:
            raise ValueError("runner PID changed")
        signal.pidfd_send_signal(pidfd.fileno(), signal.SIGTERM)
    for _ in range(40):
        time.sleep(0.25)
        if not (Path("/proc") / str(pid)).exists():
            print("match runner exited; supervisor owns game continuation")
            return 0
        try:
            if abs(_birth_time(Path("/proc"), pid) - birth) > 0.01:
                print("match runner replaced; supervisor owns game continuation")
                return 0
        except (OSError, ValueError, StopIteration):
            return 0
    raise RuntimeError("runner did not exit within 10 seconds")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, StopIteration) as exc:
        raise SystemExit(f"guarded Soren runner recovery refused: {exc}") from exc

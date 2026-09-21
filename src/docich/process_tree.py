"""Small, dependency-free process-tree termination helpers.

tmux owns the pane leader, not the Python process that asks tmux to stop a
session. Calling ``kill-session`` therefore does not guarantee that a game
wrapper's descendants have exited. This module snapshots the descendant tree,
stops leaf processes first, and only then stops the pane leader.

The helper deliberately does not call ``waitpid``: most of the processes it
stops are not children of the caller. A zombie is considered settled for the
bounded stop; the process's actual parent remains responsible for reaping it.
The tracked shell wrappers also wait for their own children, which closes that
final ownership gap.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    ppid: int
    state: str


@dataclass(frozen=True)
class TerminationResult:
    roots: tuple[int, ...]
    term_sent: tuple[int, ...]
    kill_sent: tuple[int, ...]
    remaining: tuple[int, ...]


def _linux_process_table() -> dict[int, ProcessInfo]:
    table: dict[int, ProcessInfo] = {}
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return table
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "stat").read_text(encoding="utf-8")
            closing = raw.rfind(")")
            if closing < 0:
                continue
            fields = raw[closing + 2 :].split()
            # fields[0] is state, fields[1] is ppid.
            if len(fields) < 2:
                continue
            pid = int(entry.name)
            ppid = int(fields[1])
        except (OSError, ValueError):
            continue
        table[pid] = ProcessInfo(pid=pid, ppid=ppid, state=fields[0])
    return table


def _ps_process_table() -> dict[int, ProcessInfo]:
    try:
        result = subprocess.run(
            ["ps", "-Ao", "pid=,ppid=,stat="],
            capture_output=True,
            text=True,
            check=False,
            timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    table: dict[int, ProcessInfo] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        table[pid] = ProcessInfo(pid=pid, ppid=ppid, state=parts[2])
    return table


def process_table() -> dict[int, ProcessInfo]:
    """Return a best-effort PID/PPID/state snapshot for the current host."""

    table = _linux_process_table()
    return table if table else _ps_process_table()


def descendant_pids(root_pids: list[int] | tuple[int, ...]) -> list[int]:
    """Return descendants in parent-before-child order for leaf-first stops."""

    roots = tuple(dict.fromkeys(pid for pid in root_pids if isinstance(pid, int) and pid > 0))
    table = process_table()
    children: dict[int, list[int]] = {}
    for info in table.values():
        children.setdefault(info.ppid, []).append(info.pid)

    seen = set(roots)
    queue = list(roots)
    result: list[int] = []
    while queue:
        parent = queue.pop(0)
        for child in children.get(parent, []):
            if child in seen:
                continue
            seen.add(child)
            result.append(child)
            queue.append(child)
    return result


def _is_running(pid: int) -> bool:
    """Return false for missing and zombie processes."""

    if pid <= 0:
        return False
    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.exists():
        try:
            raw = proc_stat.read_text(encoding="utf-8")
            closing = raw.rfind(")")
            if closing >= 0:
                state = raw[closing + 2 :].split()[0]
                return state != "Z"
        except (OSError, IndexError):
            pass
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "stat="],
            capture_output=True,
            text=True,
            check=False,
            timeout=1,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    state = result.stdout.strip()
    return bool(state) and not state.startswith("Z")


def _send(pid: int, sig: signal.Signals) -> bool:
    try:
        os.kill(pid, sig)
    except (OSError, ProcessLookupError):
        return False
    return True


def _wait_until_settled(pids: list[int], timeout_s: float) -> list[int]:
    deadline = time.monotonic() + max(0.0, timeout_s)
    while True:
        remaining = [pid for pid in pids if _is_running(pid)]
        if not remaining or time.monotonic() >= deadline:
            return remaining
        time.sleep(0.05)


def terminate_process_tree(
    root_pids: list[int] | tuple[int, ...],
    *,
    term_timeout_s: float = 1.5,
    kill_timeout_s: float = 1.0,
) -> TerminationResult:
    """Stop roots and descendants, with a bounded TERM -> KILL fallback.

    Descendants are signalled before their root so a shell wrapper cannot exit
    first and orphan a still-running game. The PID list is snapshotted for
    each phase; PIDs that disappeared before the snapshot are never signalled.
    """

    roots = tuple(dict.fromkeys(pid for pid in root_pids if isinstance(pid, int) and pid > 0))
    table = process_table()
    known_roots = tuple(pid for pid in roots if pid in table)
    descendants = descendant_pids(list(known_roots))
    targets = list(dict.fromkeys(known_roots + tuple(descendants)))
    term_sent: list[int] = []
    kill_sent: list[int] = []

    for pid in reversed(descendants):
        if _send(pid, signal.SIGTERM):
            term_sent.append(pid)
    for pid in known_roots:
        if _send(pid, signal.SIGTERM):
            term_sent.append(pid)

    remaining = _wait_until_settled(targets, term_timeout_s)
    if remaining:
        current_descendants = descendant_pids(remaining)
        kill_order = list(reversed(current_descendants)) + list(remaining)
        for pid in dict.fromkeys(kill_order):
            if _send(pid, signal.SIGKILL):
                kill_sent.append(pid)
        remaining = _wait_until_settled(list(dict.fromkeys(kill_order)), kill_timeout_s)

    return TerminationResult(
        roots=roots,
        term_sent=tuple(dict.fromkeys(term_sent)),
        kill_sent=tuple(dict.fromkeys(kill_sent)),
        remaining=tuple(dict.fromkeys(remaining)),
    )

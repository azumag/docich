"""Cleanup primitives shared by the headless evaluation runners.

Evaluation sessions live on the private ``docich-eval`` tmux server (see
``docich.tmux.eval_server_name``) so a crashed or transient-unit-owned eval
server can never take production panes down with it (Issue #1280).

``kill-session`` alone does not prove the pane leader and its descendants
exited: a game that ignores SIGHUP/SIGTERM keeps burning CPU after its session
vanished (Issues #1280 and #1105 measured orphaned game processes at ~50-90%
CPU).  :func:`kill_session` snapshots the pane PIDs before the kill, then stops
their process trees with a bounded TERM -> KILL fallback.  The kill target is
an ``=`` exact-name match so a session that already died can never
prefix-match a parallel match's session.

A tmux **server** crash cannot run that finally block.  Every eval process
therefore records its pane leaders in a small durable ledger (owner PID +
Linux start ticks).  The next evaluation sweeps owners that are gone and stops
their surviving panes, but only when the recorded start ticks match and the
process still lives in a tmux cgroup scope, so a recycled PID is never
signalled.
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from pathlib import Path

from .process_tree import (
    is_running,
    process_cgroup,
    process_start_ticks,
    terminate_process_tree,
)

LEDGER_ENV = "DOCICH_EVAL_PANE_LEDGER"
_TMUX_CGROUP_MARKER = "tmux-spawn-"


def ledger_path() -> Path:
    override = os.environ.get(LEDGER_ENV, "").strip()
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / f"docich-eval-panes-{os.getuid()}.json"


def pane_pids(tmux_call, session: str) -> list[int]:
    """Pane leader PIDs of one eval session (empty when it is already gone)."""

    listed = tmux_call(["list-panes", "-t", f"={session}", "-F", "#{pane_pid}"])
    if getattr(listed, "returncode", 1) != 0:
        return []
    pids: list[int] = []
    for raw in (getattr(listed, "stdout", "") or "").splitlines():
        try:
            pid = int(raw.strip())
        except ValueError:
            continue
        if pid > 0:
            pids.append(pid)
    return list(dict.fromkeys(pids))


def kill_session(tmux_call, session: str) -> tuple[int, ...]:
    """Kill exactly ``session`` and stop any surviving pane process trees.

    Returns the PIDs that could not be stopped (empty on full cleanup).  Only
    settled panes leave the crash ledger, so a failed stop is retried by the
    next evaluation.
    """

    pids = pane_pids(tmux_call, session)
    tmux_call(["kill-session", "-t", f"={session}"])
    if not pids:
        return ()
    remaining = terminate_process_tree(pids).remaining
    for pid in pids:
        if pid not in remaining:
            release_pane(pid)
    return remaining


def _owner_key() -> str:
    pid = os.getpid()
    return f"{pid}:{process_start_ticks(pid) or 0}"


def _parse_owner(key: str) -> tuple[int, int] | None:
    pid, _, start = str(key).partition(":")
    try:
        return int(pid), int(start)
    except ValueError:
        return None


def _parse_pane(entry) -> tuple[int, int] | None:
    if not isinstance(entry, (list, tuple)) or len(entry) != 2:
        return None
    try:
        pid, start = int(entry[0]), int(entry[1])
    except (TypeError, ValueError):
        return None
    if pid <= 0 or start <= 0:
        return None
    return pid, start


def _reap_owner(panes) -> bool:
    """Stop one gone owner's recorded panes; True when everything is gone."""

    settled = True
    for entry in panes if isinstance(panes, list) else []:
        pane = _parse_pane(entry)
        if pane is None:
            continue
        pid, start = pane
        if not is_running(pid) or process_start_ticks(pid) != start:
            continue
        cgroup = process_cgroup(pid) or ""
        if _TMUX_CGROUP_MARKER not in cgroup:
            # Not a tmux pane process anymore (or a platform without the
            # systemd scope integration): never signal it.
            continue
        if terminate_process_tree([pid]).remaining:
            settled = False
    return settled


def _update_ledger(mutate) -> None:
    """Mutate the ledger under flock; bookkeeping never breaks an evaluation."""

    path = ledger_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.seek(0)
                raw = handle.read()
                try:
                    data = json.loads(raw) if raw.strip() else {}
                except ValueError:
                    data = {}
                owners = data.get("owners")
                if not isinstance(owners, dict):
                    owners = {}
                mutate(owners)
                handle.seek(0)
                handle.truncate()
                json.dump({"owners": owners}, handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        return


def register() -> None:
    """Register this process and sweep panes left by crashed eval processes."""

    key = _owner_key()

    def mutate(owners):
        for owner_key, panes in list(owners.items()):
            parsed = _parse_owner(owner_key)
            if parsed is None:
                owners.pop(owner_key, None)
                continue
            pid, start = parsed
            if start > 0 and is_running(pid) and process_start_ticks(pid) == start:
                continue  # owner still alive: its panes are not orphans
            if _reap_owner(panes):
                owners.pop(owner_key, None)
        owners.setdefault(key, [])

    _update_ledger(mutate)


def record_pane(pid: int) -> None:
    entry = [int(pid), process_start_ticks(int(pid)) or 0]
    if entry[1] <= 0:
        return

    def mutate(owners):
        panes = owners.get(_owner_key())
        if not isinstance(panes, list):
            panes = []
            owners[_owner_key()] = panes
        if entry not in panes:
            panes.append(entry)

    _update_ledger(mutate)


def release_pane(pid: int) -> None:
    def mutate(owners):
        panes = owners.get(_owner_key())
        if not isinstance(panes, list):
            return
        owners[_owner_key()] = [
            entry for entry in panes
            if (_parse_pane(entry) or (0, 0))[0] != int(pid)
        ]

    _update_ledger(mutate)


def release() -> None:
    """Drop this process's ledger entry once it has no recorded panes left.

    A parallel evaluation (nInvaders arena threads) shares one owner key, so a
    finished thread must not erase panes that another thread still owns.
    """

    def mutate(owners):
        key = _owner_key()
        if owners.get(key):
            return
        owners.pop(key, None)

    _update_ledger(mutate)

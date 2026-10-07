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

Ancestry alone is not enough to prove ownership: a process whose parent (the
tmux pane leader) already exited is reparented, so it disappears from the
descendant tree while still burning CPU (Issue #1105).  The introspection
helpers at the bottom of this module additionally answer "which live
processes carry these environment tags" and "which live processes are still
in one of these tmux pane process groups/cgroup scopes", so a teardown can
reclaim exactly the processes it owns and nothing else.
"""
from __future__ import annotations

import os
import select
import signal
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import ExitStack
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


class ProcessIdentityError(RuntimeError):
    """A stable process handle cannot be acquired, inspected or signalled."""


def _pidfd_is_running(fd: int) -> bool:
    poller = select.poll()
    poller.register(fd, select.POLLIN)
    events = poller.poll(0)
    if any(event & select.POLLNVAL for _fd, event in events):
        raise ProcessIdentityError("invalid pidfd")
    # pidfds become readable on exit, including for an unreaped zombie.
    return not events


def terminate_owned_processes(
    pids: Sequence[int],
    owns_process: Callable[[int], bool],
    *,
    term_timeout_s: float = 1.5,
    kill_timeout_s: float = 1.0,
) -> TerminationResult:
    """Revalidate ownership under pidfds and retain them through TERM/KILL.

    Discovery PIDs are only candidates. Open each handle *before* reading
    ownership again, then check that the handle did not exit during that
    read. Thus /proc reads cannot authorize signalling a reused PID. After
    validation, exit checks and signals use only the retained handle.

    Stop exactly the owned candidates, without expanding a later descendant
    snapshot. Unsupported/denied pidfd operations fail closed: checking start
    ticks and then using os.kill would still race with PID reuse.
    """

    roots = tuple(dict.fromkeys(pid for pid in pids if type(pid) is int and pid > 1))
    if not roots:
        return TerminationResult(roots, (), (), ())
    opener = getattr(os, "pidfd_open", None)
    sender = getattr(signal, "pidfd_send_signal", None)
    if not callable(opener) or not callable(sender):
        raise ProcessIdentityError("orphan teardown requires pidfd support")

    handles: dict[int, int] = {}
    term_sent: list[int] = []
    kill_sent: list[int] = []

    def send(pid: int, sig: signal.Signals) -> bool:
        try:
            sender(handles[pid], sig)
        except ProcessLookupError:
            return False
        return True

    def wait(pending: Sequence[int], timeout_s: float) -> list[int]:
        deadline = time.monotonic() + max(0.0, timeout_s)
        while True:
            remaining = [pid for pid in pending if _pidfd_is_running(handles[pid])]
            if not remaining or time.monotonic() >= deadline:
                return remaining
            time.sleep(0.05)

    with ExitStack() as stack:
        try:
            for pid in roots:
                try:
                    fd = opener(pid)
                except ProcessLookupError:
                    continue
                stack.callback(os.close, fd)
                if (
                    _pidfd_is_running(fd)
                    and owns_process(pid)
                    and _pidfd_is_running(fd)
                ):
                    handles[pid] = fd

            # Acquire and validate every handle before sending any signal.
            for pid in handles:
                if send(pid, signal.SIGTERM):
                    term_sent.append(pid)
            remaining = wait(tuple(handles), term_timeout_s)
            for pid in remaining:
                if send(pid, signal.SIGKILL):
                    kill_sent.append(pid)
            remaining = wait(remaining, kill_timeout_s)
        except OSError as exc:
            raise ProcessIdentityError("orphan teardown pidfd operation failed") from exc

    return TerminationResult(roots, tuple(term_sent), tuple(kill_sent), tuple(remaining))


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


def _stat_after_comm(pid: int) -> list[str] | None:
    """``/proc/<pid>/stat`` fields after the (possibly nested) ``comm`` field."""

    if type(pid) is not int or pid <= 0:
        return None
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    closing = raw.rfind(")")
    if closing < 0:
        return None
    return raw[closing + 2 :].split()


def process_start_ticks(pid: int) -> int | None:
    """Linux ``/proc/<pid>/stat`` start time (field 22) or ``None``.

    Recorded with an evaluation pane PID so a later sweep can refuse to touch
    a recycled PID that now belongs to a different process.
    """

    fields = _stat_after_comm(pid)
    if not fields or len(fields) < 20:
        return None
    try:
        return int(fields[19])
    except ValueError:
        return None


def process_pgid(pid: int) -> int | None:
    """Linux ``/proc/<pid>/stat`` process group id (field 5) or ``None``.

    The pane leaders created by tmux are process group leaders, so a process
    that lost its parent (the pane leader died and tmux reparented its child)
    is still identifiable by the group it stayed in.
    """

    fields = _stat_after_comm(pid)
    if not fields or len(fields) < 3:
        return None
    try:
        pgid = int(fields[2])
    except ValueError:
        return None
    return pgid if pgid > 0 else None


def process_environ(pid: int) -> dict[str, str]:
    """Environment of a live process, best effort (``{}`` when unreadable).

    Only the owning user's processes are readable on Linux; every other
    failure (missing ``/proc``, race with exit, permission) degrades to "no
    information" so a caller never treats an unreadable process as owned.
    """

    if type(pid) is not int or pid <= 0:
        return {}
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return {}
    env: dict[str, str] = {}
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        key, separator, value = entry.partition(b"=")
        if not separator:
            continue
        env[key.decode("utf-8", errors="replace")] = value.decode(
            "utf-8", errors="replace"
        )
    return env


def process_ids() -> list[int]:
    """Every visible PID, from ``/proc`` when available, else ``ps``."""

    proc_root = Path("/proc")
    if proc_root.is_dir():
        try:
            pids = sorted(
                int(entry.name) for entry in proc_root.iterdir() if entry.name.isdigit()
            )
        except OSError:
            pids = []
        if pids:
            return pids
    try:
        result = subprocess.run(
            ["ps", "-Ao", "pid="],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    pids = []
    for line in result.stdout.splitlines():
        try:
            pid = int(line.strip())
        except ValueError:
            continue
        if pid > 0:
            pids.append(pid)
    return sorted(dict.fromkeys(pids))


def process_pgid_map() -> dict[int, int]:
    """PID -> process group id for every visible process (best effort)."""

    table: dict[int, int] = {}
    proc_root = Path("/proc")
    if proc_root.is_dir():
        try:
            entries = list(proc_root.iterdir())
        except OSError:
            entries = []
        for entry in entries:
            if not entry.name.isdigit():
                continue
            fields = _stat_after_comm(int(entry.name))
            if not fields or len(fields) < 3:
                continue
            try:
                pgid = int(fields[2])
            except ValueError:
                continue
            if pgid > 0:
                table[int(entry.name)] = pgid
        if table:
            return table
    try:
        result = subprocess.run(
            ["ps", "-Ao", "pid=,pgid="],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            pid, pgid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if pid > 0 and pgid > 0:
            table[pid] = pgid
    return table


def processes_with_env(
    tags: Mapping[str, str],
    *,
    pids: Sequence[int] | None = None,
    environ_reader=process_environ,
) -> list[int]:
    """PIDs whose environment assigns every ``tags`` pair exactly."""

    wanted = {str(key): str(value) for key, value in dict(tags or {}).items()}
    if not wanted:
        return []
    candidates = list(pids) if pids is not None else process_ids()
    found: list[int] = []
    for pid in candidates:
        env = environ_reader(int(pid))
        if env and all(env.get(key) == value for key, value in wanted.items()):
            found.append(int(pid))
    return found


def processes_in_pane_scopes(
    pgids: Iterable[int],
    *,
    cgroup_marker: str = "tmux-spawn-",
    pids: Sequence[int] | None = None,
    pgid_lookup: Mapping[int, int] | None = None,
    cgroup_reader=None,
) -> list[int]:
    """PIDs in one of ``pgids`` that still live in a tmux pane (systemd) scope.

    Both the process group and the cgroup scope must match a snapshot taken
    from an ownership-checked tmux target. These are discovery candidates;
    callers must revalidate ownership under a stable handle before signalling.
    """

    wanted = {int(pgid) for pgid in pgids if type(pgid) is int and pgid > 0}
    if not wanted:
        return []
    reader = process_cgroup if cgroup_reader is None else cgroup_reader
    lookup = process_pgid_map() if pgid_lookup is None else pgid_lookup
    candidates = list(pids) if pids is not None else list(lookup.keys())
    found: list[int] = []
    for pid in candidates:
        pid = int(pid)
        if lookup.get(pid) not in wanted:
            continue
        if cgroup_marker and cgroup_marker not in (reader(pid) or ""):
            continue
        found.append(pid)
    return found


def ancestor_pids(pid: int | None = None) -> list[int]:
    """``pid`` (default: this process) plus its ancestors, nearest first."""

    current = os.getpid() if pid is None else int(pid)
    table = process_table()
    chain: list[int] = []
    while current > 0 and current not in chain:
        chain.append(current)
        info = table.get(current)
        if info is None:
            break
        current = info.ppid
    return chain


def process_cgroup(pid: int) -> str | None:
    """Linux cgroup path of a process (``/proc/<pid>/cgroup``) or ``None``."""

    if not isinstance(pid, int) or pid <= 0:
        return None
    try:
        return Path(f"/proc/{pid}/cgroup").read_text(encoding="utf-8")
    except OSError:
        return None


def is_running(pid: int) -> bool:
    """Public liveness check used by orphan sweeps (zombies count as dead)."""

    return _is_running(pid)


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

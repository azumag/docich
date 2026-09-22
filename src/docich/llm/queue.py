"""Generation-lane queue for the native LLM dispatch (#829 PR-1).

Mirrors the legacy ``_ai_generation_queue_run`` behavior contract while the
state lives under the docich state dir:

- lanes ``comment > radio > improve`` (priorities 0/10/20),
- comment serializes against comment but tolerates one parallel
  low-priority job; radio/improve share a single background slot and never
  start while comment is running or waiting,
- improve additionally waits while any radio job is waiting,
- lane max-wait give-up (rc 92), stale owner reaping (dead PID immediately,
  age-based only for dead owners — a live owner is never reaped by age),
- labels outside comment/radio/improve bypass the queue entirely,
- the improve-gate (radio family only) returns give-up rc 91 without any
  model call, attempt/fail accounting, backoff, or streak.

State is a single fcntl-guarded JSON registry (``lane_locks.json``) instead
of the legacy lock-file forest.  Semantics — not filenames — are the golden
contract, because the native queue never shares a state dir with legacy.
"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import time
from typing import Any

from .contracts import (
    is_radio_family,
    lane_of,
    scope_of,
)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class LaneQueue:
    """Priority lane registry backed by one guarded JSON file."""

    def __init__(self, state_dir: Path, settings) -> None:
        self._path = Path(state_dir) / "ai_generation_locks" / "lane_locks.json"
        self._settings = settings

    def _load_locked(self, handle) -> dict[str, Any]:
        try:
            handle.seek(0)
            raw = handle.read()
        except OSError:
            raw = ""
        if not raw.strip():
            return {"running": {}, "waiting": {}}
        try:
            data = json.loads(raw)
        except ValueError:
            return {"running": {}, "waiting": {}}
        data.setdefault("running", {})
        data.setdefault("waiting", {})
        return data

    def _reap(self, data: dict[str, Any], now: float, stale_sec: int) -> None:
        for lane in list(data["running"].keys()):
            owner = data["running"][lane] or {}
            pid = int(owner.get("pid") or 0)
            if _pid_alive(pid):
                continue
            started = float(owner.get("started_at") or 0)
            if pid <= 0 or (now - started) >= stale_sec:
                del data["running"][lane]

    def _snapshot(self) -> dict[str, Any]:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "a+", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                return self._load_locked(handle)
            finally:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass

    def acquire(
        self,
        label: str,
        owner_pid: int | None = None,
        max_wait_override: int | None = None,
        now: float | None = None,
    ) -> tuple[bool, str]:
        """Acquire a lane slot.

        Returns (True, "") on success or (False, holder) on give-up, where
        holder names the blocking lane for ``queue_giveup_detail`` telemetry.
        Labels without a lane bypass immediately with (True, "bypass").
        """
        lane = lane_of(label)
        if lane is None or not self._settings.queue_enabled:
            return True, "bypass"
        pid = owner_pid or os.getpid()
        wait_sec = max(1, self._settings.queue_wait_sec)
        stale_sec = max(60, self._settings.queue_stale_sec)
        max_wait = self._max_wait(label, max_wait_override)
        hard_cap = self._settings.queue_max_wait_hard_cap
        deadline = None if (max_wait <= 0 and not hard_cap) else time.monotonic() + max(0, max_wait)
        waiting_marked = False
        started = time.time()
        while True:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a+", encoding="utf-8") as handle:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                    data = self._load_locked(handle)
                    self._reap(data, time.time(), stale_sec)
                    running = data["running"]
                    waiting = data["waiting"]
                    if self._may_start(lane, running, waiting):
                        running[lane] = {
                            "pid": pid,
                            "label": label,
                            "started_at": started,
                        }
                        if waiting_marked:
                            waiting.pop(str(pid), None)
                        handle.seek(0)
                        handle.truncate()
                        handle.write(json.dumps(data))
                        return True, ""
                    holder = self._blocker(lane, running, waiting)
                    if not waiting_marked:
                        waiting[str(pid)] = {"lane": lane, "label": label}
                        waiting_marked = True
                        handle.seek(0)
                        handle.truncate()
                        handle.write(json.dumps(data))
                finally:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
            if deadline is not None and time.monotonic() >= deadline:
                self._unmark_waiting(pid)
                return False, holder
            time.sleep(wait_sec)

    def release(self, label: str, owner_pid: int | None = None) -> None:
        lane = lane_of(label)
        if lane is None:
            return
        pid = owner_pid or os.getpid()
        try:
            with open(self._path, "a+", encoding="utf-8") as handle:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                    data = self._load_locked(handle)
                    owner = data["running"].get(lane) or {}
                    if int(owner.get("pid") or 0) == pid:
                        del data["running"][lane]
                    data["waiting"].pop(str(pid), None)
                    handle.seek(0)
                    handle.truncate()
                    handle.write(json.dumps(data))
                finally:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
        except OSError:
            pass

    def _unmark_waiting(self, pid: int) -> None:
        try:
            with open(self._path, "a+", encoding="utf-8") as handle:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                    data = self._load_locked(handle)
                    data["waiting"].pop(str(pid), None)
                    handle.seek(0)
                    handle.truncate()
                    handle.write(json.dumps(data))
                finally:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
        except OSError:
            pass

    def _max_wait(self, label: str, override: int | None) -> int:
        if override is not None and override > 0:
            return override
        scope = scope_of(label)
        if scope == "radio":
            return self._settings.radio_queue_max_wait_sec
        if scope == "improve":
            return self._settings.improve_queue_max_wait_sec
        if self._settings.queue_max_wait_sec > 0:
            return self._settings.queue_max_wait_sec
        return 0

    def _may_start(
        self, lane: str, running: dict, waiting: dict
    ) -> bool:
        if lane == "comment":
            if not self._settings.comment_lane_lock:
                return True
            owner = running.get("comment") or {}
            return not owner
        if lane in ("radio", "improve"):
            if not self._settings.radio_lane_lock:
                return True
            # Never start while comment is running or waiting.
            if running.get("comment"):
                return False
            if any(
                info.get("lane") == "comment" for info in waiting.values()
            ):
                return False
            # Radio/improve share one background slot.
            if running.get("radio") or running.get("improve"):
                return False
            if lane == "improve":
                # A later radio may overtake a waiting improve.
                if any(
                    info.get("lane") == "radio" for info in waiting.values()
                ):
                    return False
            return True
        return True

    def _blocker(self, lane: str, running: dict, waiting: dict) -> str:
        if lane == "comment":
            return "comment"
        if running.get("comment"):
            return "comment"
        if any(info.get("lane") == "comment" for info in waiting.values()):
            return "comment"
        if running.get("radio"):
            return "radio"
        if running.get("improve"):
            return "improve"
        if lane == "improve" and any(
            info.get("lane") == "radio" for info in waiting.values()
        ):
            return "radio"
        return "unknown"


def improve_gate(
    label: str,
    state_dir: Path,
    settings,
    improve_state_path: Path | None = None,
    radio_gen_started_at: float | None = None,
    now: float | None = None,
) -> bool:
    """Return True when dispatch may proceed, False on gate give-up (rc 91).

    Only radio-family labels are gated.  Skipped when the gate is disabled,
    when no improvement job is active (improve_state.json status==running
    with a live PID younger than 7200s), or when the radio generation
    started before the improvement job (started broadcasts run to
    completion).  Otherwise polls until the job ends or the wait cap
    (default 1200s) expires.
    """
    if not is_radio_family(label):
        return True
    if not settings.improve_gate_enabled:
        return True
    at = time.time() if now is None else now
    wait_sec = max(1, settings.queue_wait_sec)
    cap = max(0, settings.improve_gate_wait_max_sec)
    deadline = at + cap
    path = (
        Path(improve_state_path)
        if improve_state_path is not None
        else Path(state_dir) / "improve_state.json"
    )
    while True:
        job = _read_improve_job(path)
        if job is None:
            return True
        started = job.get("started_at") or 0
        if radio_gen_started_at and started and radio_gen_started_at < started:
            return True
        if time.time() >= deadline:
            return False
        time.sleep(wait_sec)


def _read_improve_job(path: Path) -> dict | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("status") != "running":
        return None
    pid = data.get("pid") or data.get("owner_pid") or 0
    try:
        pid = int(pid)
    except (ValueError, TypeError):
        return None
    if pid <= 0 or not _pid_alive(pid):
        return None
    try:
        age = time.time() - float(data.get("started_at") or 0)
    except (ValueError, TypeError):
        return None
    if age >= 7200:
        return None
    return data

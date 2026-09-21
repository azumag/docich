"""Durable 24/N corner selection, independent of corner execution adapters."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import datetime as dt
import fcntl
import hashlib
import json
import math
from pathlib import Path
import secrets
import time
import uuid

from .corner_catalog import load_catalog, rotation_enabled
from .corner_adapters import make_corner_adapter, CornerExecutionCoordinator
from .game_switch import atomic_write_json

DAY = 86400.0
BUSY = {"waiting", "starting", "active", "restoring", "preparing", "recovery_required", "failed"}
TERMINAL = {"idle", "completed", "interrupted", "expired"}


class RotationError(RuntimeError):
    pass


def timestamp(value):
    if isinstance(value, str):
        parsed = dt.datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise RotationError("timestamp requires timezone")
        value = parsed.timestamp()
    if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
        raise RotationError("invalid timestamp")
    return float(value)


class CornerRotationManager:
    def __init__(self, g, *, clock=time.time, sleep=time.sleep, seed=None,
                 catalog=None, adapter_factory=make_corner_adapter, executor=None):
        self.g, self.clock = g, clock
        self.catalog = tuple(load_catalog(g) if catalog is None else catalog)
        self.adapters = {c.id: adapter_factory(g, c) for c in self.catalog}
        self.executor = executor or CornerExecutionCoordinator(g, clock=clock, sleep=sleep)
        self.seed = seed
        self.path = Path(g.state_dir) / "corner_rotation.json"
        self.lock_path = Path(g.state_dir) / "locks/corner-rotation.lock"

    @contextmanager
    def locked(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.lock_path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def save(self, state):
        atomic_write_json(self.path, state)

    def load(self, now):
        if not self.path.exists():
            if any(raw.get("rotation_request_id") for adapter in self.adapters.values()
                   for raw in adapter.observations()):
                raise RotationError("rotation ledger missing; explicit recovery required")
            state = dict(schema_version=1, seed=str(self.seed if self.seed is not None else secrets.token_hex(32)),
                         slot=0, last_seen_at=now, next_due_at=now, last_slot_at=None,
                         history=[], pending=None, status="ready", migration="legacy-imported")
            # Import rolling history even for disabled/removed entries. Never
            # reset cooldown because catalog ordering or eligibility changed.
            old = Path(self.g.state_dir) / "retro_corner.json"
            if old.exists():
                legacy = json.loads(old.read_text())
                if not isinstance(legacy, dict) or legacy.get("schema_version") != 1:
                    raise RotationError("legacy state requires explicit recovery")
                rotation = legacy.get("rotation", {})
                if not isinstance(rotation, dict):
                    raise RotationError("invalid legacy rotation")
                if rotation.get("pending") or legacy.get("status") in BUSY:
                    raise RotationError("finish legacy pending execution before migration")
                for item in rotation.get("selection_history", []):
                    game = item["game"]
                    corner_id = next((c.id for c in self.catalog if c.game == game), game)
                    state["history"].append(dict(corner=corner_id, at=timestamp(item["selected_at"]), source="legacy"))
                if rotation.get("next_due_at"):
                    state["next_due_at"] = max(now, timestamp(rotation["next_due_at"]))
            return state
        state = json.loads(self.path.read_text())
        if not isinstance(state, dict) or state.get("schema_version") != 1:
            raise RotationError("unsupported rotation state; explicit recovery required")
        if (not isinstance(state.get("seed"), str) or not state["seed"]
                or type(state.get("slot")) is not int or state["slot"] < 0
                or not isinstance(state.get("history"), list)
                or state.get("status") not in {"ready", "waiting", "running", "recovery_required"}):
            raise RotationError("invalid rotation state")
        for key in ("next_due_at", "last_seen_at"):
            timestamp(state[key])
        if state.get("last_slot_at") is not None:
            timestamp(state["last_slot_at"])
        for row in state["history"]:
            if not isinstance(row.get("corner"), str):
                raise RotationError("invalid cooldown identity")
            timestamp(row["at"])
        request = state.get("pending")
        if request is not None:
            if (not isinstance(request, dict) or request.get("phase") not in {"selected", "dispatched"}
                    or not isinstance(request.get("corner"), str)):
                raise RotationError("invalid pending request")
            uuid.UUID(request["request_id"])
            timestamp(request["selected_at"])
        manual = state.get("manual_pending")
        if manual is not None:
            if (not isinstance(manual, dict) or not isinstance(manual.get("corner"), str)
                    or not isinstance(manual.get("state_file"), str)
                    or Path(manual["state_file"]).name != manual["state_file"]):
                raise RotationError("invalid manual pending request")
            uuid.UUID(manual["request_id"])
            timestamp(manual["selected_at"])
        return state

    def _observe(self, state, now):
        """Import manual usage; unknown or orphaned execution blocks selection."""
        pending = state.get("pending")
        busy = False
        for corner_id, adapter in self.adapters.items():
            released = getattr(adapter, "resources_released", None)
            if released is not None and not released():
                busy = True
            for raw in adapter.observations():
                status = raw.get("status", "idle")
                if status not in BUSY | TERMINAL:
                    raise RotationError("unknown adapter state")
                own = pending and raw.get("rotation_request_id") == pending["request_id"]
                if status in BUSY and not own:
                    busy = True
                # Use actual start AND completion; a delayed queued start can
                # never become eligible 24h after its much earlier selection.
                stamps = [timestamp(raw[key]) for key in ("started_at", "completed_at") if raw.get(key) is not None]
                if stamps:
                    stamp = max(stamps)
                    if stamp > now:
                        raise RotationError("adapter timestamp is in the future")
                    previous = max((r["at"] for r in state["history"] if r["corner"] == corner_id), default=-1)
                    if stamp > previous:
                        state["history"].append(dict(corner=corner_id, at=stamp, source="execution"))
        return busy

    def _eligible(self):
        result, excluded = [], {}
        for corner in sorted(self.catalog, key=lambda c: c.id):
            if not corner.enabled or corner.paused or (Path(self.g.state_dir) / "corners" / f"{corner.id}.paused").exists():
                excluded[corner.id] = "disabled-or-paused"
                continue
            try:
                available = self.adapters[corner.id].eligible()
            except Exception:
                available = False
            if available:
                result.append(corner.id)
            else:
                excluded[corner.id] = "adapter-unavailable"
        return result, excluded

    def tick(self):
        if not rotation_enabled(self.g):
            return {"status": "disabled"}
        with self.locked() as acquired:
            if not acquired:
                return {"status": "waiting", "reason": "already-running"}
            now = timestamp(self.clock())
            state = self.load(now)
            if state["status"] == "recovery_required":
                return {"status": "recovery_required", "reason": state.get("reason")}
            if now < state["last_seen_at"]:
                return self._wait(state, "clock-regressed")
            if now - state["last_seen_at"] > DAY:
                # A large jump is indistinguishable from downtime without a
                # trusted time source: quarantine one full day, don't erase use.
                state["clock_hold_until"] = now + DAY
            state["last_seen_at"] = now
            if now < state.get("clock_hold_until", 0):
                return self._wait(state, "clock-gap-quarantine")
            try:
                busy = self._observe(state, now)
                manual = state.get("manual_pending")
                if manual is not None:
                    adapter = self.adapters.get(manual.get("corner"))
                    completed = adapter is not None and any(
                        raw.get("rotation_request_id") == manual.get("request_id") and raw.get("status") == "completed"
                        for raw in adapter.observations()
                    )
                    if not completed:
                        return self._wait(state, "manual-request-needs-resume-or-recovery")
                    state.pop("manual_pending")
                eligible, excluded = self._eligible()
                state.update(eligible=eligible, excluded=excluded,
                             interval_seconds=DAY / len(eligible) if eligible else None)
                pending = state.get("pending")
                if busy:
                    return self._wait(state, "other-corner-needs-finish-or-recovery")
                if pending is None:
                    if not eligible:
                        return self._wait(state, "no-enabled-corner")
                    if state.get("last_slot_at") is not None:
                        state["next_due_at"] = state["last_slot_at"] + state["interval_seconds"]
                    if now < state["next_due_at"]:
                        return self._wait(state, "not-due")
                    recent = {r["corner"] for r in state["history"] if r["at"] > now - DAY}
                    candidates = [c for c in eligible if c not in recent]
                    if not candidates:
                        return self._wait(state, "all-corners-cooling-down")
                    # Seeded independent random ranking is stable across restart
                    # and catalog order, without persisting interpreter RNG state.
                    def rank(corner):
                        return hashlib.sha256(f'{state["seed"]}:{state["slot"]}:{corner}'.encode()).digest()
                    chosen = min(candidates, key=rank)
                    pending = dict(corner=chosen, phase="selected", selected_at=now,
                                   request_id=str(uuid.uuid5(uuid.NAMESPACE_URL,
                                       f'corner:{state["seed"]}:{state["slot"]}:{chosen}')))
                    state["pending"] = pending
                    state["slot"] += 1
                    self.save(state)  # write-ahead reservation, before any side effect
                if pending["corner"] not in self.adapters:
                    raise RotationError("pending corner removed from catalog")
                adapter = self.adapters[pending["corner"]]
                owned = any(raw.get("rotation_request_id") == pending["request_id"]
                            for raw in adapter.observations())
                if pending["corner"] not in eligible and not owned:
                    return self._wait(state, "selected-corner-disabled-or-paused")
                if pending["phase"] == "selected":
                    if pending["corner"] not in eligible:
                        return self._wait(state, "selected-corner-disabled-or-paused")
                    # Re-check cooldown after importing concurrent/manual activity.
                    if any(r["corner"] == pending["corner"] and r["at"] > now - DAY for r in state["history"]):
                        return self._wait(state, "selected-corner-cooling-down")
                    pending["phase"] = "dispatched"
                    state["last_slot_at"] = now
                    state["next_due_at"] = now + state["interval_seconds"]
                    state["history"].append(dict(corner=pending["corner"], at=now, source="reservation"))
                state.update(status="running", reason=None)
                self.save(state)
                result = self.executor.execute(adapter, pending)
                status = result if isinstance(result, str) else result.status
                for raw in adapter.observations():
                    if raw.get("rotation_request_id") == pending["request_id"] and raw.get("started_at") is not None:
                        actual_start = timestamp(raw["started_at"])
                        state["last_slot_at"] = max(state["last_slot_at"], actual_start)
                        if state["interval_seconds"] is not None:
                            state["next_due_at"] = state["last_slot_at"] + state["interval_seconds"]
                if status == "completed":
                    finished = timestamp(self.clock())
                    state["history"].append(dict(corner=pending["corner"], at=max(now, finished), source="completion"))
                    state.update(pending=None, status="ready", last_result={"corner": pending["corner"],
                                 "request_id": pending["request_id"], "status": status, "at": finished})
                    # Compact only old history, after successful execution; retain
                    # at least the last use of every ID including removed entries.
                    latest = {}
                    for row in state["history"]:
                        if row["at"] >= latest.get(row["corner"], {}).get("at", -1):
                            latest[row["corner"]] = row
                    state["history"] = list(latest.values())
                elif status in {"queued", "waiting", "already-running"}:
                    state.update(status="waiting", reason="execution-pending")
                else:
                    raise RotationError("execution requires recovery")
                self.save(state)
                return {"status": state["status"], "corner": pending["corner"], "result": status}
            except Exception:
                # Do not persist exception text: provider/config errors may carry
                # output or credentials. The pending request remains inspectable.
                state.update(status="recovery_required", reason="execution-or-state-unverified")
                self.save(state)
                raise

    def _wait(self, state, reason):
        state.update(status="waiting", reason=reason)
        self.save(state)
        return {"status": "waiting", "reason": reason}


def stop_manual(g, manager, callback):
    """Stops share the coordinator lock; the manager retains its restore receipt."""
    rotation = CornerRotationManager(g)
    with rotation.locked() as acquired:
        if not acquired:
            raise RotationError("common corner coordinator is busy")
        return callback()


def main(argv=None):
    from .config import load_global
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument("command", choices=["tick", "status"])
    args = parser.parse_args(argv)
    manager = CornerRotationManager(load_global(Path(__file__).resolve().parents[2], args.config))
    if args.command == "status":
        print(manager.path.read_text() if manager.path.exists() else "{}")
    else:
        print(json.dumps(manager.tick(), sort_keys=True))
    return 0


def run_manual(g, manager, games):
    """Serialize legacy/manual entries with automatic rotation; account usage.

    Manual commands keep their original state files and execution options. A
    crash leaves the write-ahead reservation and adapter state for recovery.
    """
    from types import SimpleNamespace
    from .retro_corner import CornerResult
    rotation = CornerRotationManager(g)
    with rotation.locked() as acquired:
        if not acquired:
            raise RotationError("common corner coordinator is busy")
        now = timestamp(rotation.clock())
        state = rotation.load(now)
        if state["status"] == "recovery_required" or state.get("pending"):
            raise RotationError("pending corner must finish or recover before manual start")
        if now < state["last_seen_at"]:
            raise RotationError("clock regressed")
        eligible, _ = rotation._eligible()
        state.update(eligible=eligible, interval_seconds=DAY / len(eligible) if eligible else None)
        path = getattr(manager, "state_path", None) or manager.path
        request = state.get("manual_pending")
        if request is not None:
            choices = [c for c in rotation.catalog if c.id == request.get("corner") and c.game in games]
            if not choices or request.get("state_file") != path.name:
                raise RotationError("manual pending owner mismatch")
            chosen = choices[0]
            if chosen.id not in eligible and not any(
                raw.get("rotation_request_id") == request["request_id"]
                for raw in rotation.adapters[chosen.id].observations()
            ):
                raise RotationError("reserved manual corner is disabled")
        else:
            if rotation._observe(state, now):
                raise RotationError("pending corner must finish or recover before manual start")
            choices = [c for c in rotation.catalog if c.game in games and c.id in eligible
                       and not any(r["corner"] == c.id and r["at"] > now - DAY for r in state["history"])]
            if not choices:
                raise RotationError("no eligible manual corner outside rolling cooldown")
            chosen = min(choices, key=lambda c: hashlib.sha256(f'{state["seed"]}:{state["slot"]}:{c.id}'.encode()).digest())
            request = dict(corner=chosen.id, selected_at=now, request_id=str(uuid.uuid4()), state_file=path.name)
            state["manual_pending"] = request
            state["slot"] += 1
            state["history"].append(dict(corner=chosen.id, at=now, source="manual-reservation"))
            state.update(last_slot_at=now, next_due_at=now + DAY / len(eligible))
        # Limit multi-game retro manual starts to the same reserved selection.
        if hasattr(manager, "config"):
            from dataclasses import replace
            overrides = {"games": [chosen.game]}
            for key in ("daily_each_game", "randomize_start"):
                if hasattr(manager.config, key):
                    overrides[key] = False
            manager.config = replace(manager.config, **overrides)
        state.update(last_seen_at=now, status="running")
        rotation.save(state)
        adapter = SimpleNamespace(manager=manager, state_path=path,
                                  run=lambda req: manager.run_rotation(req["request_id"]))
        try:
            result = rotation.executor.execute(adapter, request)
            status = result if isinstance(result, str) else result.status
            state.update(status="waiting", reason="manual-execution-pending")
            if status == "completed":
                finished = timestamp(rotation.clock())
                state["history"].append(dict(corner=chosen.id, at=finished, source="manual-completion"))
                state["last_slot_at"] = max(now, finished)
                if eligible:
                    state["next_due_at"] = state["last_slot_at"] + DAY / len(eligible)
                state.update(status="ready", reason=None)
                state.pop("manual_pending", None)
            elif status not in {"queued", "waiting", "already-running"}:
                state.update(status="recovery_required", reason="manual-execution-unverified")
            rotation.save(state)
            if isinstance(result, str) and hasattr(manager, "state_path"):
                return CornerResult(result, game=chosen.game)
            return result
        except Exception:
            state.update(status="recovery_required", reason="manual-execution-unverified")
            rotation.save(state)
            raise


if __name__ == "__main__":
    raise SystemExit(main())

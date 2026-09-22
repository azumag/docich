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
import sys
import time
import uuid

from .corner_catalog import cooldown_seconds, load_catalog, rotation_enabled, schedule_mode
from .corner_adapters import (
    make_corner_adapter,
    CornerExecutionCoordinator,
    CornerExecutionError,
    RetiredCornerObserver,
)
from .game_switch import atomic_write_json

DAY = 86400.0
STOP_REQUEST_DIR = "corner-stop-requests"
BUSY = {"waiting", "starting", "active", "restoring", "preparing", "recovery_required", "failed"}
TERMINAL = {"idle", "completed", "interrupted", "expired"}

# Fixed latch taxonomy (#986). The exception text is never persisted: provider
# or config output can carry credentials or generated content. Diagnostics and
# operators only ever see one of these categories. Keep in sync with
# ops/vm_actions/collect_diagnostics.py ROTATION_ERROR_KINDS (regression-tested).
ERROR_KINDS = frozenset({
    "adapter-state",
    "adapter-timestamp",
    "catalog-mismatch",
    "execution-error",
    "execution-unverified",
    "invalid-state",
    "unexpected",
})


class RotationError(RuntimeError):
    """A fail-closed rotation failure carrying a fixed, persistable category."""

    kind = "invalid-state"

    def __init__(self, message, *, kind=None):
        super().__init__(message)
        if kind is not None and kind in ERROR_KINDS:
            self.kind = kind


def _error_kind(exc):
    """Map a latch cause to a fixed category; never return exception text."""
    kind = getattr(exc, "kind", None)
    if isinstance(kind, str) and kind in ERROR_KINDS:
        return kind
    if isinstance(exc, CornerExecutionError):
        return "execution-error"
    return "unexpected"


def timestamp(value):
    if isinstance(value, str):
        parsed = dt.datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise RotationError("timestamp requires timezone")
        value = parsed.timestamp()
    if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
        raise RotationError("invalid timestamp")
    return float(value)


def _stop_request_path(g, state_path):
    return Path(g.state_dir) / STOP_REQUEST_DIR / Path(state_path).name


def rotation_stop_requested(g, state_path):
    path = _stop_request_path(g, state_path)
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return True
    return isinstance(data, dict) and data.get("state_file") == Path(state_path).name


def clear_rotation_stop_request(g, state_path):
    path = _stop_request_path(g, state_path)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        # A stale marker is fail-closed and will be retried by the next owner.
        return False
    return True


class CornerRotationManager:
    def __init__(self, g, *, clock=time.time, sleep=time.sleep, seed=None,
                 catalog=None, adapter_factory=make_corner_adapter, executor=None):
        self.g, self.clock = g, clock
        self.catalog = tuple(load_catalog(g) if catalog is None else catalog)
        self.adapters = {c.id: adapter_factory(g, c) for c in self.catalog}
        self.executor = executor or CornerExecutionCoordinator(g, clock=clock, sleep=sleep)
        self.seed = seed
        # Dispatch policy and rolling cooldown are configuration, not state:
        # flipping schedule_mode must not rewrite the durable ledger.
        self.schedule_mode = schedule_mode(g)
        self.cooldown_seconds = cooldown_seconds(g)
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

    def _initial_known_corners(self):
        """Seed the migration ledger from pre-catalog corner state files.

        A catalog can be edited before the first unified state is written. In
        that case the current rows alone cannot identify an old active corner
        or an unfinished improvement job. Keep the legacy state visible as a
        retired observer until it reaches a terminal, released state.
        """
        known = {
            corner.id: {
                "id": corner.id,
                "adapter": corner.adapter,
                "game": corner.game,
            }
            for corner in self.catalog
        }
        current_by_game = {corner.game: corner for corner in self.catalog}
        legacy = (
            ("retro_corner.json", "game", None),
            ("paper_corner.json", "paper", ("paper", "paper-view")),
            ("paper_corner_manual.json", "paper", ("paper", "paper-view")),
            ("soren91_corner.json", "meriken", ("meriken", "soren91")),
            ("nethack_corner.json", "nethack", ("nethack", "nethack")),
        )
        for filename, adapter, fixed_identity in legacy:
            path = Path(self.g.state_dir) / filename
            if not path.exists():
                continue
            try:
                raw = json.loads(path.read_text())
            except (OSError, ValueError) as exc:
                raise RotationError("legacy corner state requires explicit recovery") from exc
            if not isinstance(raw, dict):
                raise RotationError("legacy corner state requires explicit recovery")
            if fixed_identity is not None:
                corner_id, game = fixed_identity
                known.setdefault(corner_id, {"id": corner_id, "adapter": adapter, "game": game})
                continue
            game = raw.get("game")
            if not isinstance(game, str) or not game:
                continue
            current = current_by_game.get(game)
            if current is not None:
                known.setdefault(current.id, {
                    "id": current.id,
                    "adapter": current.adapter,
                    "game": current.game,
                })
            else:
                known.setdefault(game, {"id": game, "adapter": adapter, "game": game})
        return known

    def load(self, now):
        if not self.path.exists():
            if any(raw.get("rotation_request_id") for adapter in self.adapters.values()
                   for raw in adapter.observations()):
                raise RotationError("rotation ledger missing; explicit recovery required")
            state = dict(schema_version=1, seed=str(self.seed if self.seed is not None else secrets.token_hex(32)),
                         slot=0, last_seen_at=now, next_due_at=now, last_slot_at=None,
                         history=[], pending=None, status="ready", migration="legacy-imported",
                         known_corners=self._initial_known_corners())
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
        known = state.get("known_corners")
        if known is not None:
            if not isinstance(known, dict):
                raise RotationError("invalid known corner ledger")
            for record in known.values():
                if (not isinstance(record, dict)
                        or not isinstance(record.get("id"), str)
                        or record.get("adapter") not in {"game", "paper", "meriken", "nethack"}
                        or not isinstance(record.get("game"), str)):
                    raise RotationError("invalid known corner identity")
        if (state.get("status") == "running"
                and state.get("pending") is None
                and state.get("manual_pending") is None):
            raise RotationError("running state missing request owner")
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

    def _remember_catalog(self, state):
        known = state.setdefault("known_corners", {})
        if not isinstance(known, dict):
            raise RotationError("invalid known corner ledger")
        for corner in self.catalog:
            current = {"id": corner.id, "adapter": corner.adapter, "game": corner.game}
            previous = known.get(corner.id)
            if (isinstance(previous, dict)
                    and (previous.get("adapter"), previous.get("game"))
                    != (corner.adapter, corner.game)):
                retired_key = f"retired:{corner.id}:{previous.get('game', 'unknown')}"
                known.setdefault(retired_key, previous)
            known[corner.id] = current

    def _observers(self, state):
        observers = dict(self.adapters)
        known = state.get("known_corners") or {}
        current_ids = set(self.adapters)
        for key, record in known.items():
            if key in current_ids:
                continue
            observers[key] = RetiredCornerObserver(self.g, record)
        return observers

    def _observe(self, state, now):
        """Import manual usage; unknown or orphaned execution blocks selection."""
        pending = state.get("pending")
        busy = False
        for corner_id, adapter in self._observers(state).items():
            history_id = getattr(adapter, "corner_id", corner_id)
            released = getattr(adapter, "resources_released", None)
            if released is not None and not released():
                busy = True
            for raw in adapter.observations():
                status = raw.get("status", "idle")
                if status not in BUSY | TERMINAL:
                    raise RotationError("unknown adapter state", kind="adapter-state")
                own = pending and raw.get("rotation_request_id") == pending["request_id"]
                if status in BUSY and not own:
                    busy = True
                # Use actual start AND completion; a delayed queued start can
                # never become eligible 24h after its much earlier selection.
                stamps = [timestamp(raw[key]) for key in ("started_at", "completed_at") if raw.get(key) is not None]
                if stamps:
                    stamp = max(stamps)
                    if stamp > now:
                        raise RotationError("adapter timestamp is in the future",
                                            kind="adapter-timestamp")
                    previous = max((r["at"] for r in state["history"] if r["corner"] == history_id), default=-1)
                    if stamp > previous:
                        state["history"].append(dict(corner=history_id, at=stamp, source="execution"))
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

    def _cooldown_due_at(self, history, eligible, now):
        """Return the earliest timestamp at which a cooling corner can recur.

        ``next_due_at`` is the nominal 24/N cadence.  When the cadence gets
        ahead of the rolling cooldown (for example after downtime or a late
        completion), every eligible corner can still be excluded even though
        the nominal slot is overdue.  Keeping the old nominal timestamp makes
        diagnostics report a permanent overdue slot and causes each tick to
        reconsider the same impossible selection.  Re-anchor to the first
        actual cooldown expiry instead.
        """
        eligible = set(eligible)
        deadlines = [
            row["at"] + self.cooldown_seconds
            for row in history
            if row.get("corner") in eligible and row["at"] > now - self.cooldown_seconds
        ]
        return min(deadlines) if deadlines else None

    def tick(self):
        if not rotation_enabled(self.g):
            return {"status": "disabled"}
        with self.locked() as acquired:
            if not acquired:
                return {"status": "waiting", "reason": "already-running"}
            now = timestamp(self.clock())
            state = self.load(now)
            self._remember_catalog(state)
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
                interval = DAY / len(eligible) if eligible else None
                state.update(
                    eligible=eligible, excluded=excluded,
                    # interval_seconds only describes the interval policy; the
                    # queue policy has no cadence (cooldown is the only gate).
                    interval_seconds=interval if self.schedule_mode == "interval" else None,
                )
                pending = state.get("pending")
                if busy:
                    return self._wait(state, "other-corner-needs-finish-or-recovery")
                if pending is None:
                    if not eligible:
                        return self._wait(state, "no-enabled-corner")
                    if self.schedule_mode == "interval":
                        if state.get("last_slot_at") is not None:
                            state["next_due_at"] = state["last_slot_at"] + state["interval_seconds"]
                        if now < state["next_due_at"]:
                            return self._wait(state, "not-due")
                    else:
                        # Queue dispatch: the next eligible corner fires as soon
                        # as the shared slot is free; next_due_at is informational
                        # (the earliest cooldown expiry once nothing is eligible).
                        state["next_due_at"] = now
                    recent = {
                        r["corner"] for r in state["history"]
                        if r["at"] > now - self.cooldown_seconds
                    }
                    candidates = [c for c in eligible if c not in recent]
                    if not candidates:
                        cooldown_due = self._cooldown_due_at(state["history"], eligible, now)
                        if cooldown_due is not None:
                            state["next_due_at"] = cooldown_due
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
                    raise RotationError("pending corner removed from catalog",
                                        kind="catalog-mismatch")
                adapter = self.adapters[pending["corner"]]
                owned = any(raw.get("rotation_request_id") == pending["request_id"]
                            for raw in adapter.observations())
                if pending["corner"] not in eligible and not owned:
                    return self._wait(state, "selected-corner-disabled-or-paused")
                if pending["phase"] == "selected":
                    if pending["corner"] not in eligible:
                        return self._wait(state, "selected-corner-disabled-or-paused")
                    # Re-check cooldown after importing concurrent/manual activity.
                    if any(r["corner"] == pending["corner"] and r["at"] > now - self.cooldown_seconds
                           for r in state["history"]):
                        return self._wait(state, "selected-corner-cooling-down")
                    pending["phase"] = "dispatched"
                    state["last_slot_at"] = now
                    state["next_due_at"] = (
                        now + state["interval_seconds"]
                        if self.schedule_mode == "interval" else now
                    )
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
                    raise RotationError("execution requires recovery",
                                        kind="execution-unverified")
                self.save(state)
                return {"status": state["status"], "corner": pending["corner"], "result": status}
            except Exception as exc:
                # Do not persist exception text: provider/config errors may carry
                # output or credentials. The pending request remains inspectable.
                state.update(status="recovery_required",
                             reason="execution-or-state-unverified",
                             error_kind=_error_kind(exc))
                self.save(state)
                raise

    def recover(self):
        """Resolve a latched reservation without bypassing fail-closed gates (#986).

        This is the operator-triggered counterpart of the early return in
        ``tick()``. It never edits the ledger by hand, never drops the
        reservation and never starts a second execution:

        - a reservation (automatic ``pending`` or ``manual_pending``) whose own
          adapter observation is already terminal is committed from that
          observation (request identity, history and cooldown preserved, no
          duplicate launch);
        - an automatic reservation that never started is handed back to the
          normal tick path as ``waiting``/``execution-pending``, which
          re-validates game-switch phase, the program slot, cooldown and
          ownership before any side effect and re-latches with a fixed
          ``error_kind`` if the cause persists;
        - a reservation whose corner is still running or itself needs corner
          recovery, an inconsistent ledger, and a manual reservation that has
          no terminal observation yet, stay latched and fail closed. For the
          manual case the corner's own manual stop/recover must run first;
          this method then observes that terminal state and commits it.
        """
        if not rotation_enabled(self.g):
            return {"status": "disabled"}
        with self.locked() as acquired:
            if not acquired:
                return {"status": "waiting", "reason": "already-running"}
            now = timestamp(self.clock())
            state = self.load(now)
            self._remember_catalog(state)
            if state["status"] != "recovery_required":
                raise RotationError("rotation state is not latched", kind="invalid-state")
            # Every branch below may refresh last_seen_at, so clock regression
            # is rejected before any of them can move a timestamp backwards.
            if now < state["last_seen_at"]:
                raise RotationError("clock regressed", kind="invalid-state")
            pending = state.get("pending")
            manual = state.get("manual_pending")
            if pending is not None and manual is not None:
                raise RotationError("manual reservation is inconsistent with the automatic one",
                                    kind="invalid-state")
            reservation = pending if isinstance(pending, dict) else manual
            if reservation is None:
                # Latched before any reservation existed: retry through the
                # normal path, which re-derives selection and re-latches with
                # a fixed error_kind if the cause is still present.
                state.update(last_seen_at=now, status="waiting", reason="recovery-retry")
                self.save(state)
                return {"status": "waiting", "reason": "recovery-retry"}
            if reservation.get("corner") not in self.adapters:
                raise RotationError("pending corner removed from catalog",
                                    kind="catalog-mismatch")
            try:
                outcome = self._resolve_reservation(state, reservation, now,
                                                    manual=manual is not None)
            except Exception as exc:
                # A refusal must not overwrite the classification of the
                # original latch: that is the evidence operators diagnose from.
                state.update(status="recovery_required",
                             reason="execution-or-state-unverified")
                if state.get("error_kind") is None:
                    state["error_kind"] = _error_kind(exc)
                self.save(state)
                raise
            self.save(state)
            return outcome

    def _resolve_reservation(self, state, reservation, now, *, manual):
        corner_id = reservation["corner"]
        adapter = self.adapters[corner_id]
        owned = [raw for raw in adapter.observations()
                 if raw.get("rotation_request_id") == reservation.get("request_id")]
        # Any own busy observation blocks, so the enumeration order of the
        # canonical and the manual state file can never decide the outcome.
        if any(raw.get("status", "idle") in BUSY for raw in owned):
            # The corner is running or needs its own recovery. Launching again
            # here would duplicate the corner.
            raise RotationError("pending execution needs corner-level recovery",
                                kind="execution-unverified")
        if not owned:
            if manual:
                # The manual attempt never recorded an execution here, so no
                # corner-side state can ever carry this request id. Return the
                # reservation to the operator exactly like a mid-run handoff:
                # ``tick()`` then parks on manual-request-needs-resume-or-
                # recovery (no automatic corner starts) until the same manual
                # start resumes this request or a manual stop/recover and the
                # game-switch receipt prove it finished. Dropping the
                # reservation here would silently delete an operator's slot.
                state.update(last_seen_at=now, status="waiting",
                             reason="manual-request-needs-resume-or-recovery")
                return {"status": "waiting", "corner": corner_id,
                        "reason": "manual-request-needs-resume-or-recovery"}
            if self._observe(state, now):
                state.update(last_seen_at=now, status="waiting",
                             reason="other-corner-needs-finish-or-recovery")
                return {"status": "waiting",
                        "reason": "other-corner-needs-finish-or-recovery"}
            # Hand the unchanged reservation back to the timer tick. The history
            # row written at dispatch keeps this corner inside its rolling
            # cooldown either way, so a resume can never bypass cooldown.
            state.update(last_seen_at=now, status="waiting", reason="execution-pending")
            return {"status": "waiting", "corner": corner_id,
                    "reason": "execution-pending", "resumed": True}

        def stamp(raw):
            values = [timestamp(raw[key]) for key in ("completed_at", "started_at")
                      if raw.get(key) is not None]
            return max(values) if values else -1.0

        raw = max(owned, key=stamp)
        observed = raw.get("status", "idle")
        if observed not in TERMINAL:
            raise RotationError("unknown adapter state", kind="adapter-state")
        finished = (timestamp(raw["completed_at"])
                    if raw.get("completed_at") is not None else now)
        if finished > now:
            raise RotationError("adapter timestamp is in the future",
                                kind="adapter-timestamp")
        started = raw.get("started_at")
        if started is not None:
            stamp_at = timestamp(started)
            previous = (timestamp(state["last_slot_at"])
                        if state.get("last_slot_at") is not None else stamp_at)
            state["last_slot_at"] = max(previous, stamp_at, finished)
        # Commit the observed terminal result: request identity and the
        # dispatch reservation stay in history, so cooldown still counts.
        if manual:
            state["manual_pending"] = None
            if observed == "completed":
                state["history"].append(
                    dict(corner=corner_id, at=max(now, finished),
                         source="manual-completion"))
        else:
            state["pending"] = None
            state["history"].append(
                dict(corner=corner_id, at=max(now, finished), source="completion"))
        state.update(last_seen_at=now, status="ready", reason=None,
                     last_result={"corner": corner_id,
                                  "request_id": reservation["request_id"],
                                  "status": observed, "at": finished})
        return {"status": "ready", "corner": corner_id,
                "result": observed, "recovered": True}

    def _wait(self, state, reason):
        state.update(status="waiting", reason=reason)
        self.save(state)
        return {"status": "waiting", "reason": reason}


def stop_manual(g, manager, callback, *, busy_callback=None):
    """Stop through the common lock, or the owner state if a run is active.

    A long-running corner owns the scheduler lock while its adapter waits for
    a boundary or drains content. Refusing an operator stop in that window
    would strand the owner. The manager-specific state/coordinator path is the
    fallback and still fails closed on an unverified runtime owner.
    """
    rotation = CornerRotationManager(g)
    with rotation.locked() as acquired:
        if acquired:
            return callback()
    state_path = getattr(manager, "state_path", None) or getattr(manager, "path", None)
    if state_path is None:
        return busy_callback() if busy_callback is not None else callback()
    try:
        current = json.loads(Path(state_path).read_text()) if Path(state_path).exists() else {}
    except (OSError, ValueError):
        current = {"status": "recovery_required"}
    # An already-active owner can be stopped through its manager-specific
    # coordinator path even while the common lock is held.  A starting owner
    # must instead receive a durable marker: direct stop has no runtime to
    # restore yet, and the marker is consumed once the start reaches active.
    if busy_callback is not None and current.get("status") != "starting":
        return busy_callback()
    if current.get("status") not in {"starting", "active", "restoring"}:
        return callback()
    request_path = _stop_request_path(g, state_path)
    request_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write_json(request_path, {
        "state_file": Path(state_path).name,
        "requested_at": time.time(),
    })
    return "queued"


def main(argv=None):
    from .config import load_global
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument("command", choices=["tick", "status", "recover"])
    args = parser.parse_args(argv)
    manager = CornerRotationManager(load_global(Path(__file__).resolve().parents[2], args.config))
    if args.command == "status":
        print(manager.path.read_text() if manager.path.exists() else "{}")
    elif args.command == "recover":
        # Operator-gated recovery (#986). RotationError messages are fixed
        # literals from this module; unexpected exception text is never printed
        # because it may carry provider output or credentials.
        try:
            outcome = manager.recover()
        except RotationError as exc:
            print(f"corner rotation recovery refused: {exc}", file=sys.stderr)
            return 2
        except Exception:
            print("corner rotation recovery failed; see error_kind in the rotation state",
                  file=sys.stderr)
            return 3
        # Success means the durable latch is gone, not that recovery was
        # attempted: a busy lock or a disabled rotation leaves it in place and
        # must fail the owner-only operation instead of reporting success. An
        # existing ledger that cannot be read cannot prove the latch is gone
        # either (a missing ledger has no latch to resolve).
        if not manager.path.exists():
            resolved = True
        else:
            try:
                persisted = json.loads(manager.path.read_text())
            except (OSError, ValueError):
                persisted = None
            resolved = (isinstance(persisted, dict)
                        and persisted.get("status") != "recovery_required")
        print(json.dumps({**outcome, "latch_resolved": resolved}, sort_keys=True))
        if not resolved:
            print("corner rotation latch remains after recovery", file=sys.stderr)
            return 4
        return 0
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
        rotation._remember_catalog(state)
        if state["status"] == "recovery_required" or state.get("pending"):
            raise RotationError("pending corner must finish or recover before manual start")
        if now < state["last_seen_at"]:
            raise RotationError("clock regressed")
        eligible, _ = rotation._eligible()
        state.update(
            eligible=eligible,
            interval_seconds=(DAY / len(eligible) if eligible else None)
            if rotation.schedule_mode == "interval" else None,
        )
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
                       and not any(r["corner"] == c.id and r["at"] > now - rotation.cooldown_seconds
                                   for r in state["history"])]
            if not choices:
                raise RotationError("no eligible manual corner outside rolling cooldown")
            chosen = min(choices, key=lambda c: hashlib.sha256(f'{state["seed"]}:{state["slot"]}:{c.id}'.encode()).digest())
            request = dict(corner=chosen.id, selected_at=now, request_id=str(uuid.uuid4()), state_file=path.name)
            state["manual_pending"] = request
            state["slot"] += 1
            state["history"].append(dict(corner=chosen.id, at=now, source="manual-reservation"))
            state["last_slot_at"] = now
            state["next_due_at"] = (now + DAY / len(eligible)
                                    if rotation.schedule_mode == "interval" else now)
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
        selected_adapter = rotation.adapters[chosen.id]
        adapter = SimpleNamespace(
            manager=manager,
            state_path=path,
            run=lambda req: manager.run_rotation(req["request_id"]),
            runtime_environment=getattr(selected_adapter, "runtime_environment", None),
        )
        try:
            result = rotation.executor.execute(adapter, request)
            status = result if isinstance(result, str) else result.status
            state.update(status="waiting", reason="manual-execution-pending")
            if status == "completed":
                finished = timestamp(rotation.clock())
                state["history"].append(dict(corner=chosen.id, at=finished, source="manual-completion"))
                state["last_slot_at"] = max(now, finished)
                if eligible:
                    state["next_due_at"] = (
                        state["last_slot_at"] + DAY / len(eligible)
                        if rotation.schedule_mode == "interval" else finished
                    )
                state.update(status="ready", reason=None)
                state.pop("manual_pending", None)
            elif status not in {"queued", "waiting", "already-running"}:
                state.update(status="recovery_required",
                             reason="manual-execution-unverified",
                             error_kind="execution-unverified")
            rotation.save(state)
            if isinstance(result, str) and hasattr(manager, "state_path"):
                return CornerResult(result, game=chosen.game)
            return result
        except Exception as exc:
            state.update(status="recovery_required",
                         reason="manual-execution-unverified",
                         error_kind=_error_kind(exc))
            rotation.save(state)
            raise


if __name__ == "__main__":
    raise SystemExit(main())

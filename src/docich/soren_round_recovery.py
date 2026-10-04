"""Explicitly abandon one owned Soren round, retaining the failed reservation.

This is an operator transaction, never a timer repair. The interrupted result
is private evidence, not a completed game score. All effects stay behind an
identity-bound durable journal and the existing owner locks.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import copy
import datetime as dt
import fcntl
import json
import math
from pathlib import Path
import time

from .game_switch import GameSwitchStore, atomic_write_json, validate_request_id

JOURNAL = "soren_round_recovery.json"
STAGES = ("prepared", "frozen", "paused", "saved", "stopped", "archived",
          "bridge_starting", "bridge_ready", "runner_starting", "completed")
IDENTITY = ("schema", "request_id", "game", "generation", "deadline_epoch", "deadline_at")


class RecoveryRefused(RuntimeError):
    pass


def recovery_held(state_dir):
    # Malformed/missing-after-unlink residue is not permission. A completed
    # journal also holds rotation until a separately reviewed owner resume.
    path = Path(state_dir) / JOURNAL
    return path.exists() or path.is_symlink()


def read_object(path):
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise RecoveryRefused("required evidence unavailable") from exc
    if not isinstance(value, dict):
        raise RecoveryRefused("required evidence is not an object")
    return value


def stamp(value):
    try:
        parsed = dt.datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError()
        result = parsed.timestamp()
    except (TypeError, ValueError, OverflowError) as exc:
        raise RecoveryRefused("invalid evidence timestamp") from exc
    if not math.isfinite(result) or result < 0:
        raise RecoveryRefused("invalid evidence timestamp")
    return result


@contextmanager
def owner_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RecoveryRefused("owner is busy") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def prove_timeout(state_dir, canonical, rotation, retro, request, ack, now):
    """Accept only a cancelled boundary timeout that retained this exact active."""
    pending = rotation.get("pending")
    if (rotation.get("status") != "recovery_required" or not isinstance(pending, dict)
            or pending.get("phase") != "dispatched" or rotation.get("manual_pending") is not None):
        raise RecoveryRefused("not one held dispatched reservation")
    rid = validate_request_id(pending.get("request_id"))
    active = canonical.get("active")
    if (canonical.get("phase") != "ready" or not isinstance(active, dict)
            or active.get("game") != "sorengame" or active.get("adapter") != "soren"
            or canonical.get("candidate") is not None or canonical.get("previous") is not None
            or canonical.get("retiring") or canonical.get("request_id") is not None
            or canonical.get("operation") is not None):
        raise RecoveryRefused("canonical does not solely own ready Soren")
    receipt = GameSwitchStore(state_dir).receipts.load(rid)
    result = receipt.get("result") if receipt else None
    target = retro.get("game")
    if (not receipt or receipt.get("operation") != "switch" or receipt.get("status") != "failed"
            or receipt.get("target") != target or not isinstance(result, dict)
            or result.get("request_id") != rid or result.get("operation") != "switch"
            or result.get("status") != "failed" or result.get("error_code") != "timeout"
            or result.get("from_game") != "sorengame" or result.get("to_game") != target
            or type(receipt.get("generation")) is not int
            or result.get("generation") != receipt["generation"]
            or receipt["generation"] <= active["generation"]
            or canonical.get("last_result") != result or result.get("cleanup_pending")
            or retro.get("status") != "failed" or retro.get("last_error_code") != "timeout"
            or retro.get("previous_game") != "sorengame"
            or retro.get("rotation_request_id") != rid or retro.get("switch_request_id") != rid
            or target != "hanjuku"):
        raise RecoveryRefused("failed reservation/receipt/active identity mismatch")
    corner = (rotation.get("known_corners") or {}).get(pending.get("corner"))
    if not isinstance(corner, dict) or corner.get("game") != target or corner.get("adapter") != "retro":
        raise RecoveryRefused("pending reservation target attribution missing")
    if not 0 <= now - stamp(retro.get("completed_at")) <= 86400:
        raise RecoveryRefused("completion is stale or in the future")
    if now < rotation.get("last_seen_at", float("inf")):
        raise RecoveryRefused("rotation clock regressed")
    if (type(request.get("schema")) is not int or request["schema"] != 1
            or request.get("request_id") != rid or request.get("game") != "sorengame"
            or type(request.get("generation")) is not int
            or request["generation"] != active["generation"]
            or request.get("operation") is not None or ack.get("status") != "cancelled"
            or any(key not in request or ack.get(key) != request[key] for key in IDENTITY)
            or type(request.get("deadline_epoch")) not in (int, float)
            or not math.isfinite(request["deadline_epoch"])
            or not 0 < request["deadline_epoch"] <= now
            or abs(stamp(request.get("deadline_at")) - request["deadline_epoch"]) > 1):
        raise RecoveryRefused("matching reversible boundary cancellation missing")
    # New receipts carry a durable phase + retained-owner proof. Legacy
    # receipts require the exact coordinator event AND the cancelled broker;
    # generic failed/timeout text alone never authorizes abandonment.
    if result.get("failure_phase") == "round_boundary":
        if result.get("retained_active") != active or result.get("boundary_cancelled") is not True:
            raise RecoveryRefused("retained active proof changed")
    else:
        found = False
        try:
            with (Path(state_dir) / "logs/game_switch.log").open() as handle:
                for line in handle:
                    row = json.loads(line)
                    if (row.get("event") in {"round_boundary_failed", "drain_recovered_timeout"}
                            and row.get("request_id") == rid and row.get("operation") == "switch"
                            and row.get("generation") == receipt["generation"]
                            and row.get("from_game") == "sorengame" and row.get("to_game") == target
                            and row.get("phase") == "ready" and row.get("result") == "failed"
                            and row.get("error_code") == "timeout"
                            and stamp(receipt["created_at"]) <= stamp(row.get("timestamp")) <= now):
                        found = True
        except (OSError, ValueError, AttributeError) as exc:
            raise RecoveryRefused("legacy boundary proof unavailable") from exc
        if not found:
            raise RecoveryRefused("legacy boundary phase was not proved")
    return rid, copy.deepcopy(active), receipt


class OwnedRoundRecovery:
    def __init__(self, state_dir, root, effects, *, clock=time.time):
        self.state_dir, self.root = Path(state_dir), Path(root)
        self.effects, self.clock = effects, clock
        self.store = GameSwitchStore(self.state_dir)
        self.path = self.state_dir / JOURNAL
        self.lifecycle = self.root / "tmp/state/game_lifecycle"

    def _save(self, journal, stage):
        journal["stage"] = stage
        atomic_write_json(self.path, journal)

    def _matches(self, journal, canonical, rotation, retro):
        if (canonical.get("active") != journal["active"]
                or canonical.get("candidate") is not None or canonical.get("previous") is not None
                or canonical.get("retiring") or rotation.get("pending") != journal["pending"]
                or rotation.get("history") != journal["rotation_history"]
                or rotation.get("manual_pending") is not None
                or rotation.get("status") != "recovery_required"
                or retro.get("rotation_request_id") != journal["request_id"]
                or retro.get("switch_request_id") != journal["request_id"]
                or retro.get("completed_at") != journal["completed_at"]):
            raise RecoveryRefused("recovery ownership changed")
        completed = journal["stage"] == "completed"
        if canonical.get("phase") != ("ready" if completed else "recovery_required"):
            # A crash immediately after writing the journal is the sole
            # allowed ready/prepared state. No process effect ran yet.
            if not ((journal["stage"] == "prepared" and canonical.get("phase") == "ready")
                    or (completed and canonical.get("phase") == "recovery_required")):
                raise RecoveryRefused("canonical recovery fence changed")

    def run(self):
        # Match the established rotation -> retro -> coordinator lock order.
        with ExitStack() as locks:
            for name in ("locks/corner-rotation.lock", "locks/retro-corner.lock"):
                locks.enter_context(owner_lock(self.state_dir / name))
            locks.enter_context(self.store.lock(exclusive=True))
            broker_lock = ExitStack()
            locks.enter_context(broker_lock)
            broker_lock.enter_context(owner_lock(self.lifecycle / "broker.lock"))
            canonical, missing = self.store.canonical.load()
            if missing:
                raise RecoveryRefused("canonical missing")
            rotation = read_object(self.state_dir / "corner_rotation.json")
            retro = read_object(self.state_dir / "retro_corner.json")
            if self.path.exists():
                j = read_object(self.path)
                if j.get("schema") != 1 or j.get("stage") not in STAGES:
                    raise RecoveryRefused("invalid recovery journal")
                self._matches(j, canonical, rotation, retro)
                self.effects.verify_archive(j)
                self.effects.target_released(j["target_generation"], j["target"])
                if j["stage"] == "completed":
                    if canonical.get("phase") == "recovery_required":
                        broker_lock.close()
                        self.effects.verify_new(j)
                        self.store.canonical.transition({"recovery_required"}, "ready", updates={"last_error": None})
                    return {"status": "completed", "rotation": "held", "result": "interrupted",
                            "common_workers_changed": j.get("common_workers_changed")}
            else:
                request = read_object(self.lifecycle / "request.json")
                ack = read_object(self.lifecycle / "ack.json")
                rid, active, receipt = prove_timeout(self.state_dir, canonical, rotation, retro,
                                                   request, ack, self.clock())
                resource = self.lifecycle / "game_resource.json"
                control = self.lifecycle / "control.json"
                if control.exists():
                    c = read_object(control)
                    if (any(c.get(k) != request[k] for k in IDENTITY) or c.get("action") != "cancel"):
                        raise RecoveryRefused("boundary control cancellation identity changed")
                if resource.exists():
                    r = read_object(resource)
                    if (any(r.get(k) != request[k] for k in IDENTITY)
                            or r.get("status") != "cancelled" or r.get("irreversible")
                            or r.get("quit_called")):
                        raise RecoveryRefused("resource cancellation is not reversible")
                if self.store.receipts.queued():
                    raise RecoveryRefused("queued coordinator request exists")
                self.effects.target_released(receipt["generation"], receipt["target"])
                inventory = self.effects.preflight()
                j = dict(schema=1, stage="prepared", request_id=rid, active=active,
                         target=receipt["target"], target_generation=receipt["generation"],
                         pending=copy.deepcopy(rotation["pending"]),
                         rotation_history=copy.deepcopy(rotation["history"]),
                         completed_at=retro["completed_at"], lifecycle_request=request,
                         inventory=inventory, started_epoch=self.clock())
                # Durable journal precedes every effect. Existing entry points
                # reject this hold even if the process crashes before fencing.
                self._save(j, "prepared")
            if j["stage"] == "prepared":
                self.store.canonical.transition({"ready", "recovery_required"}, "recovery_required",
                    updates={"last_error": {"error_code": "recovery_required",
                                             "detail": "owned Soren round recovery holds this runtime"}})
            if j["stage"] == "prepared":
                self.effects.freeze(j)
                self._save(j, "frozen")
            if j["stage"] == "frozen":
                self.effects.pause(j)
                self._save(j, "paused")
            if j["stage"] == "paused":
                self.effects.archive(j)
                self._save(j, "saved")
            if j["stage"] == "saved":
                self.effects.verify_archive(j)
                self.effects.stop(j)
                self._save(j, "stopped")
            if j["stage"] == "stopped":
                self.effects.clear_old(j)
                # Preserve the original failure completion time and both IDs.
                retro.update(status="interrupted", end_reason="owned-soren-round-recovery-held")
                atomic_write_json(self.state_dir / "retro_corner.json", retro)
                self._save(j, "archived")
            # New game processes must be able to take the broker lock. All
            # old pinned writers are dead and their records archived first.
            broker_lock.close()
            if j["stage"] == "archived":
                # Journal launch intent before releasing a supervisor gate.
                self._save(j, "bridge_starting")
            if j["stage"] == "bridge_starting":
                self.effects.common_unchanged(j)
                self.effects.start_bridge(j)
                self._save(j, "bridge_ready")
            if j["stage"] == "bridge_ready":
                self._save(j, "runner_starting")
            if j["stage"] == "runner_starting":
                self.effects.start_runner(j)
                self.effects.verify_new(j)
                # Report-only: the game is already restored, so a common worker
                # that restarted on its own must not strand the recovery.
                j["common_workers_changed"] = len(self.effects.common_changed(j))
                # Journal completes first: a crash before the ready commit is
                # recovered by the special completion reconciliation above.
                self._save(j, "completed")
                self.store.canonical.transition({"recovery_required"}, "ready", updates={
                    "last_error": None})
            return {"status": "completed", "rotation": "held", "result": "interrupted",
                    "common_workers_changed": j.get("common_workers_changed")}

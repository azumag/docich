"""Manual TRPG lifecycle and core driver under the shared GameSwitch slot.

GameSwitch owns runtime teardown, rollback and restoration. Only the manager
of the matching active generation advances the session and emits narration.
Chat is consumed read-only; the existing manual-only scheduling is preserved.
"""
from __future__ import annotations

import datetime as dt
import json
import time
import uuid
from pathlib import Path

from .game_switch import (
    ERROR_AGENT_START_FAILED,
    ERROR_PREPARE_FAILED,
    ERROR_QUIESCE_FAILED,
    ERROR_READINESS_TIMEOUT,
    ERROR_ROLLBACK_FAILED,
    ERROR_SOURCE_FENCE_LOST,
    ERROR_START_FAILED,
    ERROR_TIMEOUT,
    GameSwitchBusyError,
    GameSwitchCoordinator,
    GameSwitchError,
    GameSwitchStore,
    TERMINAL_RECEIPT_STATUSES,
    atomic_write_json,
)
from .trpg_view import VIEW_NAME

STATE_FILENAME = "trpg_corner_state.json"
DEFAULT_DURATION_MINUTES = 8
MAX_DURATION_MINUTES = 14
RUNTIME_IDENTITY_KEYS = ("game", "runtime_id", "generation", "lease_id")
PENDING_SWITCH_STATUSES = frozenset({"queued", "in_progress", "busy"})
ROLLBACK_ERROR_CODES = frozenset({
    ERROR_AGENT_START_FAILED,
    ERROR_PREPARE_FAILED,
    ERROR_QUIESCE_FAILED,
    ERROR_READINESS_TIMEOUT,
    ERROR_START_FAILED,
    # The request-wide deadline expiring during the start switch reports
    # ``timeout`` and still runs the switch's own rollback; the rolled-back
    # receipt plus the stable previous owner is the same terminal proof (#2187).
    ERROR_TIMEOUT,
})


class TrpgProgramError(RuntimeError):
    """A fixed, non-sensitive TRPG corner lifecycle failure."""


def _identity(runtime, *, expected_game=None):
    if runtime is None:
        return None
    if not isinstance(runtime, dict):
        raise TrpgProgramError("game-switch runtime identity is invalid")
    identity = {key: runtime.get(key) for key in RUNTIME_IDENTITY_KEYS}
    if (not all(isinstance(identity[key], str) and identity[key] for key in
                ("game", "runtime_id", "lease_id"))
            or type(identity["generation"]) is not int
            or identity["generation"] < 1
            or (expected_game is not None and identity["game"] != expected_game)):
        raise TrpgProgramError("game-switch runtime identity is incomplete")
    return identity


def _stable(canonical):
    phase = canonical.get("phase")
    return (phase in {"idle", "ready"}
            and canonical.get("candidate") is None
            and canonical.get("previous") is None
            and not canonical.get("retiring"))


class TrpgProgramManager:
    """Run one reviewed scenario and restore the displaced runtime."""

    def __init__(self, g, *, duration_minutes=DEFAULT_DURATION_MINUTES, coordinator=None,
                 clock=time.time, sleep=time.sleep, poll_s=1.0, audio_enabled=False):
        if (type(duration_minutes) is not int
                or not 1 <= duration_minutes <= MAX_DURATION_MINUTES):
            raise TrpgProgramError(
                f"trpg program duration must be an integer from 1 to {MAX_DURATION_MINUTES} minutes")
        if type(poll_s) not in (int, float) or poll_s <= 0:
            raise TrpgProgramError("trpg program poll interval must be positive")
        self.g = g
        self.duration_minutes = duration_minutes
        if type(audio_enabled) is not bool:
            raise TrpgProgramError("trpg audio_enabled must be boolean")
        self.audio_enabled = audio_enabled
        self.clock, self.sleep, self.poll_s = clock, sleep, float(poll_s)
        self.path = Path(g.state_dir) / STATE_FILENAME
        self.state_path = self.path
        self.store = GameSwitchStore(g.state_dir)
        if coordinator is None:
            from .adapters import make_coordinator_adapter
            from .stream_category import commit_hook

            announce_running_game = commit_hook(g)

            def announce_after_commit(game):
                # The synthetic view has no reviewed category/title mapping.
                # Keep the displaced game's metadata while it is on screen; the
                # normal hook runs again after restoration.
                if game != VIEW_NAME:
                    announce_running_game(game)

            coordinator = GameSwitchCoordinator(
                self.store,
                lambda spec: make_coordinator_adapter(g, spec),
                post_commit=announce_after_commit,
            )
        self.coordinator = coordinator

    def eligible(self):
        from .trpg_session import scenario, chat_path
        try:
            scenario(self.g)
            chat_path(self.g)
            return True
        except (OSError, ValueError, TypeError, RuntimeError):
            return False

    def _session_tick(self, identity, *, stop_reason=None, request_id=None):
        from .naming import runtime_directory
        from .trpg_session import SESSION_FILE, tick
        path = runtime_directory(self.g.state_dir, identity["runtime_id"]) / SESSION_FILE
        return tick(self.g, path, identity, audio_enabled=self.audio_enabled,
                    now=self.clock(), stop_reason=stop_reason, owner_request_id=request_id)

    def _adopt_rollback(self, state, canonical, previous):
        from .naming import runtime_directory
        from .trpg_session import SESSION_FILE, load
        if (canonical.get("last_result") or {}).get("status") != "rolled_back":
            return None
        try:
            active = _identity(canonical.get("active"), expected_game=VIEW_NAME)
            if active is None:
                return None
            path = runtime_directory(self.g.state_dir, active["runtime_id"]) / SESSION_FILE
            data = load(path, active)
            if (previous not in data["owners"]
                    or data["owner_request_id"] not in (None, state["rotation_request_id"])
                    or data["owner_request_id"] is None and data["state"]["phase"] != "IDLE"):
                return None
        except (OSError, ValueError, TypeError, KeyError):
            return None
        state["trpg_program_runtime_identity"] = active
        self._save(state)
        return active

    def _read_state(self):
        if not self.path.exists():
            return {"schema_version": 1, "status": "idle"}
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise TrpgProgramError("trpg program corner state is unreadable") from exc
        if (not isinstance(state, dict) or state.get("schema_version") != 1
                or not isinstance(state.get("status"), str)):
            raise TrpgProgramError("trpg program corner state is invalid")
        return state

    def _save(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, state)

    def observations(self):
        if not self.path.exists():
            return []
        state = self._read_state()
        if state.get("game", VIEW_NAME) != VIEW_NAME:
            raise TrpgProgramError("trpg program corner state owner is invalid")
        return [state]

    def resources_released(self):
        # GameSwitch owns every view process and browser child; no detached
        # trpg program worker exists.
        return True

    def _canonical(self):
        try:
            with self.store.lock(exclusive=False):
                return self.store.canonical.load()[0]
        except GameSwitchBusyError:
            return None

    @staticmethod
    def _source(canonical):
        if canonical.get("phase") not in {"idle", "ready", "draining"}:
            raise TrpgProgramError("game-switch requires recovery")
        active = canonical.get("active")
        if canonical.get("phase") == "idle":
            if active is not None:
                raise TrpgProgramError("idle game-switch state has an active runtime")
            return None
        if not isinstance(active, dict):
            raise TrpgProgramError("active game-switch state has no runtime")
        return _identity(active)

    @staticmethod
    def _request_id(value):
        try:
            parsed = uuid.UUID(value)
        except (ValueError, TypeError, AttributeError) as exc:
            raise TrpgProgramError("rotation request identity is invalid") from exc
        if str(parsed) != value:
            raise TrpgProgramError("rotation request identity is invalid")
        return value

    def _new_state(self, request_id):
        canonical = self._canonical()
        if canonical is None:
            return None
        source = self._source(canonical)
        if source and source["game"] == VIEW_NAME:
            raise TrpgProgramError("trpg program view has no matching corner owner")
        now = self.clock()
        return {
            "schema_version": 1,
            "game": VIEW_NAME,
            "status": "starting",
            "rotation_request_id": request_id,
            "start_request_id": request_id,
            "switch_request_id": request_id,
            "previous_game": source["game"] if source else None,
            "previous_runtime_identity": source,
            "requested_at": now,
            "starting_at": now,
            "duration_minutes": self.duration_minutes,
        }

    def _mark_interrupted(self, state, reason):
        state.update(status="interrupted", completed_at=self.clock(), end_reason=reason)
        state.pop("switch_request_id", None)
        state.pop("switch_status", None)
        self._save(state)
        self._clear_stop_request()
        return "completed"

    def _clear_stop_request(self):
        from .corner_rotation import clear_rotation_stop_request

        clear_rotation_stop_request(self.g, self.path)

    def _stop_requested(self):
        from .corner_rotation import rotation_stop_requested

        return rotation_stop_requested(self.g, self.path)

    def _dispatch_start(self, state):
        request_id = state.get("start_request_id")
        if not isinstance(request_id, str) or request_id != state.get("rotation_request_id"):
            raise TrpgProgramError(" start request owner is invalid")
        source = state.get("previous_runtime_identity")
        if source is None:
            result = self.coordinator.start(VIEW_NAME, request_id=request_id)
        else:
            result = self.coordinator.switch(
                VIEW_NAME,
                request_id=request_id,
                payload={"expected_source": source},
            )
        status = getattr(result, "status", None)
        if status in PENDING_SWITCH_STATUSES:
            state.update(status="starting", switch_status=status)
            self._save(state)
            return "queued"
        if status != "succeeded":
            state.update(
                status="failed",
                completed_at=self.clock(),
                last_error_code=getattr(result, "error_code", None) or "switch_failed",
            )
            self._save(state)
            raise TrpgProgramError(" view start did not succeed")
        receipt = getattr(result, "receipt", None)
        result_body = receipt.get("result") if isinstance(receipt, dict) else None
        if (not isinstance(receipt, dict)
                or receipt.get("status") != "succeeded"
                or receipt.get("request_id") != request_id
                or receipt.get("target") != VIEW_NAME
                or receipt.get("operation") not in {"start", "switch"}
                or not isinstance(result_body, dict)
                or result_body.get("request_id") != request_id
                or result_body.get("status") != "succeeded"
                or result_body.get("to_game") != VIEW_NAME):
            raise TrpgProgramError(" start receipt identity is invalid")
        identity = _identity(result_body.get("active_runtime"), expected_game=VIEW_NAME)
        if (identity is None
                or getattr(result, "request_id", None) != request_id
                or getattr(result, "to_game", None) != VIEW_NAME
                or getattr(result, "generation", None) != identity["generation"]
                or receipt.get("generation") != identity["generation"]):
            raise TrpgProgramError(" start result identity does not match receipt")
        canonical = self._canonical()
        if canonical is None:
            # The receipt is already terminal and proves the exact candidate
            # identity. Keep the write-ahead owner until canonical state can be
            # read and reconciled against it.
            state.update(status="starting", switch_status="in_progress",
                         starting_runtime_identity=identity)
            self._save(state)
            return "queued"
        active = canonical.get("active")
        if (canonical.get("phase") in {"ready", "draining"}
                and isinstance(active, dict)
                and _identity(active) == identity):
            pass
        elif _stable(canonical):
            return self._mark_interrupted(state, "operator-moved-after-start")
        else:
            state.update(status="starting", switch_status="in_progress",
                         starting_runtime_identity=identity)
            self._save(state)
            return "queued"
        try:
            started = dt.datetime.fromisoformat(
                str(receipt.get("updated_at")).replace("Z", "+00:00")
            ).timestamp()
        except (TypeError, ValueError, OverflowError):
            started = self.clock()
        state.pop("switch_request_id", None)
        state.pop("switch_status", None)
        state.pop("starting_runtime_identity", None)
        state.update(
            status="active",
            trpg_program_runtime_identity=identity,
            started_at=started,
            ends_at=started + self.duration_minutes * 60,
        )
        self._save(state)
        return "queued" if canonical.get("phase") == "draining" else None

    @staticmethod
    def _matches_active(canonical, identity):
        if canonical is None or not isinstance(canonical.get("active"), dict):
            return False
        try:
            actual = _identity(canonical["active"])
        except TrpgProgramError:
            return False
        return actual == identity

    def _wait_and_restore(self, state):
        identity = state.get("trpg_program_runtime_identity")
        if (not isinstance(identity, dict)
                or set(identity) != set(RUNTIME_IDENTITY_KEYS)
                or identity.get("game") != VIEW_NAME):
            raise TrpgProgramError(" runtime owner identity is missing")
        while True:
            if self._stop_requested():
                return self._restore(state, end_reason="manual")
            canonical = self._canonical()
            if canonical is None:
                ends_at = state.get("ends_at")
                wait = self.poll_s if type(ends_at) not in (int, float) else max(0.05, ends_at - self.clock())
                self.sleep(min(self.poll_s, wait))
                continue
            phase = canonical.get("phase")
            if phase == "draining" and self._matches_active(canonical, identity):
                # Another owner has begun a GameSwitch request. Let that durable
                # request finish, then decide from the stable canonical owner.
                return "queued"
            if phase in {"stopping", "starting", "rolling_back"}:
                identities = []
                for key in ("active", "previous", "candidate"):
                    item = canonical.get(key)
                    if isinstance(item, dict):
                        try:
                            identities.append(_identity(item))
                        except TrpgProgramError:
                            continue
                if any(all(item.get(key) == identity[key] for key in ("game", "runtime_id", "generation"))
                       for item in identities):
                    return "queued"
            if not _stable(canonical):
                raise TrpgProgramError(" runtime ownership is in an unsafe switch phase")
            if not self._matches_active(canonical, identity):
                restored = self._adopt_rollback(state, canonical, identity)
                if restored is None:
                    return self._mark_interrupted(state, "operator-moved-during-")
                identity = restored
            ends_at = state.get("ends_at")
            if type(ends_at) not in (int, float) or isinstance(ends_at, bool) or ends_at < 0:
                raise TrpgProgramError(" end time is invalid")
            remaining = ends_at - self.clock()
            if remaining <= 0:
                return self._restore(state, end_reason="duration")
            try:
                if self._session_tick(identity, request_id=state["rotation_request_id"]):
                    return self._restore(state, end_reason="game-completed")
            except (OSError, ValueError, TypeError, KeyError):
                return self._restore(state, end_reason="session-unavailable")
            self.sleep(min(self.poll_s, remaining))

    def _restored_owner_matches(self, state, canonical, result):
        previous = state.get("previous_game")
        if previous is None:
            return (result.get("operation") == "stop"
                    and canonical.get("phase") == "idle"
                    and canonical.get("active") is None
                    and _stable(canonical))
        active = canonical.get("active")
        if (result.get("operation") != "switch"
                or result.get("generation") is None
                or canonical.get("phase") != "ready"
                or not isinstance(active, dict)
                or active.get("game") != previous
                or active.get("generation") != result.get("generation")
                or not _stable(canonical)):
            return False
        try:
            expected = _identity(result.get("active_runtime"), expected_game=previous)
            actual = _identity(active, expected_game=previous)
        except TrpgProgramError:
            return False
        return expected is not None and actual == expected

    def _restore_receipt_finished(self, state, canonical):
        request_id = state.get("restore_request_id")
        if not isinstance(request_id, str):
            return False
        try:
            receipt = self.store.receipts.load(request_id)
        except Exception:
            return False
        if (not isinstance(receipt, dict) or receipt.get("status") != "succeeded"
                or receipt.get("request_id") != request_id):
            return False
        result = receipt.get("result")
        if (not isinstance(result, dict)
                or result.get("from_game") != VIEW_NAME
                or result.get("to_game") != state.get("previous_game")
                or result.get("status") != "succeeded"):
            return False
        return self._restored_owner_matches(state, canonical, result)

    def _restore(self, state, *, end_reason="duration"):
        identity = state.get("trpg_program_runtime_identity")
        if (not isinstance(identity, dict)
                or set(identity) != set(RUNTIME_IDENTITY_KEYS)
                or identity.get("game") != VIEW_NAME):
            raise TrpgProgramError(" runtime owner identity is missing before restore")
        if state.get("status") != "restoring":
            state.update(status="restoring", restore_requested_at=self.clock(), end_reason=end_reason)
            state["restore_request_id"] = str(uuid.uuid4())
            state["switch_request_id"] = state["restore_request_id"]
            self._save(state)
        else:
            end_reason = state.get("end_reason", end_reason)
        request_id = state.get("restore_request_id")
        if not isinstance(request_id, str) or request_id != state.get("switch_request_id"):
            raise TrpgProgramError(" restore request owner is invalid")
        canonical = self._canonical()
        if canonical is None:
            return "queued"
        if _stable(canonical) and not self._matches_active(canonical, identity):
            restored = self._adopt_rollback(state, canonical, identity)
            if restored is not None:
                identity = restored
        if self._restore_receipt_finished(state, canonical):
            state.update(status="completed", completed_at=self.clock(), end_reason=end_reason,
                         restored_runtime_identity=(
                             _identity(canonical["active"])
                             if isinstance(canonical.get("active"), dict) else None
                         ))
            state.pop("switch_request_id", None)
            state.pop("switch_status", None)
            self._save(state)
            self._clear_stop_request()
            return "completed"
        if canonical.get("phase") == "draining" and self._matches_active(canonical, identity):
            pass  # GameSwitch accepts a durable request behind the active drain.
        elif not (_stable(canonical) and self._matches_active(canonical, identity)):
            if _stable(canonical):
                return self._mark_interrupted(state, "operator-moved-before-restore")
            raise TrpgProgramError(" restore source is not the owned runtime")

        if end_reason in {"manual", "duration"}:
            try:
                self._session_tick(identity, stop_reason=end_reason, request_id=state["rotation_request_id"])
            except (OSError, ValueError, TypeError, KeyError):
                state["last_error_code"] = "trpg-session-unavailable"
        previous = state.get("previous_game")
        payload = {"expected_source": identity}
        if previous is None:
            result = self.coordinator.stop(request_id=request_id, payload=payload)
        else:
            result = self.coordinator.switch(previous, request_id=request_id, payload=payload)
        status = getattr(result, "status", None)
        if status in PENDING_SWITCH_STATUSES:
            state.update(status="restoring", switch_status=status)
            self._save(state)
            return "queued"
        if status == "failed" and getattr(result, "error_code", None) == ERROR_SOURCE_FENCE_LOST:
            return self._mark_interrupted(state, "operator-moved-before-restore")
        if status != "succeeded":
            state.update(status="restoring", last_error_code=getattr(result, "error_code", None))
            self._save(state)
            raise TrpgProgramError(" runtime restore did not succeed")
        canonical = self._canonical()
        if canonical is None:
            state.update(status="restoring", switch_status="queued")
            self._save(state)
            return "queued"
        receipt = getattr(result, "receipt", None)
        result_body = receipt.get("result") if isinstance(receipt, dict) else None
        if (not isinstance(result_body, dict)
                or result_body.get("from_game") != VIEW_NAME
                or result_body.get("to_game") != previous
                or not self._restored_owner_matches(state, canonical, result_body)):
            raise TrpgProgramError(" restore result has no matching canonical owner")
        restored = _identity(canonical["active"]) if isinstance(canonical.get("active"), dict) else None
        state.update(status="completed", completed_at=self.clock(), end_reason=end_reason,
                     restored_runtime_identity=restored)
        state.pop("switch_request_id", None)
        state.pop("switch_status", None)
        self._save(state)
        self._clear_stop_request()
        return "completed"

    def run_rotation(self, request_id):
        request_id = self._request_id(request_id)
        state = self._read_state()
        if state.get("rotation_request_id") == request_id:
            status = state.get("status")
            if status in {"completed", "interrupted"}:
                return "completed"
            if status == "starting":
                if self._stop_requested():
                    switch_request_id = state.get("switch_request_id")
                    if (not isinstance(switch_request_id, str)
                            or switch_request_id != state.get("start_request_id")):
                        raise TrpgProgramError(" start owner has no matching switch request")
                    try:
                        receipt = self.store.receipts.load(switch_request_id)
                    except Exception:
                        raise TrpgProgramError(" start receipt cannot be verified") from None
                    if receipt is None:
                        # A stop marker can cancel a write-ahead reservation only
                        # before GameSwitch has durably accepted it.
                        return self._mark_interrupted(state, "operator-stopped-before-start")
                    # Accepted/queued/succeeded receipts are durable owners:
                    # replay the same request id instead of starting a new one.
                result = self._dispatch_start(state)
                if result is not None:
                    return result
            elif status == "active":
                return self._wait_and_restore(state)
            elif status == "restoring":
                return self._restore(state)
            else:
                raise TrpgProgramError(" rotation owner needs recovery")
        else:
            if state.get("status") not in {"idle", "completed", "interrupted"}:
                raise TrpgProgramError("another  execution owns the state")
            state = self._new_state(request_id)
            if state is None:
                return "queued"
            if self._stop_requested():
                return self._mark_interrupted(state, "operator-stopped-before-start")
            self._save(state)
            result = self._dispatch_start(state)
            if result is not None:
                return result
        return self._wait_and_restore(state)

    def reconcile_failed_start(self, request_id):
        """Commit only a terminal receipt that proves this view never started."""
        try:
            request_id = self._request_id(request_id)
            with self.store.lock(exclusive=False):
                state = self._read_state()
                if (state.get("status") not in {"starting", "failed"}
                        or state.get("rotation_request_id") != request_id
                        or state.get("start_request_id") != request_id
                        or state.get("switch_request_id") != request_id):
                    return False
                receipt = self.store.receipts.load(request_id)
                canonical, missing = self.store.canonical.load()
                if receipt is None or missing:
                    return False
                result = receipt.get("result")
                previous = state.get("previous_runtime_identity")
                previous_game = state.get("previous_game")
                if (receipt.get("operation") not in {"start", "switch"}
                        or receipt.get("target") != VIEW_NAME
                        or receipt.get("request_id") != request_id
                        or not isinstance(result, dict)
                        or result.get("request_id") != request_id
                        or result.get("to_game") != VIEW_NAME
                        or receipt.get("status") not in {"failed", "rolled_back"}):
                    return False
                code = result.get("error_code")
                if state.get("status") == "failed" and state.get("last_error_code") != code:
                    return False
                if receipt.get("status") == "rolled_back":
                    restored_generation = result.get("restored_generation")
                    if (result.get("status") != "rolled_back"
                            or result.get("from_game") != previous_game
                            or code not in ROLLBACK_ERROR_CODES
                            or type(restored_generation) is not int
                            or result.get("generation") != receipt.get("generation")
                            or result.get("cleanup_pending") not in (None, False)
                            or canonical.get("phase") != "ready"
                            or not isinstance(canonical.get("active"), dict)
                            or canonical["active"].get("game") != previous_game
                            or type(canonical["active"].get("generation")) is not int
                            or canonical["active"]["generation"] < restored_generation
                            or canonical.get("candidate") is not None
                            or canonical.get("previous") is not None
                            or canonical.get("retiring")):
                        return False
                    _identity(canonical["active"], expected_game=previous_game)
                elif code == ERROR_SOURCE_FENCE_LOST:
                    if not _stable(canonical):
                        return False
                    active = canonical.get("active")
                    if isinstance(active, dict) and active.get("game") == VIEW_NAME:
                        return False
                    if previous is None:
                        if previous_game is not None:
                            return False
                    elif not isinstance(previous, dict):
                        return False
                elif code == ERROR_QUIESCE_FAILED:
                    if (not _stable(canonical)
                            or previous is None
                            or not isinstance(canonical.get("active"), dict)
                            or _identity(canonical["active"]) != previous):
                        return False
                elif code == ERROR_ROLLBACK_FAILED:
                    # The start failed *and* its own rollback failed. Accept a
                    # terminal ``failed`` receipt only together with canonical
                    # state that proves the previous owner finished restoring.
                    if (result.get("status") != "failed"
                            or result.get("from_game") not in (None, previous_game)
                            or previous_game is None
                            or not _stable(canonical)
                            or not isinstance(canonical.get("active"), dict)
                            or canonical["active"].get("game") != previous_game
                            or type(canonical["active"].get("generation")) is not int
                            or canonical["active"].get("generation") < 1):
                        return False
                    _identity(canonical["active"], expected_game=previous_game)
                    if isinstance(previous, dict):
                        prior_generation = previous.get("generation")
                        if (type(prior_generation) is not int
                                or canonical["active"]["generation"] < prior_generation):
                            return False
                else:
                    return False
                state.update(status="interrupted",
                             completed_at=state.get("completed_at", self.clock()),
                             end_reason="switch-terminal-before-corner-active")
                state.pop("switch_request_id", None)
                state.pop("switch_status", None)
                self._save(state)
                self._clear_stop_request()
                return True
        except (OSError, ValueError, GameSwitchBusyError, TrpgProgramError):
            return False

    def settle_released_failure(self, request_id):
        """Retire an old failed owner after rotation proved its slot released.

        A released failure remains in the rotation audit, but the single
        corner state file must become terminal before a later request can use
        it. Recheck the shared plane under its lock because the rotation's
        release proof and this write are separate operations.
        """
        try:
            request_id = self._request_id(request_id)
            with self.store.lock(exclusive=True):
                state = self._read_state()
                if (state.get("status") != "failed"
                        or state.get("rotation_request_id") != request_id
                        or state.get("started_at") is not None
                        or state.get("trpg_program_runtime_identity") is not None):
                    return False
                canonical, missing = self.store.canonical.load()
                if missing or not _stable(canonical):
                    return False
                if (canonical.get("operation") is not None
                        or canonical.get("request_id") is not None
                        or canonical.get("deadline_at") is not None):
                    return False
                active = _identity(canonical.get("active"))
                if active is None or active["game"] == VIEW_NAME:
                    return False
                receipt = self.store.receipts.load(request_id)
                if (isinstance(receipt, dict)
                        and receipt.get("status") not in TERMINAL_RECEIPT_STATUSES):
                    return False
                queued = self.store.receipts.queued()
                if not isinstance(queued, list) or any(
                        isinstance(row, dict) and row.get("request_id") == request_id
                        for row in queued):
                    return False
                state.update(status="interrupted",
                             completed_at=state.get("completed_at") or self.clock(),
                             end_reason="released-failure-before-retry")
                self._save(state)
                self._clear_stop_request()
                return True
        except (OSError, ValueError, GameSwitchError, TrpgProgramError):
            return False

    def stop(self):
        """Request a durable early finish; the running owner performs restore."""
        from .corner_catalog import rotation_enabled
        from .corner_rotation import stop_manual

        def request_stop():
            state = self._read_state()
            if state.get("status") not in {"starting", "active", "restoring"}:
                return "not-active"
            from .corner_rotation import STOP_REQUEST_DIR
            path = Path(self.g.state_dir) / STOP_REQUEST_DIR / self.path.name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            atomic_write_json(path, {"state_file": self.path.name, "requested_at": self.clock()})
            return "queued"

        if rotation_enabled(self.g):
            return stop_manual(self.g, self, request_stop)
        return request_stop()

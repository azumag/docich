"""One-match Tsuitate corner lifecycle under the shared GameSwitch slot."""
from __future__ import annotations

import datetime as dt
import json
import time
import uuid
from pathlib import Path

from .game_switch import (
    ERROR_AGENT_START_FAILED,
    ERROR_QUIESCE_FAILED,
    ERROR_READINESS_TIMEOUT,
    ERROR_SOURCE_FENCE_LOST,
    ERROR_START_FAILED,
    GameSwitchBusyError,
    GameSwitchCoordinator,
    GameSwitchStore,
    atomic_write_json,
)
from .tsuitate_beta_control import ControlError, call_beta_control
from .tsuitate_view import VIEW_NAME

STATE_FILENAME = "tsuitate_corner.json"
RUNTIME_IDENTITY_KEYS = ("game", "runtime_id", "generation", "lease_id")
PENDING_SWITCH_STATUSES = frozenset({"queued", "in_progress", "busy"})
ROLLBACK_ERROR_CODES = frozenset({
    ERROR_AGENT_START_FAILED,
    ERROR_QUIESCE_FAILED,
    ERROR_READINESS_TIMEOUT,
    ERROR_START_FAILED,
})
BETA_ACTIVE = frozenset({"queued", "playing", "draining"})
BETA_TERMINAL = frozenset({"stopped", "finished", "queue_timeout"})


class TsuitateCornerError(RuntimeError):
    pass


def _identity(runtime, *, expected_game=None):
    if runtime is None:
        return None
    if not isinstance(runtime, dict):
        raise TsuitateCornerError("game-switch runtime identity is invalid")
    identity = {key: runtime.get(key) for key in RUNTIME_IDENTITY_KEYS}
    if (
        not all(isinstance(identity[key], str) and identity[key]
                for key in ("game", "runtime_id", "lease_id"))
        or type(identity["generation"]) is not int
        or identity["generation"] < 1
        or (expected_game is not None and identity["game"] != expected_game)
    ):
        raise TsuitateCornerError("game-switch runtime identity is incomplete")
    return identity


def _stable(canonical):
    return (
        canonical.get("phase") in {"idle", "ready"}
        and canonical.get("candidate") is None
        and canonical.get("previous") is None
        and not canonical.get("retiring")
    )


class TsuitateCornerManager:
    def __init__(self, g, *, coordinator=None, control=None,
                 clock=time.time, sleep=time.sleep, poll_s=1.0):
        self.g = g
        self.clock, self.sleep, self.poll_s = clock, sleep, float(poll_s)
        self.path = Path(g.state_dir) / STATE_FILENAME
        self.state_path = self.path
        self.store = GameSwitchStore(g.state_dir)
        self.control = control or call_beta_control
        if coordinator is None:
            from .adapters import make_coordinator_adapter
            from .stream_category import commit_hook
            coordinator = GameSwitchCoordinator(
                self.store,
                lambda spec: make_coordinator_adapter(g, spec),
                post_commit=commit_hook(g),
            )
        self.coordinator = coordinator

    def eligible(self):
        try:
            status = self.control("status")
        except ControlError:
            return False
        return (
            status.get("readyForNextRun") is True
            and status.get("state") in {"stopped", "finished", "queue_timeout"}
        )

    def _read_state(self):
        if not self.path.exists():
            return {"schema_version": 1, "status": "idle"}
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise TsuitateCornerError("tsuitate corner state is unreadable") from exc
        if (
            not isinstance(state, dict)
            or state.get("schema_version") != 1
            or not isinstance(state.get("status"), str)
        ):
            raise TsuitateCornerError("tsuitate corner state is invalid")
        return state

    def _save(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, state)

    def observations(self):
        if not self.path.exists():
            return []
        state = self._read_state()
        if state.get("game", VIEW_NAME) != VIEW_NAME:
            raise TsuitateCornerError("tsuitate corner state owner is invalid")
        return [state]

    def resources_released(self):
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
            raise TsuitateCornerError("game-switch requires recovery")
        active = canonical.get("active")
        if canonical.get("phase") == "idle":
            if active is not None:
                raise TsuitateCornerError("idle game-switch state has an active runtime")
            return None
        if not isinstance(active, dict):
            raise TsuitateCornerError("active game-switch state has no runtime")
        return _identity(active)

    @staticmethod
    def _request_id(value):
        try:
            parsed = uuid.UUID(value)
        except (ValueError, TypeError, AttributeError) as exc:
            raise TsuitateCornerError("rotation request identity is invalid") from exc
        if str(parsed) != value:
            raise TsuitateCornerError("rotation request identity is invalid")
        return value

    def _new_state(self, request_id):
        canonical = self._canonical()
        if canonical is None:
            return None
        source = self._source(canonical)
        if source and source["game"] == VIEW_NAME:
            raise TsuitateCornerError("tsuitate view has no matching corner owner")
        return {
            "schema_version": 1,
            "game": VIEW_NAME,
            "status": "starting",
            "rotation_request_id": request_id,
            "start_request_id": request_id,
            "switch_request_id": request_id,
            "previous_game": source["game"] if source else None,
            "previous_runtime_identity": source,
            "requested_at": self.clock(),
            "starting_at": self.clock(),
        }

    def _clear_stop_request(self):
        from .corner_rotation import clear_rotation_stop_request
        clear_rotation_stop_request(self.g, self.path)

    def _stop_requested(self):
        from .corner_rotation import rotation_stop_requested
        return rotation_stop_requested(self.g, self.path)

    def _mark_interrupted(self, state, reason):
        state.update(status="interrupted", completed_at=self.clock(), end_reason=reason)
        state.pop("switch_request_id", None)
        state.pop("switch_status", None)
        self._save(state)
        self._clear_stop_request()
        return "completed"

    @staticmethod
    def _matches_active(canonical, identity):
        if canonical is None or not isinstance(canonical.get("active"), dict):
            return False
        try:
            return _identity(canonical["active"]) == identity
        except TsuitateCornerError:
            return False

    def _dispatch_view_start(self, state):
        request_id = state.get("start_request_id")
        if not isinstance(request_id, str) or request_id != state.get("rotation_request_id"):
            raise TsuitateCornerError("tsuitate start request owner is invalid")
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
            raise TsuitateCornerError("tsuitate view start did not succeed")
        receipt = getattr(result, "receipt", None)
        body = receipt.get("result") if isinstance(receipt, dict) else None
        if (
            not isinstance(receipt, dict)
            or receipt.get("status") != "succeeded"
            or receipt.get("request_id") != request_id
            or receipt.get("target") != VIEW_NAME
            or receipt.get("operation") not in {"start", "switch"}
            or not isinstance(body, dict)
            or body.get("request_id") != request_id
            or body.get("status") != "succeeded"
            or body.get("to_game") != VIEW_NAME
        ):
            raise TsuitateCornerError("tsuitate start receipt identity is invalid")
        identity = _identity(body.get("active_runtime"), expected_game=VIEW_NAME)
        canonical = self._canonical()
        if canonical is None:
            state.update(
                status="starting",
                switch_status="in_progress",
                starting_runtime_identity=identity,
            )
            self._save(state)
            return "queued"
        if not (
            canonical.get("phase") in {"ready", "draining"}
            and self._matches_active(canonical, identity)
        ):
            if _stable(canonical):
                return self._mark_interrupted(state, "operator-moved-after-start")
            state.update(
                status="starting",
                switch_status="in_progress",
                starting_runtime_identity=identity,
            )
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
            view_runtime_identity=identity,
            started_at=started,
            beta_started=False,
        )
        self._save(state)
        return self._ensure_beta_started(state)

    def _control_status(self, state):
        try:
            status = self.control("status")
        except ControlError as exc:
            state["last_error_code"] = exc.code
            self._save(state)
            return None
        for key in ("state", "brainVersion", "completedGames", "reservedGames",
                    "stopRequested", "readyForNextRun"):
            state["beta_" + key] = status.get(key)
        state["beta_run_matches"] = status.get("runId") == state.get("rotation_request_id")
        state.pop("last_error_code", None)
        self._save(state)
        return status

    def _ensure_beta_started(self, state):
        request_id = state.get("rotation_request_id")
        status = self._control_status(state)
        if status is None:
            return "queued"
        if status.get("runId") == request_id:
            state["beta_started"] = True
            self._save(state)
            return None
        if status.get("readyForNextRun") is not True:
            raise TsuitateCornerError("another beta run owns the singleton")
        try:
            started = self.control("start", request_id)
        except ControlError as exc:
            # The command response can be ambiguous.  Do not issue a new run:
            # the next tick first queries status for this exact runId.
            state["last_error_code"] = exc.code
            self._save(state)
            return "queued"
        if started.get("runId") != request_id or started.get("state") not in (
            BETA_ACTIVE | BETA_TERMINAL | {"paused"}
        ):
            raise TsuitateCornerError("beta start response has no matching run owner")
        state.update(
            beta_started=True,
            beta_state=started.get("state"),
            beta_brainVersion=started.get("brainVersion"),
        )
        state.pop("last_error_code", None)
        self._save(state)
        return None

    def _request_beta_stop(self, state):
        if state.get("beta_stop_requested") is True:
            return True
        request_id = state.get("rotation_request_id")
        try:
            response = self.control("stop", request_id)
        except ControlError as exc:
            state["last_error_code"] = exc.code
            self._save(state)
            return False
        if response.get("runId") not in {None, request_id}:
            raise TsuitateCornerError("beta stop response belongs to another run")
        state.update(beta_stop_requested=True, beta_stop_requested_at=self.clock())
        self._save(state)
        return True

    def _wait_and_restore(self, state):
        identity = state.get("view_runtime_identity")
        if (
            not isinstance(identity, dict)
            or set(identity) != set(RUNTIME_IDENTITY_KEYS)
            or identity.get("game") != VIEW_NAME
        ):
            raise TsuitateCornerError("tsuitate runtime owner identity is missing")
        while True:
            canonical = self._canonical()
            if canonical is None:
                return "queued"
            phase = canonical.get("phase")
            if phase in {"stopping", "starting", "rolling_back", "draining"}:
                identities = []
                for key in ("active", "previous", "candidate"):
                    item = canonical.get(key)
                    if isinstance(item, dict):
                        try:
                            identities.append(_identity(item))
                        except TsuitateCornerError:
                            pass
                if identity in identities:
                    return "queued"
            elif _stable(canonical) and not self._matches_active(canonical, identity):
                state["view_lost"] = True
                state["end_reason"] = "operator-moved-during-tsuitate"
                self._save(state)
            elif not _stable(canonical):
                raise TsuitateCornerError("tsuitate runtime ownership is in an unsafe switch phase")

            should_stop = self._stop_requested() or state.get("view_lost") is True

            # A start response may have been ambiguous. Always observe the
            # singleton before deciding whether this corner owns a beta run.
            # In particular, a stop/view-loss request must never manufacture a
            # new match merely so that it can be stopped immediately afterward.
            if state.get("beta_started") is not True:
                status = self._control_status(state)
                if status is None:
                    return "queued"
                if status.get("runId") == state.get("rotation_request_id"):
                    state["beta_started"] = True
                    self._save(state)
                elif should_stop:
                    if state.get("view_lost") is True:
                        return self._mark_interrupted(
                            state, state.get("end_reason", "operator-moved-during-tsuitate")
                        )
                    return self._restore(state, end_reason="manual")
                else:
                    pending = self._ensure_beta_started(state)
                    if pending is not None:
                        return pending

            if should_stop and not self._request_beta_stop(state):
                return "queued"

            status = self._control_status(state)
            if status is None:
                return "queued"
            if status.get("runId") != state.get("rotation_request_id"):
                raise TsuitateCornerError("beta status belongs to another run")
            beta_state = status.get("state")
            state["beta_state"] = beta_state
            self._save(state)

            if beta_state == "paused":
                # terminal_unconfirmed/recovery is intentionally owner-driven.
                # Existing WebUI reconcile may resolve it; until then this
                # corner remains busy and no next corner starts.
                return "queued"
            if beta_state in BETA_ACTIVE:
                self.sleep(self.poll_s)
                continue
            if beta_state not in BETA_TERMINAL:
                raise TsuitateCornerError("beta returned an unknown lifecycle state")

            reason = {
                "finished": "game-completed",
                "queue_timeout": "queue-timeout",
                "stopped": "manual" if should_stop else "beta-stopped",
            }[beta_state]
            if state.get("view_lost") is True:
                return self._mark_interrupted(state, state.get("end_reason", "operator-moved"))
            return self._restore(state, end_reason=reason)

    def _restored_owner_matches(self, state, canonical, result):
        previous = state.get("previous_game")
        if previous is None:
            return (
                result.get("operation") == "stop"
                and canonical.get("phase") == "idle"
                and canonical.get("active") is None
                and _stable(canonical)
            )
        active = canonical.get("active")
        if (
            result.get("operation") != "switch"
            or result.get("generation") is None
            or canonical.get("phase") != "ready"
            or not isinstance(active, dict)
            or active.get("game") != previous
            or active.get("generation") != result.get("generation")
            or not _stable(canonical)
        ):
            return False
        try:
            expected = _identity(result.get("active_runtime"), expected_game=previous)
            actual = _identity(active, expected_game=previous)
        except TsuitateCornerError:
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
        if (
            not isinstance(receipt, dict)
            or receipt.get("status") != "succeeded"
            or receipt.get("request_id") != request_id
        ):
            return False
        result = receipt.get("result")
        if (
            not isinstance(result, dict)
            or result.get("from_game") != VIEW_NAME
            or result.get("to_game") != state.get("previous_game")
            or result.get("status") != "succeeded"
        ):
            return False
        return self._restored_owner_matches(state, canonical, result)

    def _restore(self, state, *, end_reason):
        identity = state.get("view_runtime_identity")
        if (
            not isinstance(identity, dict)
            or set(identity) != set(RUNTIME_IDENTITY_KEYS)
            or identity.get("game") != VIEW_NAME
        ):
            raise TsuitateCornerError("tsuitate runtime owner identity is missing before restore")
        if state.get("status") != "restoring":
            state.update(
                status="restoring",
                restore_requested_at=self.clock(),
                end_reason=end_reason,
            )
            state["restore_request_id"] = str(uuid.uuid4())
            state["switch_request_id"] = state["restore_request_id"]
            self._save(state)
        else:
            end_reason = state.get("end_reason", end_reason)
        request_id = state.get("restore_request_id")
        if not isinstance(request_id, str) or request_id != state.get("switch_request_id"):
            raise TsuitateCornerError("tsuitate restore request owner is invalid")
        canonical = self._canonical()
        if canonical is None:
            return "queued"
        if self._restore_receipt_finished(state, canonical):
            state.update(
                status="completed",
                completed_at=self.clock(),
                end_reason=end_reason,
                restored_runtime_identity=(
                    _identity(canonical["active"])
                    if isinstance(canonical.get("active"), dict) else None
                ),
            )
            state.pop("switch_request_id", None)
            state.pop("switch_status", None)
            self._save(state)
            self._clear_stop_request()
            return "completed"
        if canonical.get("phase") == "draining" and self._matches_active(canonical, identity):
            pass
        elif not (_stable(canonical) and self._matches_active(canonical, identity)):
            if _stable(canonical):
                return self._mark_interrupted(state, "operator-moved-before-restore")
            raise TsuitateCornerError("tsuitate restore source is not the owned runtime")

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
            raise TsuitateCornerError("tsuitate runtime restore did not succeed")
        canonical = self._canonical()
        if canonical is None:
            state.update(status="restoring", switch_status="queued")
            self._save(state)
            return "queued"
        receipt = getattr(result, "receipt", None)
        body = receipt.get("result") if isinstance(receipt, dict) else None
        if (
            not isinstance(body, dict)
            or body.get("from_game") != VIEW_NAME
            or body.get("to_game") != previous
            or not self._restored_owner_matches(state, canonical, body)
        ):
            raise TsuitateCornerError("tsuitate restore result has no matching canonical owner")
        state.update(
            status="completed",
            completed_at=self.clock(),
            end_reason=end_reason,
            restored_runtime_identity=(
                _identity(canonical["active"])
                if isinstance(canonical.get("active"), dict) else None
            ),
        )
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
                    return self._mark_interrupted(state, "operator-stopped-before-start")
                result = self._dispatch_view_start(state)
                if result is not None:
                    return result
            elif status == "active":
                return self._wait_and_restore(state)
            elif status == "restoring":
                return self._restore(state, end_reason=state.get("end_reason", "game-completed"))
            else:
                raise TsuitateCornerError("tsuitate rotation owner needs recovery")
        else:
            if state.get("status") not in {"idle", "completed", "interrupted"}:
                raise TsuitateCornerError("another tsuitate execution owns the state")
            state = self._new_state(request_id)
            if state is None:
                return "queued"
            if self._stop_requested():
                return self._mark_interrupted(state, "operator-stopped-before-start")
            self._save(state)
            result = self._dispatch_view_start(state)
            if result is not None:
                return result
        return self._wait_and_restore(state)

    def reconcile_failed_start(self, request_id):
        try:
            request_id = self._request_id(request_id)
            with self.store.lock(exclusive=False):
                state = self._read_state()
                if (
                    state.get("status") not in {"starting", "failed"}
                    or state.get("rotation_request_id") != request_id
                    or state.get("start_request_id") != request_id
                    or state.get("switch_request_id") != request_id
                ):
                    return False
                receipt = self.store.receipts.load(request_id)
                canonical, missing = self.store.canonical.load()
                if receipt is None or missing:
                    return False
                result = receipt.get("result")
                previous = state.get("previous_runtime_identity")
                previous_game = state.get("previous_game")
                if (
                    receipt.get("operation") not in {"start", "switch"}
                    or receipt.get("target") != VIEW_NAME
                    or receipt.get("request_id") != request_id
                    or not isinstance(result, dict)
                    or result.get("request_id") != request_id
                    or result.get("to_game") != VIEW_NAME
                    or receipt.get("status") not in {"failed", "rolled_back"}
                ):
                    return False
                code = result.get("error_code")
                if state.get("status") == "failed" and state.get("last_error_code") != code:
                    return False
                if receipt.get("status") == "rolled_back":
                    restored_generation = result.get("restored_generation")
                    if (
                        result.get("status") != "rolled_back"
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
                        or canonical.get("retiring")
                    ):
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
                    if (
                        not _stable(canonical)
                        or previous is None
                        or not isinstance(canonical.get("active"), dict)
                        or _identity(canonical["active"]) != previous
                    ):
                        return False
                else:
                    return False
                state.update(
                    status="interrupted",
                    completed_at=state.get("completed_at", self.clock()),
                    end_reason="switch-terminal-before-corner-active",
                )
                state.pop("switch_request_id", None)
                state.pop("switch_status", None)
                self._save(state)
                self._clear_stop_request()
                return True
        except (OSError, ValueError, GameSwitchBusyError, TsuitateCornerError):
            return False

    def stop(self):
        from .corner_catalog import rotation_enabled
        from .corner_rotation import stop_manual

        def request_stop():
            state = self._read_state()
            if state.get("status") not in {"starting", "active", "restoring"}:
                return "not-active"
            from .corner_rotation import STOP_REQUEST_DIR
            path = Path(self.g.state_dir) / STOP_REQUEST_DIR / self.path.name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            atomic_write_json(
                path,
                {"state_file": self.path.name, "requested_at": self.clock()},
            )
            return "queued"

        if rotation_enabled(self.g):
            return stop_manual(self.g, self, request_stop)
        return request_stop()

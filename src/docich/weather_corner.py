"""Opt-in lifecycle owner for the read-only JMA program view.

Scheduling and shared program-slot arbitration stay in corner_rotation and
corner_adapters. GameSwitch owns boundary waits, runtime teardown, rollback,
and restoration; this module persists the weather corner's request and full
runtime identity, plus an opt-in shared-audio item plan. An opt-in publisher
refreshes the forecast before a new execution; this module never generates speech.
"""
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
from .weather import WeatherError, narration
from .weather_audio import (
    MAX_ITEM_INDEX, WeatherAudioError, build_weather_audio_request,
    item_idempotency_key, request_fingerprint, runtime_identity_matches,
    validate_weather_audio_receipt, validate_weather_audio_request,
)
from .weather_view import read_view

WEATHER_VIEW_NAME = "weather-view"
STATE_FILENAME = "weather_corner.json"
RUNTIME_IDENTITY_KEYS = ("game", "runtime_id", "generation", "lease_id")
PENDING_SWITCH_STATUSES = frozenset({"queued", "in_progress", "busy"})
ROLLBACK_ERROR_CODES = frozenset({
    ERROR_AGENT_START_FAILED,
    ERROR_QUIESCE_FAILED,
    ERROR_READINESS_TIMEOUT,
    ERROR_START_FAILED,
})


class WeatherCornerError(RuntimeError):
    """A fixed, non-sensitive weather corner lifecycle failure."""


def _identity(runtime, *, expected_game=None):
    if runtime is None:
        return None
    if not isinstance(runtime, dict):
        raise WeatherCornerError("game-switch runtime identity is invalid")
    identity = {key: runtime.get(key) for key in RUNTIME_IDENTITY_KEYS}
    if (not all(isinstance(identity[key], str) and identity[key] for key in
                ("game", "runtime_id", "lease_id"))
            or type(identity["generation"]) is not int
            or identity["generation"] < 1
            or (expected_game is not None and identity["game"] != expected_game)):
        raise WeatherCornerError("game-switch runtime identity is incomplete")
    return identity


def _stable(canonical):
    phase = canonical.get("phase")
    return (phase in {"idle", "ready"}
            and canonical.get("candidate") is None
            and canonical.get("previous") is None
            and not canonical.get("retiring"))


class WeatherCornerManager:
    """Run one weather display under an existing GameSwitch/program slot."""

    def __init__(self, g, *, duration_minutes=None, coordinator=None,
                 audio_enabled=False, audio_port=None, forecast_refresh=None,
                 clock=time.time, sleep=time.sleep, poll_s=1.0):
        if duration_minutes is not None and (
                type(duration_minutes) is not int or not 1 <= duration_minutes <= 14):
            raise WeatherCornerError("weather duration must be an integer from 1 to 14 minutes")
        if type(poll_s) not in (int, float) or poll_s <= 0:
            raise WeatherCornerError("weather poll interval must be positive")
        if type(audio_enabled) is not bool:
            raise WeatherCornerError("weather audio setting must be boolean")
        if audio_enabled and audio_port is None:
            raise WeatherCornerError("weather audio consumer is unavailable")
        self.g = g
        self.duration_minutes = duration_minutes
        self.audio_enabled = audio_enabled
        self.audio_port = audio_port
        self.forecast_refresh = forecast_refresh
        self.clock, self.sleep, self.poll_s = clock, sleep, float(poll_s)
        self.path = Path(g.state_dir) / STATE_FILENAME
        self.state_path = self.path
        self.snapshot_path = Path(g.state_dir) / "weather" / "snapshot.json"
        self.store = GameSwitchStore(g.state_dir)
        if coordinator is None:
            from .adapters import make_coordinator_adapter
            from .stream_category import commit_hook

            announce_running_game = commit_hook(g)

            def announce_after_commit(game):
                # The synthetic view has no reviewed category/title mapping.
                # Keep the displaced game's metadata untouched while it is
                # on screen; the normal hook runs again after restoration.
                if game != WEATHER_VIEW_NAME:
                    announce_running_game(game)

            coordinator = GameSwitchCoordinator(
                self.store,
                lambda spec: make_coordinator_adapter(g, spec),
                post_commit=announce_after_commit,
            )
        self.coordinator = coordinator

    def eligible(self):
        if self.duration_minutes is None:
            return False
        if self.forecast_refresh is not None:
            # Fetch only once selected, never during eligibility/status polling.
            return True
        try:
            read_view(self.snapshot_path)
        except (OSError, WeatherError):
            return False
        return True

    def _read_state(self):
        if not self.path.exists():
            return {"schema_version": 1, "status": "idle"}
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise WeatherCornerError("weather corner state is unreadable") from exc
        if (not isinstance(state, dict) or state.get("schema_version") != 1
                or not isinstance(state.get("status"), str)):
            raise WeatherCornerError("weather corner state is invalid")
        return state

    def _save(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, state)

    def observations(self):
        if not self.path.exists():
            return []
        state = self._read_state()
        if state.get("game", WEATHER_VIEW_NAME) != WEATHER_VIEW_NAME:
            raise WeatherCornerError("weather corner state owner is invalid")
        return [state]

    def resources_released(self):
        # GameSwitch owns every weather process and browser child. The corner
        # returns only after GameSwitch confirms cleanup or records a terminal
        # operator move; no detached weather worker exists.
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
            raise WeatherCornerError("game-switch requires recovery")
        active = canonical.get("active")
        if canonical.get("phase") == "idle":
            if active is not None:
                raise WeatherCornerError("idle game-switch state has an active runtime")
            return None
        if canonical.get("phase") == "ready" and not isinstance(active, dict):
            raise WeatherCornerError("ready game-switch state has no active runtime")
        if canonical.get("phase") == "draining" and not isinstance(active, dict):
            raise WeatherCornerError("draining game-switch state has no active runtime")
        return _identity(active)

    @staticmethod
    def _request_id(value):
        try:
            parsed = uuid.UUID(value)
        except (ValueError, TypeError, AttributeError) as exc:
            raise WeatherCornerError("rotation request identity is invalid") from exc
        if str(parsed) != value:
            raise WeatherCornerError("rotation request identity is invalid")
        return value

    def _new_state(self, request):
        request_id = self._request_id(request.get("request_id"))
        try:
            forecast = read_view(self.snapshot_path)
        except (OSError, WeatherError):
            return {
                "schema_version": 1,
                "game": WEATHER_VIEW_NAME,
                "status": "interrupted",
                "rotation_request_id": request_id,
                "requested_at": request.get("selected_at", self.clock()),
                "completed_at": self.clock(),
                "end_reason": "forecast-unavailable-before-start",
            }
        canonical = self._canonical()
        if canonical is None:
            return None
        source = self._source(canonical)
        if source and source["game"] == WEATHER_VIEW_NAME:
            raise WeatherCornerError("weather view has no matching corner owner")
        now = self.clock()
        expires_at = forecast.get("expires_at")
        if (type(expires_at) not in (int, float)
                or expires_at - now <= 5):
            return {
                "schema_version": 1,
                "game": WEATHER_VIEW_NAME,
                "status": "interrupted",
                "rotation_request_id": request_id,
                "requested_at": request.get("selected_at", now),
                "completed_at": now,
                "end_reason": "forecast-expires-before-start",
            }
        return {
            "schema_version": 1,
            "game": WEATHER_VIEW_NAME,
            "status": "starting",
            "rotation_request_id": request_id,
            "start_request_id": request_id,
            "switch_request_id": request_id,
            "previous_game": source["game"] if source else None,
            "previous_runtime_identity": source,
            "requested_at": request.get("selected_at", now),
            "starting_at": now,
            "forecast_expires_at": expires_at,
        }

    def _mark_interrupted(self, state, reason):
        state.update(status="interrupted", completed_at=self.clock(), end_reason=reason)
        state.pop("switch_request_id", None)
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
            raise WeatherCornerError("weather start request owner is invalid")
        source = state.get("previous_runtime_identity")
        if source is None:
            result = self.coordinator.start(WEATHER_VIEW_NAME, request_id=request_id)
        else:
            result = self.coordinator.switch(
                WEATHER_VIEW_NAME,
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
            raise WeatherCornerError("weather view start did not succeed")
        receipt = getattr(result, "receipt", None)
        result_body = receipt.get("result") if isinstance(receipt, dict) else None
        if (not isinstance(receipt, dict)
                or receipt.get("status") != "succeeded"
                or receipt.get("request_id") != request_id
                or receipt.get("target") != WEATHER_VIEW_NAME
                or receipt.get("operation") not in {"start", "switch"}
                or not isinstance(result_body, dict)
                or result_body.get("request_id") != request_id
                or result_body.get("status") != "succeeded"
                or result_body.get("to_game") != WEATHER_VIEW_NAME):
            raise WeatherCornerError("weather start receipt identity is invalid")
        identity = _identity(result_body.get("active_runtime"), expected_game=WEATHER_VIEW_NAME)
        if (identity is None
                or getattr(result, "request_id", None) != request_id
                or getattr(result, "to_game", None) != WEATHER_VIEW_NAME
                or getattr(result, "generation", None) != identity["generation"]
                or receipt.get("generation") != identity["generation"]):
            raise WeatherCornerError("weather start result identity does not match receipt")
        canonical = self._canonical()
        if canonical is None:
            # The receipt is already terminal and proves the exact candidate
            # identity. Keep the write-ahead owner until canonical state can
            # be read and reconciled against it.
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
            # The successful GameSwitch receipt survives a crash before this
            # owner-state write. Retain its exact runtime identity until the
            # canonical switch transaction settles; never discard the owner
            # just because the runtime is temporarily outside active.
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
        try:
            forecast = read_view(self.snapshot_path)
            expires_at = forecast.get("expires_at")
        except (OSError, WeatherError):
            expires_at = started
        state.pop("switch_request_id", None)
        state.pop("switch_status", None)
        state.update(
            status="active",
            weather_runtime_identity=identity,
            started_at=started,
            ends_at=min(started + self.duration_minutes * 60,
                        expires_at if type(expires_at) in (int, float) else started),
            forecast_expires_at=expires_at,
        )
        state.pop("starting_runtime_identity", None)
        self._save(state)
        return "queued" if canonical.get("phase") == "draining" else None

    @staticmethod
    def _matches_active(canonical, identity):
        if canonical is None or not isinstance(canonical.get("active"), dict):
            return False
        try:
            actual = _identity(canonical["active"])
        except WeatherCornerError:
            return False
        return actual == identity

    def _validate_audio_delivery(self, state):
        delivery = state.get("audio_delivery")
        if delivery is None:
            return None
        if (not isinstance(delivery, dict)
                or delivery.get("execution_id") != state.get("rotation_request_id")
                or delivery.get("status") not in {"running", "stopping", "completed", "stopped", "failed"}
                or type(delivery.get("next_index")) is not int):
            raise WeatherCornerError("weather audio delivery state is invalid")
        next_index = delivery["next_index"]
        requests = delivery.get("requests")
        if delivery["status"] == "failed":
            if requests != [] or next_index != 0:
                raise WeatherCornerError("failed weather audio state is invalid")
            return delivery
        if (not isinstance(requests, list) or len(requests) != MAX_ITEM_INDEX + 1
                or not 0 <= next_index <= len(requests)):
            raise WeatherCornerError("weather audio request plan is invalid")
        identity = state.get("weather_runtime_identity")
        expected_digest = None
        for index, request in enumerate(requests):
            try:
                request_fingerprint(request)
                expected_key = item_idempotency_key(delivery["execution_id"], index)
            except (TypeError, ValueError) as exc:
                raise WeatherCornerError("weather audio request plan is invalid") from exc
            if (request.get("execution_id") != delivery["execution_id"]
                    or request.get("item_index") != index
                    or request.get("item_key") != expected_key
                    or not runtime_identity_matches(identity, request.get("runtime_fence"))):
                raise WeatherCornerError("weather audio request owner does not match the runtime")
            forecast = request.get("forecast")
            digest = forecast.get("report_digest") if isinstance(forecast, dict) else None
            if expected_digest is None:
                expected_digest = digest
            elif digest != expected_digest:
                raise WeatherCornerError("weather audio request plan mixes forecasts")
        if delivery["status"] == "running" and next_index >= len(requests):
            raise WeatherCornerError("weather audio completion state is invalid")
        if delivery["status"] == "stopping" and next_index >= len(requests):
            raise WeatherCornerError("weather audio stop state is invalid")
        if delivery["status"] == "completed" and next_index != len(requests):
            raise WeatherCornerError("weather audio completion state is invalid")
        return delivery

    def _prepare_audio_delivery(self, state):
        delivery = state.get("audio_delivery")
        if delivery is not None:
            return self._validate_audio_delivery(state)
        try:
            forecast = read_view(self.snapshot_path, clock=self.clock)
            lines = narration(forecast)
            if len(lines) != MAX_ITEM_INDEX + 1:
                raise WeatherAudioError("weather narration item count changed")
            execution_id = state.get("rotation_request_id")
            identity = state.get("weather_runtime_identity")
            requests = [
                build_weather_audio_request(
                    execution_id=execution_id,
                    item_index=index,
                    text=line,
                    runtime_identity=identity,
                    forecast_view=forecast,
                    now=self.clock(),
                )
                for index, line in enumerate(lines)
            ]
        except (OSError, TypeError, ValueError) as exc:
            state["audio_delivery"] = {
                "status": "failed", "execution_id": state.get("rotation_request_id"),
                "requests": [], "next_index": 0, "reason": "request-invalid",
            }
            self._save(state)
            return state["audio_delivery"]
        state["audio_delivery"] = {
            "status": "running", "execution_id": execution_id,
            "requests": requests, "next_index": 0,
        }
        self._save(state)
        return state["audio_delivery"]

    def _stop_audio_delivery(self, state, reason):
        delivery = self._validate_audio_delivery(state)
        if delivery is None or delivery["status"] in {"completed", "stopped", "failed"}:
            return
        if self.audio_port is None:
            raise WeatherCornerError("weather audio consumer is unavailable during cancellation")
        index = delivery["next_index"]
        request = delivery["requests"][index]
        if delivery["status"] == "running":
            delivery.update(status="stopping", stop_reason=reason, stop_requested_at=self.clock())
            self._save(state)
        else:
            reason = delivery.get("stop_reason", reason)
        try:
            receipt = self.audio_port.interrupt_weather_audio(request["item_key"])
            if receipt is not None:
                receipt = validate_weather_audio_receipt(request, receipt)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise WeatherCornerError("weather audio item termination is unconfirmed") from exc
        if receipt is not None and receipt["status"] == "played":
            delivery["next_index"] += 1
            if delivery["next_index"] == len(delivery["requests"]):
                delivery.update(status="completed", completed_at=self.clock())
            else:
                delivery.update(
                    status="stopped", reason=reason,
                    quiescence_confirmed_at=self.clock(),
                )
        else:
            delivery.update(status="stopped", reason=reason, quiescence_confirmed_at=self.clock())
        self._save(state)

    def _advance_audio_delivery(self, state):
        if not self.audio_enabled:
            if state.get("audio_delivery") is not None:
                self._stop_audio_delivery(state, "audio-disabled")
            return
        delivery = self._prepare_audio_delivery(state)
        if delivery["status"] == "stopping":
            self._stop_audio_delivery(state, delivery.get("stop_reason", "resume-stop"))
            return
        if delivery["status"] != "running":
            return
        index = delivery["next_index"]
        request = delivery["requests"][index]
        try:
            receipt = self.audio_port.get_weather_audio_receipt(request["item_key"])
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise WeatherCornerError("shared weather audio consumer is unavailable") from exc
        if receipt is None:
            try:
                request = validate_weather_audio_request(request, now=self.clock())
            except WeatherAudioError:
                self._stop_audio_delivery(state, "forecast-expired")
                return
            try:
                receipt = self.audio_port.enqueue_weather_audio(request)
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                # A lost command response is resolved on restart by querying
                # the same durable key before attempting the same payload.
                raise WeatherCornerError("shared weather audio enqueue is unavailable") from exc
        try:
            receipt = validate_weather_audio_receipt(request, receipt)
        except WeatherAudioError as exc:
            raise WeatherCornerError("weather audio receipt could not be verified") from exc
        if receipt["status"] == "queued":
            if self.clock() >= request["runtime_fence"]["expires_at"]:
                self._stop_audio_delivery(state, "forecast-expired")
            return
        if receipt["status"] == "played":
            delivery["next_index"] += 1
            if delivery["next_index"] == len(delivery["requests"]):
                delivery.update(status="completed", completed_at=self.clock())
            self._save(state)
            return
        if receipt["status"] in {"rejected", "interrupted"}:
            delivery.update(
                status="stopping", stop_reason=f"{receipt['status']}:{receipt['reason']}",
                stop_requested_at=self.clock(),
            )
            self._save(state)
            self._stop_audio_delivery(state, delivery["stop_reason"])
            return
        delivery.update(status="stopped", reason=f"{receipt['status']}:{receipt['reason']}")
        self._save(state)

    def _wait_and_restore(self, state):
        weather_identity = state.get("weather_runtime_identity")
        if (not isinstance(weather_identity, dict)
                or set(weather_identity) != set(RUNTIME_IDENTITY_KEYS)
                or weather_identity.get("game") != WEATHER_VIEW_NAME):
            raise WeatherCornerError("weather runtime owner identity is missing")
        while True:
            if self._stop_requested():
                return self._restore(state, end_reason="manual")
            try:
                read_view(self.snapshot_path)
            except (OSError, WeatherError):
                return self._restore(state, end_reason="forecast-expired")
            canonical = self._canonical()
            if canonical is None:
                self.sleep(min(self.poll_s, max(0.05, state["ends_at"] - self.clock())))
                continue
            phase = canonical.get("phase")
            if phase == "draining" and self._matches_active(canonical, weather_identity):
                # An operator or another owner has begun a GameSwitch request.
                # Let that durable request finish, then decide from the stable
                # canonical owner on the next scheduler tick.
                self._stop_audio_delivery(state, "runtime-transition")
                self._save(state)
                return "queued"
            if phase in {"stopping", "starting", "rolling_back"}:
                identities = []
                for key in ("active", "previous", "candidate"):
                    item = canonical.get(key)
                    if isinstance(item, dict):
                        try:
                            identities.append(_identity(item))
                        except WeatherCornerError:
                            continue
                if weather_identity in identities:
                    self._stop_audio_delivery(state, "runtime-transition")
                    self._save(state)
                    return "queued"
            if not _stable(canonical):
                self._stop_audio_delivery(state, "runtime-transition")
                raise WeatherCornerError("weather runtime ownership is in an unsafe switch phase")
            if not self._matches_active(canonical, weather_identity):
                self._stop_audio_delivery(state, "operator-moved")
                return self._mark_interrupted(state, "operator-moved-during-weather")
            remaining = state.get("ends_at")
            if type(remaining) not in (int, float) or remaining < 0:
                raise WeatherCornerError("weather end time is invalid")
            remaining = remaining - self.clock()
            if remaining <= 0:
                return self._restore(state)
            self._advance_audio_delivery(state)
            self.sleep(min(self.poll_s, remaining))

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
                or result.get("from_game") != WEATHER_VIEW_NAME
                or result.get("to_game") != state.get("previous_game")
                or result.get("status") != "succeeded"):
            return False
        return self._restored_owner_matches(state, canonical, result)

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
        except WeatherCornerError:
            return False
        return expected is not None and actual == expected

    def _restore(self, state, *, end_reason="duration"):
        from .game_switch import ERROR_SOURCE_FENCE_LOST

        weather_identity = state.get("weather_runtime_identity")
        if (not isinstance(weather_identity, dict)
                or set(weather_identity) != set(RUNTIME_IDENTITY_KEYS)
                or weather_identity.get("game") != WEATHER_VIEW_NAME):
            raise WeatherCornerError("weather runtime owner identity is missing before restore")
        # Quiesce the only weather-owned shared-queue item before restoring the
        # prior runtime. At most one narration item can be outstanding.
        self._stop_audio_delivery(state, "corner-ended")
        if state.get("status") != "restoring":
            state.update(status="restoring", restore_requested_at=self.clock(), end_reason=end_reason)
            state["restore_request_id"] = str(uuid.uuid4())
            state["switch_request_id"] = state["restore_request_id"]
            self._save(state)
        else:
            end_reason = state.get("end_reason", end_reason)
        request_id = state.get("restore_request_id")
        if not isinstance(request_id, str) or request_id != state.get("switch_request_id"):
            raise WeatherCornerError("weather restore request owner is invalid")
        canonical = self._canonical()
        if canonical is None:
            return "queued"
        if self._restore_receipt_finished(state, canonical):
            result = self.store.receipts.load(request_id)["result"]
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
        if canonical.get("phase") == "draining" and self._matches_active(canonical, weather_identity):
            pass  # GameSwitch accepts a durable request behind the active drain.
        elif not (_stable(canonical) and self._matches_active(canonical, weather_identity)):
            if _stable(canonical):
                return self._mark_interrupted(state, "operator-moved-before-restore")
            raise WeatherCornerError("weather restore source is not the owned runtime")

        previous = state.get("previous_game")
        payload = {"expected_source": weather_identity}
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
            raise WeatherCornerError("weather runtime restore did not succeed")
        canonical = self._canonical()
        if canonical is None:
            state.update(status="restoring", switch_status="queued")
            self._save(state)
            return "queued"
        result_data = getattr(result, "receipt", None)
        result_body = result_data.get("result") if isinstance(result_data, dict) else None
        if (not isinstance(result_body, dict)
                or result_body.get("from_game") != WEATHER_VIEW_NAME
                or result_body.get("to_game") != previous
                or not self._restored_owner_matches(state, canonical, result_body)):
            raise WeatherCornerError("weather restore result has no matching canonical owner")
        restored = _identity(canonical["active"]) if isinstance(canonical.get("active"), dict) else None
        state.update(status="completed", completed_at=self.clock(), end_reason=end_reason,
                     restored_runtime_identity=restored)
        state.pop("switch_request_id", None)
        state.pop("switch_status", None)
        self._save(state)
        self._clear_stop_request()
        return "completed"

    def run_rotation(self, request_id):
        if self.duration_minutes is None:
            raise WeatherCornerError("weather duration is required for the opt-in catalog entry")
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
                        raise WeatherCornerError("weather start owner has no matching switch request")
                    try:
                        receipt = self.store.receipts.load(switch_request_id)
                    except Exception:
                        raise WeatherCornerError("weather start receipt cannot be verified") from None
                    if receipt is None:
                        # A stop marker can cancel a write-ahead reservation
                        # only before GameSwitch has durably accepted it.
                        return self._mark_interrupted(state, "operator-stopped-before-start")
                    # Accepted/queued/succeeded receipts are durable owners.
                    # Replay the same request id: if it is queued, keep this
                    # state; if it committed before a crash, recover its exact
                    # active_runtime and flow through the normal restore path.
                result = self._dispatch_start(state)
                if result is not None:
                    return result
            elif status == "active":
                return self._wait_and_restore(state)
            elif status == "restoring":
                return self._restore(state)
            else:
                raise WeatherCornerError("weather rotation owner needs recovery")
        else:
            if state.get("status") not in {"idle", "completed", "interrupted"}:
                raise WeatherCornerError("another weather execution owns the state")
            if self.forecast_refresh is not None and not self._stop_requested():
                try:
                    self.forecast_refresh(self.snapshot_path.parent)
                except (OSError, WeatherError):
                    # No switch/queue was requested. Complete this reservation
                    # without retrying stale data or blocking the other corners.
                    self._save({"schema_version": 1, "game": WEATHER_VIEW_NAME,
                                "status": "interrupted", "rotation_request_id": request_id,
                                "completed_at": self.clock(),
                                "end_reason": "forecast-fetch-failed-before-start"})
                    return "completed"
            state = self._new_state({"request_id": request_id})
            if state is None:
                return "queued"
            if state.get("status") == "interrupted":
                self._save(state)
                return "completed"
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
                        or receipt.get("target") != WEATHER_VIEW_NAME
                        or receipt.get("request_id") != request_id
                        or not isinstance(result, dict)
                        or result.get("request_id") != request_id
                        or result.get("to_game") != WEATHER_VIEW_NAME
                        or receipt.get("status") not in {"failed", "rolled_back"}):
                    return False
                code = result.get("error_code")
                if (state.get("status") == "failed"
                        and state.get("last_error_code") != code):
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
                    if isinstance(active, dict) and active.get("game") == WEATHER_VIEW_NAME:
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
                else:
                    return False
                state.update(status="interrupted", completed_at=state.get("completed_at", self.clock()),
                             end_reason="switch-terminal-before-corner-active")
                state.pop("switch_request_id", None)
                state.pop("switch_status", None)
                self._save(state)
                self._clear_stop_request()
                return True
        except (OSError, ValueError, GameSwitchBusyError, WeatherCornerError):
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

"""Bounded lifecycle owner for the participatory AI探偵ミステリー corner.

Same shape as the reviewed 予想王 (``yoso_corner``) and 同志カード
(``doushi_corner``) managers: the common rotation selects the catalog row,
GameSwitch owns the display switch / boundaries / rollback / restoration, and
this module persists the corner's request, the runtime identity and one
bounded case session between the switch receipts.

The case itself is owned by the deterministic engine (``mystery_case``): the
culprit, clues and alibis are fixed from one seed before anything is shown,
and the seed's commitment goes on the broadcast board immediately. This
module can only advance phases — it can never change the solution, and no
LLM output ever becomes game state. Polls (action votes) and the final
channel-point prediction are display and participation only.

Session phases (all inside one blocking ``run_rotation``, the same
synchronous model every other corner uses): intro → investigate poll ×2 →
interrogate poll ×2 → deduction (the remaining rooms/suspects are published
as one dossier, then the whole evidence set is reviewed) → culprit
prediction → reveal (verdict / story / motive / reasoning) → restore. A phase
that cannot run (no poll/prediction configuration, transport failure, or a
time budget too small) degrades to the seeded solo detective or is skipped,
but **once a prediction exists it is always settled or refunded** — the
commitment shown on air must never strand a bet.

The dossier completion in the deduction phase is what makes the case fair
(#2757 A): polls can only cover two rooms and two suspects inside the slot,
so the board must not depend on which ones won. The prediction therefore
always opens on the complete clue/alibi set, from which exactly one suspect's
alibi fails.

Time bounds: ``ends_at`` (catalog duration) stays the hard cap; every wait is
clamped to the remaining budget minus the reveal/restore reserve
(``SESSION_RESERVE_S`` = the four reveal parts plus the restore handshake).
The reveal always runs before the restore so the audience sees the answer the
commitment covered.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import random
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
    GameSwitchStore,
    atomic_write_json,
)
from .mystery_case import CaseError, ai_detective_choice, generate, new_seed
from .mystery_case import VERSION as CASE_VERSION
from .mystery_case import (
    clue_for_room,
    poll_actions,
    poll_question,
    public_board,
    reveal,
    verify_reveal,
)
from .mystery_view import PAYLOAD_FILENAME, VIEW_NAME, write_payload

STATE_FILENAME = "mystery_corner.json"
DEFAULT_DURATION_MINUTES = 30
MAX_DURATION_MINUTES = 45
# Same bounded ceiling the retro restore recovery uses (#2199).
RESTORE_RECOVERY_TIMEOUT_S = 420.0
RUNTIME_IDENTITY_KEYS = ("game", "runtime_id", "generation", "lease_id")
PENDING_SWITCH_STATUSES = frozenset({"queued", "in_progress", "busy"})
ROLLBACK_ERROR_CODES = frozenset({
    ERROR_AGENT_START_FAILED,
    ERROR_PREPARE_FAILED,
    ERROR_QUIESCE_FAILED,
    ERROR_READINESS_TIMEOUT,
    ERROR_START_FAILED,
    ERROR_TIMEOUT,
})

#: The reveal runs as four narrated parts, each with its own board dwell time.
#: 45 + 60 + 40 + 55 = 200s: enough to read a full-width explanation panel at
#: a comfortable pace inside the 30-minute slot (see the module docstring).
REVEAL_PARTS = (
    ("verdict", 45),
    ("story", 60),
    ("motive", 40),
    ("reasoning", 55),
)
#: Total reveal dwell; the session reserve below is this plus the restore.
REVEAL_SECONDS = sum(seconds for _name, seconds in REVEAL_PARTS)

#: Time kept aside for the reveal display + the restore switch.
#: #2757 allocates the reveal as four explained parts (200s) plus the restore
#: handshake, so the reserve covers both and every earlier wait is clamped.
SESSION_RESERVE_S = REVEAL_SECONDS + 100.0
INTRO_SECONDS = 30
#: Twitch poll bounds are 15..1800s; the corner stays well inside them.
POLL_DURATION_S = 120
POLL_MIN_DURATION_S = 15
POLL_WAIT_POLL_S = 5.0
POLL_DISPLAY_SECONDS = 20
#: Two rounds per phase: the first two rooms/suspects are chosen live by the
#: poll, and the deduction phase then publishes the rest in one dossier so the
#: whole evidence set is on the board before the prediction opens (#2757 A).
INVESTIGATE_ROUNDS = 2
INTERROGATE_ROUNDS = 2
DEDUCTION_SECONDS = 90
PREDICTION_WINDOW_S = 300
PREDICTION_MIN_WINDOW_S = 30
PREDICTION_WAIT_POLL_S = 10.0
PREDICTION_SETTLE_ATTEMPTS = 5

#: Fixed end reasons this corner may record (diagnostics vocabulary).
END_REASONS = frozenset({
    "case-completed", "duration", "manual", "case-error",
    "switch-terminal-before-corner-active",
    "operator-moved-during-mystery", "operator-moved-after-start",
    "operator-moved-before-restore", "operator-stopped-before-start",
})

#: Run-ledger outcome per end reason (the trpg core's outcome vocabulary,
#: absorbed into this corner by the #684 unification ruling 2026-10-18).
_OUTCOME_BY_END_REASON = {
    "case-completed": "completed",
    "manual": "aborted",
    "case-error": "failed",
    "duration": "timeout",
    "operator-moved-during-mystery": "aborted",
    "operator-moved-after-start": "aborted",
    "operator-moved-before-restore": "aborted",
    "operator-stopped-before-start": "aborted",
    "switch-terminal-before-corner-active": "aborted",
}


def _outcome_for(end_reason):
    return _OUTCOME_BY_END_REASON.get(end_reason, "failed")

#: Fixed, non-sensitive session error codes.
SESSION_ERRORS = frozenset({
    "case-generate-failed", "poll-create-failed", "poll-status-failed",
    "prediction-create-failed", "prediction-settle-failed", "view-write-failed",
})

#: Mystery-only display scope (#2494, owner approval 2026-10-20): while the
#: case board owns the shared slot the OBS status rails are hidden, and every
#: exit path shows them again. The notice overlay and all other corners keep
#: their behavior; the control is best-effort and never fails the corner.
STATUS_OVERLAY_SCENE = "soren"
STATUS_OVERLAY_CONTROL_TIMEOUT_S = 10.0

#: Shared program-corner flag (Soren ``tmp/.program_corner_active``). While a
#: program view owns the stream, the Soren radio must not generate or play its
#: own segments: the 2026-10-20 live run had the deferred radio start in the
#: middle of the case and cut the corner narration. The flag carries a sliding
#: expiry refreshed on every board write; Soren treats a missing/expired flag
#: as inactive, so a crashed corner resumes the radio after at most this TTL
#: (fail-open). PAPER keeps its legacy flag name for backward compatibility,
#: and this corner mirrors its payload into that legacy name until the shared
#: Soren gate is deployed everywhere.
PROGRAM_FLAG_NAME = ".program_corner_active"
PROGRAM_FLAG_TTL_S = 1800.0


def _status_overlay_sources():
    """OBS status-rail source names (soviet_now defaults, env-overridable)."""
    return (
        os.environ.get("STATUS_OVERLAY_SOURCE", "statsOverlay"),
        os.environ.get("SHOW_STATUS_OVERLAY_SOURCE", "opsOverlay"),
    )


def _noop_status_overlay_control(_action, _source):
    """Unwired default: direct construction and offline runs stay inert."""
    return False


def default_status_overlay_control(g):
    """Best-effort ``control(action, source) -> bool`` via soviet_now's obs_control.

    One reviewed OBS WebSocket wrapper call per source: the wrapper applies
    sources sequentially and never rolls back an earlier one, so the caller
    owns the per-source restore obligation (review P2-3).  The wrapper is a
    no-op under ``EXPLORE_MODE=1`` on its own; no credential crosses this
    boundary, and a failure is a display degradation only — never a corner
    failure.
    """
    from .trading.soren_output import resolve_soren_root

    def control(action, source):
        if action not in ("hide", "show") or not source:
            return False
        import subprocess

        try:
            root = resolve_soren_root(g)
            if not (Path(root) / "obs_control.sh").is_file():
                return False
            completed = subprocess.run(
                ("./obs_control.sh", action, STATUS_OVERLAY_SCENE, source),
                cwd=str(root),
                timeout=STATUS_OVERLAY_CONTROL_TIMEOUT_S,
                capture_output=True,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return completed.returncode == 0

    return control


class MysteryCornerError(RuntimeError):
    """A fixed, non-sensitive mystery corner lifecycle failure."""


def _identity(runtime, *, expected_game=None):
    if runtime is None:
        return None
    if not isinstance(runtime, dict):
        raise MysteryCornerError("game-switch runtime identity is invalid")
    identity = {key: runtime.get(key) for key in RUNTIME_IDENTITY_KEYS}
    if (not all(isinstance(identity[key], str) and identity[key] for key in
                ("game", "runtime_id", "lease_id"))
            or type(identity["generation"]) is not int
            or identity["generation"] < 1
            or (expected_game is not None and identity["game"] != expected_game)):
        raise MysteryCornerError("game-switch runtime identity is incomplete")
    return identity


def _stable(canonical):
    phase = canonical.get("phase")
    return (phase in {"idle", "ready"}
            and canonical.get("candidate") is None
            and canonical.get("previous") is None
            and not canonical.get("retiring"))


def _sink_counts(value, total):
    fallback = {"sent": 0, "failed": total, "skipped": 0}
    if not isinstance(value, dict):
        return fallback
    out = {}
    for key in ("sent", "failed", "skipped"):
        raw = value.get(key)
        out[key] = raw if type(raw) is int and 0 <= raw <= total else 0
    return out if sum(out.values()) <= total else fallback


def _contested_outcomes(remote):
    """Outcomes that received at least one bet, or None if unreadable.

    Same owner decision as the 2026-10-15 no-contest refund (#933): a
    prediction where fewer than two outcomes were bet never formed a contest,
    so it is canceled and refunded instead of resolved.
    """
    outcomes = remote.get("outcomes") if isinstance(remote, dict) else None
    if not isinstance(outcomes, list) or not outcomes:
        return None
    counted = known = 0
    for outcome in outcomes:
        if not isinstance(outcome, dict):
            return None
        for key in ('users', 'channel_points'):
            value = outcome.get(key)
            if isinstance(value, bool) or not isinstance(value, int):
                continue
            known += 1
            if value > 0:
                counted += 1
                break
    return counted if known else None


def _crowd_outcome_title(remote):
    """The outcome with the most channel points (first-max), or None."""
    outcomes = remote.get("outcomes") if isinstance(remote, dict) else None
    if not isinstance(outcomes, list) or not outcomes:
        return None
    best, best_points, known = None, -1, 0
    for outcome in outcomes:
        if not isinstance(outcome, dict) or not isinstance(outcome.get("title"), str):
            return None
        points = outcome.get("channel_points")
        if isinstance(points, bool) or not isinstance(points, int) or points < 0:
            points = None
        else:
            known += 1
        if points is not None and points > best_points:
            best, best_points = outcome["title"], points
    return best if known else None


class MysteryCornerManager:
    """Run one bounded mystery case under the shared GameSwitch slot."""

    def __init__(self, g, *, duration_minutes=DEFAULT_DURATION_MINUTES, coordinator=None,
                 audio_enabled=False, poll_client_factory=None,
                 prediction_client_factory=None, agents_fn=None,
                 clock=time.time, sleep=time.sleep, poll_s=1.0,
                 poll_wait_poll_s=POLL_WAIT_POLL_S,
                 status_overlay_control=None):
        if (type(duration_minutes) is not int
                or not 1 <= duration_minutes <= MAX_DURATION_MINUTES):
            raise MysteryCornerError(
                f"mystery duration must be an integer from 1 to {MAX_DURATION_MINUTES} minutes")
        for name, value in (("poll_s", poll_s), ("poll_wait_poll_s", poll_wait_poll_s)):
            if type(value) not in (int, float) or value <= 0:
                raise MysteryCornerError(f"mystery {name} must be positive")
        if type(audio_enabled) is not bool:
            raise MysteryCornerError("mystery audio setting must be boolean")
        self.g = g
        self.duration_minutes = duration_minutes
        self.audio_enabled = audio_enabled
        self.clock, self.sleep, self.poll_s = clock, sleep, float(poll_s)
        self.poll_wait_poll_s = float(poll_wait_poll_s)
        self.poll_client_factory = poll_client_factory or self._default_poll_factory
        self.prediction_client_factory = (
            prediction_client_factory or self._default_prediction_factory)
        self.agents_fn = agents_fn
        if status_overlay_control is None:
            status_overlay_control = _noop_status_overlay_control
        if not callable(status_overlay_control):
            raise MysteryCornerError(
                "mystery status overlay control must be callable")
        self.status_overlay_control = status_overlay_control
        self.path = Path(g.state_dir) / STATE_FILENAME
        self.state_path = self.path
        self.store = GameSwitchStore(g.state_dir)
        if coordinator is None:
            from .adapters import make_coordinator_adapter
            from .stream_category import commit_hook

            # The synthetic mystery view has its own reviewed title/category
            # (AI探偵ミステリー / Just Chatting), so the normal post-commit hook
            # must follow it instead of keeping the displaced game's metadata
            # (2026-10-21 owner request). The same hook announces the previous
            # game again when the restore commits.
            coordinator = GameSwitchCoordinator(
                self.store,
                lambda spec: make_coordinator_adapter(g, spec),
                post_commit=commit_hook(g),
            )
        self.coordinator = coordinator

    @staticmethod
    def _default_poll_factory(g):
        from .mystery_api import configured_poll_client

        return configured_poll_client(g)

    @staticmethod
    def _default_prediction_factory(g):
        from .mystery_api import configured_prediction_client

        return configured_prediction_client(g)

    # --- State helpers (yoso/doushi shape) ---------------------------------

    def eligible(self):
        return self.duration_minutes is not None

    def _read_state(self):
        if not self.path.exists():
            return {"schema_version": 1, "status": "idle"}
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise MysteryCornerError("mystery corner state is unreadable") from exc
        if (not isinstance(state, dict) or state.get("schema_version") != 1
                or not isinstance(state.get("status"), str)):
            raise MysteryCornerError("mystery corner state is invalid")
        return state

    def _save(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, state)

    def observations(self):
        if not self.path.exists():
            return []
        state = self._read_state()
        if state.get("game", VIEW_NAME) != VIEW_NAME:
            raise MysteryCornerError("mystery corner state owner is invalid")
        return [state]

    def resources_released(self):
        # GameSwitch owns every view process and browser child; no detached
        # mystery worker exists.
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
            raise MysteryCornerError("game-switch requires recovery")
        active = canonical.get("active")
        if canonical.get("phase") == "idle":
            if active is not None:
                raise MysteryCornerError("idle game-switch state has an active runtime")
            return None
        if not isinstance(active, dict):
            raise MysteryCornerError("active game-switch state has no runtime")
        return _identity(active)

    @staticmethod
    def _request_id(value):
        try:
            parsed = uuid.UUID(value)
        except (ValueError, TypeError, AttributeError) as exc:
            raise MysteryCornerError("rotation request identity is invalid") from exc
        if str(parsed) != value:
            raise MysteryCornerError("rotation request identity is invalid")
        return value

    def _new_state(self, request_id):
        canonical = self._canonical()
        if canonical is None:
            return None
        source = self._source(canonical)
        if source and source["game"] == VIEW_NAME:
            raise MysteryCornerError("mystery view has no matching corner owner")
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
        # Interrupted exits still hand the shared display back: show the
        # status rails this corner hid and let the radio resume before
        # marking the state terminal.
        self._release_status_overlays(state)
        self._clear_program_flag(state)
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

    # --- Status rails (mystery-only display scope, #2494) -------------------

    def _hide_status_overlays(self, state):
        """Hide the OBS status rails while the case board owns the slot.

        Per-source and idempotent (review P2-3): the wrapper applies sources
        sequentially without rollback, so the restore obligation is recorded
        per source. The intent is persisted *before* the attempts — a crash
        in between can only re-show an already visible rail — and a source
        whose hide failed is dropped from the obligation instead of assuming
        the whole call changed nothing.  A source already owed a restore
        (carried over from an earlier request, review P2) stays as it is and
        is not hidden a second time.
        """
        carried = state.get("status_overlay_hidden_sources")
        carried = list(carried) if isinstance(carried, list) else []
        sources = [source for source in _status_overlay_sources()
                   if source not in carried]
        if not sources:
            return
        state["status_overlay_hidden_sources"] = carried + list(sources)
        self._save(state)
        hidden = list(carried)
        for source in sources:
            try:
                applied = bool(self.status_overlay_control("hide", source))
            except Exception:
                applied = False
            if applied:
                hidden.append(source)
        if hidden:
            state["status_overlay_hidden_sources"] = hidden
        else:
            state.pop("status_overlay_hidden_sources", None)
        self._save(state)

    def _release_status_overlays(self, state):
        """Show the OBS status rails this corner still owes a restore for.

        Per-source: every recorded source is shown once and dropped from the
        obligation on success; a failed show stays recorded so a later entry
        (terminal re-entry, recovery, or the next rotation) retries it
        (review P2-1). Never raises.
        """
        pending = state.get("status_overlay_hidden_sources")
        if not isinstance(pending, list) or not pending:
            return
        remaining = []
        for source in pending:
            try:
                shown = bool(self.status_overlay_control("show", source))
            except Exception:
                shown = False
            if not shown:
                remaining.append(source)
        if remaining:
            state["status_overlay_hidden_sources"] = remaining
        else:
            state.pop("status_overlay_hidden_sources", None)
        self._save(state)

    # --- Soren radio suppression (program-corner flag) ---------------------

    def _program_flag_path(self):
        from .trading.soren_output import resolve_soren_root

        return resolve_soren_root(self.g) / "tmp" / PROGRAM_FLAG_NAME

    def _legacy_program_flag_path(self):
        """PAPER-era name, mirrored until the shared Soren gate is deployed."""
        return self._program_flag_path().with_name(".paper_corner_active")

    def _program_flag_owner(self, state):
        owner = state.get("rotation_request_id")
        return owner if isinstance(owner, str) and owner else ""

    def _foreign_program_flag(self, flag, owner, now):
        """True when ``flag`` belongs to a different, still-live corner.

        A flag without an owner is the legacy PAPER format; while it is live it
        must not be overwritten either. Unreadable/corrupt flags are treated as
        ours to replace, matching the radio's fail-open parsing.
        """
        try:
            current = json.loads(flag.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return False
        except (OSError, ValueError):
            return False
        if not isinstance(current, dict):
            return False
        ends_at = current.get("ends_at")
        if type(ends_at) not in (int, float) or ends_at <= now:
            return False
        current_owner = current.get("owner")
        if not isinstance(current_owner, str):
            current_owner = ""
        return current_owner != owner

    def _refresh_program_flag(self, state):
        """Advertise this program corner to the Soren radio (best-effort).

        The radio reads only ``ends_at``; the owner token lets this corner
        avoid deleting or overwriting a newer owner's flag. The legacy PAPER
        flag is mirrored for a deployment that still runs the old Soren gate; a
        newer Soren checks the shared flag first. Failures are swallowed: the
        radio treats a missing/broken flag as inactive, and the sliding expiry
        bounds a crashed corner to ``PROGRAM_FLAG_TTL_S``. Never raises.
        """
        try:
            # No narration means nothing to protect: leave the radio alone.
            if not self.audio_enabled:
                return
            owner = self._program_flag_owner(state)
            if not owner:
                return
            now = self.clock()
            paths = (self._program_flag_path(), self._legacy_program_flag_path())
            # A lost slot can still call _write_view for its reveal. Never
            # overwrite the new owner's live flag in that path.
            if any(self._foreign_program_flag(flag, owner, now) for flag in paths):
                return
            payload = {
                "kind": "mystery",
                "owner": owner,
                "request_id": state.get("rotation_request_id"),
                "started_at": state.get("started_at"),
                "progress_at": now,
                "ends_at": now + PROGRAM_FLAG_TTL_S,
            }
            for flag in paths:
                try:
                    flag.parent.mkdir(parents=True, exist_ok=True)
                    atomic_write_json(flag, payload)
                except Exception:
                    continue
        except Exception:
            pass

    def _clear_program_flag(self, state):
        """Remove only this corner's flags; a newer owner's flag is kept."""
        try:
            owner = self._program_flag_owner(state)
            if not owner:
                return
            for flag in (self._program_flag_path(), self._legacy_program_flag_path()):
                try:
                    current = json.loads(flag.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    continue
                except (OSError, ValueError):
                    # Unreadable flags expire on their own; do not guess
                    # ownership.
                    continue
                if isinstance(current, dict) and current.get("owner") == owner:
                    flag.unlink(missing_ok=True)
        except Exception:
            pass

    # --- Start dispatch (yoso shape) ---------------------------------------

    def _dispatch_start(self, state):
        request_id = state.get("start_request_id")
        if not isinstance(request_id, str) or request_id != state.get("rotation_request_id"):
            raise MysteryCornerError("mystery start request owner is invalid")
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
            raise MysteryCornerError("mystery view start did not succeed")
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
            raise MysteryCornerError("mystery start receipt identity is invalid")
        identity = _identity(result_body.get("active_runtime"), expected_game=VIEW_NAME)
        if (identity is None
                or getattr(result, "request_id", None) != request_id
                or getattr(result, "to_game", None) != VIEW_NAME
                or getattr(result, "generation", None) != identity["generation"]
                or receipt.get("generation") != identity["generation"]):
            raise MysteryCornerError("mystery start result identity does not match receipt")
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
            mystery_runtime_identity=identity,
            started_at=started,
            ends_at=started + self.duration_minutes * 60,
        )
        self._save(state)
        # The case session (and the stop marker handling) belongs to the
        # caller's active-state path.
        return None

    # --- The case session ---------------------------------------------------

    def _session(self, state):
        """Validated bounded session progress; creates the durable seed."""
        session = state.get("session")
        if session is not None and (
                not isinstance(session, dict)
                or type(session.get("seed")) is not int
                or not 0 <= session["seed"] < 2**64
                or not isinstance(session.get("announced"), dict)
                or not isinstance(session.get("revealed_rooms"), list)
                or not isinstance(session.get("revealed_suspects"), list)):
            raise MysteryCornerError("mystery session progress is invalid")
        if session is None:
            session = {
                "seed": new_seed(),
                "case_version": CASE_VERSION,
                "phase": "intro",
                "announced": {},
                "revealed_rooms": [],
                "revealed_suspects": [],
                "revealed": False,
            }
            state["session"] = session
            self._save(state)
        return session

    def _case(self, session):
        # A session started under another engine version must not be re-read
        # under new rules: the published commitment covers (version, seed), so
        # silently regenerating would change the answer behind that hash.
        if session.get("case_version") != CASE_VERSION:
            raise MysteryCornerError("mystery case engine version changed mid-session")
        try:
            return generate(session["seed"])
        except CaseError as exc:
            raise MysteryCornerError("mystery case generation failed") from exc

    def _remaining(self, state):
        ends_at = state.get("ends_at")
        if type(ends_at) not in (int, float) or isinstance(ends_at, bool):
            raise MysteryCornerError("mystery end time is invalid")
        return ends_at - self.clock()

    def _write_view(self, state, case, session, *, phase, poll=None, prediction=None,
                    refresh_flag=True, reveal_step=None):
        """Best-effort bounded broadcast payload write (never raises).

        ``refresh_flag=False`` is for the slot-lost reveal: the board may still
        be written, but the next owner's radio flag must not be overwritten.
        ``reveal_step`` (1..len(REVEAL_PARTS)) lets the board highlight the
        explanation section being read at that moment.
        """
        # Keep the radio suppressed for as long as the board is being updated;
        # the flag is refreshed before the write so a view failure cannot let
        # the radio resume mid-corner.
        if refresh_flag:
            self._refresh_program_flag(state)
        try:
            revealed = reveal(case) if session.get("revealed") else None
            board = public_board(
                case,
                revealed_rooms=tuple(session.get("revealed_rooms") or ()),
                revealed_suspects=tuple(session.get("revealed_suspects") or ()),
                revealed=revealed,
            )
            payload = {
                "schema": "mystery-view",
                "version": 1,
                "runtime_id": state.get("mystery_runtime_identity", {}).get("runtime_id"),
                "generation": state.get("mystery_runtime_identity", {}).get("generation"),
                "phase": phase if phase in _VIEW_PHASES else "intro",
                "case": board,
                "poll": poll,
                "prediction": prediction,
                "reveal_step": (reveal_step if type(reveal_step) is int
                                and 1 <= reveal_step <= len(REVEAL_PARTS) else None),
                "updated_at": self.clock(),
            }
            write_payload(Path(self.g.state_dir) / PAYLOAD_FILENAME, payload)
        except Exception:
            state["last_error_code"] = "view-write-failed"

    def _speak(self, state, session, *, kind, fallback, overlay, facts=None,
               marker=None, allow_llm=True):
        """At-most-once narration for one phase marker; never raises."""
        marker = marker or f"{kind}"
        if session["announced"].get(marker):
            return
        # Record the attempt before the side effect so a crash cannot repeat it.
        session["announced"][marker] = True
        self._save(state)
        from .mystery_narration import publish

        agents = ""
        if self.agents_fn is not None:
            try:
                agents = self.agents_fn(self.g)
            except Exception:
                agents = ""
        spoken, spoken_kind = fallback, "fallback"
        if allow_llm and agents and facts:
            try:
                from .mystery_narration import generate_text

                spoken, spoken_kind = generate_text(self.g, agents, facts, fallback)
            except Exception:
                spoken, spoken_kind = fallback, "fallback"
        result = {}
        try:
            result = publish(
                self.g, kind=kind, title="AI探偵ミステリー",
                overlay_body=overlay, speech_text=spoken,
                request_id=str(state.get("rotation_request_id") or ""),
                audio_enabled=self.audio_enabled) or {}
        except Exception:
            result = {}
        narration = session.setdefault("narration", {})
        if isinstance(narration, dict):
            narration[marker] = {
                "overlay": _sink_counts(result.get("overlay"), 1),
                "speech": _sink_counts(result.get("speech"), 1),
                "kind": spoken_kind,
            }

    def _poll_client(self, state):
        try:
            client, _reason = self.poll_client_factory(self.g)
        except Exception:
            state["last_error_code"] = "poll-create-failed"
            return None
        return client if client is not None else None

    def _run_active_session(self, state):
        identity = state.get("mystery_runtime_identity")
        if (not isinstance(identity, dict)
                or set(identity) != set(RUNTIME_IDENTITY_KEYS)
                or identity.get("game") != VIEW_NAME):
            raise MysteryCornerError("mystery runtime owner identity is missing")
        # The case board owns the shared slot for this run: hide the OBS
        # status rails for the mystery corner only (#2494). Every exit path
        # (restore completion or interruption) shows them again.  The hide
        # is gated on the canonical ownership: re-opening a session whose
        # slot was already moved away must not touch the next corner's rails
        # (review P2-2) — a restore obligation recorded earlier is still
        # released on the exit paths regardless.
        canonical = self._canonical()
        slot_lost = (canonical is not None and _stable(canonical)
                     and not self._matches_active(canonical, identity))
        if not slot_lost:
            self._hide_status_overlays(state)
        session = self._session(state)
        try:
            case = self._case(session)
        except MysteryCornerError:
            state["last_error_code"] = "case-generate-failed"
            # Nothing was ever shown: end the slot without a reveal.
            self._record_run_ledger(state, None, session, end_reason="case-error")
            return self._restore(state, end_reason="case-error")

        # Stop markers still own the exit; the session degrades to display-only.
        if self._stop_requested():
            self._record_run_ledger(state, case, session, end_reason="manual")
            return self._restore(state, end_reason="manual")

        end_reason = "case-completed"

        def slot_interrupted():
            """``"moved"``/``"draining"`` when the shared slot left this corner.

            yoso's wait loop observes the same conditions between polls; the
            session checks between phases so an operator move (or another
            owner's durable switch request) is noticed promptly.
            """
            canonical = self._canonical()
            if canonical is None:
                return None
            if (canonical.get("phase") == "draining"
                    and self._matches_active(canonical, identity)):
                return "draining"
            if _stable(canonical) and not self._matches_active(canonical, identity):
                return "moved"
            return None

        interrupted = None
        try:
            self._phase_intro(state, case, session)
            interrupted = slot_interrupted()
            for round_index in range(INVESTIGATE_ROUNDS):
                if interrupted is not None or self._stop_requested():
                    break
                if len(session["revealed_rooms"]) >= len(case.rooms):
                    break
                self._phase_poll(state, case, session, "investigate", round_index)
                interrupted = slot_interrupted()
            for round_index in range(INTERROGATE_ROUNDS):
                if interrupted is not None or self._stop_requested():
                    break
                if len(session["revealed_suspects"]) >= len(case.suspects):
                    break
                self._phase_poll(state, case, session, "interrogate", round_index)
                interrupted = slot_interrupted()
            if interrupted is None and not self._stop_requested():
                # #2757 A: the whole dossier (every room, every alibi) is on
                # the board before the prediction opens, whatever the polls
                # managed to cover.
                self._complete_dossier(state, case, session)
                self._phase_deduction(state, case, session)
                self._phase_prediction(state, case, session)
        except MysteryCornerError:
            end_reason = "case-error"
        except Exception:
            # A bounded failure inside a phase must never fail the game switch;
            # the commitment still gets its reveal below.
            state["last_error_code"] = "case-error"
            end_reason = "case-error"
        if interrupted is None and self._stop_requested():
            end_reason = "manual"
        # An unsettled prediction must never strand points: settle or refund it
        # even when an earlier phase blew up or the slot was taken away.
        self._ensure_prediction_settled(state, case, session)
        if interrupted == "moved":
            # Our runtime is gone; record the reveal in state without narration
            # and without touching the radio flag the new owner may hold.
            if not session.get("revealed"):
                session["revealed"] = True
                self._write_view(state, case, session, phase="reveal", refresh_flag=False)
                self._save(state)
            self._record_run_ledger(state, case, session, end_reason="operator-moved-during-mystery")
            return self._mark_interrupted(state, "operator-moved-during-mystery")
        if not session.get("revealed"):
            try:
                self._phase_reveal(state, case, session)
            except Exception:
                pass
        self._record_run_ledger(state, case, session, end_reason=end_reason)
        if interrupted == "draining":
            # Another owner's durable switch request is draining: hand the slot
            # back (the restore queues behind the drain like yoso's does).
            return self._restore(state, end_reason="manual")
        return self._restore(state, end_reason=end_reason)

    def _record_run_ledger(self, state, case, session, *, end_reason):
        """One bounded run-ledger entry per finished case (at-most-once).

        The trpg core's run ledger semantics (#2481 → #684 unification):
        bounded fields, count/size trim, atomic persistence. Viewer
        identities never reach the ledger — counts only.
        """
        if session.get("ledger_recorded"):
            return
        session["ledger_recorded"] = True
        self._save(state)
        entry = {
            "seed": case.seed if case is not None else None,
            "commitment": case.commitment if case is not None else None,
            "culprit_index": case.culprit_index if case is not None else None,
            "started_at": state.get("started_at"),
            "ended_at": self.clock(),
            "outcome": _outcome_for(end_reason),
            "participants": session.get("participants") or 0,
            "votes_total": session.get("votes_total") or 0,
            "polls": session.get("polls_held") or 0,
            "prediction": (session.get("prediction")
                           if isinstance(session.get("prediction"), dict) else None),
            "end_reason": end_reason,
            "error_code": state.get("last_error_code"),
        }
        try:
            from .mystery_participation import (
                LEDGER_FILENAME,
                load_ledger,
                record_run,
                save_ledger,
            )

            path = Path(self.g.state_dir) / LEDGER_FILENAME
            ledger = load_ledger(path)
            record_run(ledger, entry)
            save_ledger(path, ledger)
        except Exception:
            state["last_error_code"] = "ledger-record-failed"

    def _grace_display(self, state, seconds):
        remaining = self._remaining(state)
        if remaining <= 0:
            return
        self.sleep(min(seconds, remaining))

    def _phase_intro(self, state, case, session):
        session["phase"] = "intro"
        self._write_view(state, case, session, phase="intro")
        from .mystery_narration import intro_text

        facts = (f"舞台: {case.place}\n被害者: {case.victim}\n時刻: {case.time_label}\n"
                 f"容疑者: {len(case.suspects)}人")
        overlay = f"{case.headline()}（{case.time_label}）\n容疑者 {len(case.suspects)}人"
        self._speak(state, session, kind="intro", fallback=intro_text(case),
                    overlay=overlay, facts=facts)
        self._grace_display(state, INTRO_SECONDS)

    def _complete_dossier(self, state, case, session):
        """Publish every remaining room clue and alibi before the prediction.

        The poll rounds can only cover a couple of rooms/suspects inside the
        slot, so the deduction phase opens with the AI detective publishing
        the rest: after this point the board holds the complete evidence set
        and the culprit is uniquely derivable from it (#2757 A).
        """
        for room in case.rooms:
            if room.id not in session["revealed_rooms"]:
                session["revealed_rooms"].append(room.id)
        for index in range(len(case.suspects)):
            if index not in session["revealed_suspects"]:
                session["revealed_suspects"].append(index)
        session["dossier_complete"] = True
        self._save(state)
        self._write_view(state, case, session, phase="deduction")

    def _phase_deduction(self, state, case, session):
        session["phase"] = "deduction"
        self._write_view(state, case, session, phase="deduction")
        from .mystery_narration import deduction_summary_text

        overlay = (
            f"推理タイム：手がかり{len(case.rooms)}件／アリバイ{len(case.suspects)}人分\n"
            "時刻つきの手がかりの場所と、各人の証言の場所を突き合わせてください"
        )
        facts_lines = [f"事件: {case.headline()}（{case.time_label}）"]
        for room in case.rooms:
            clue = clue_for_room(case, room.id)
            facts_lines.append(f"手がかり（{room.name}）: {clue.text if clue else 'なし'}")
        for index, suspect in enumerate(case.suspects):
            facts_lines.append(f"アリバイ（{suspect.name}）: {case.alibi_of(index)}")
        self._speak(state, session, kind="deduction",
                    fallback=deduction_summary_text(case),
                    overlay=overlay, facts="\n".join(facts_lines),
                    marker="deduction")
        self._grace_display(state, DEDUCTION_SECONDS)

    def _phase_poll(self, state, case, session, phase, round_index=0):
        """One participatory vote; degrades to the seeded solo detective."""
        covered = (session["revealed_rooms"] if phase == "investigate"
                   else [str(index) for index in session["revealed_suspects"]])
        actions = poll_actions(case, phase, exclude=covered)
        if not actions:
            return
        question = poll_question(case, phase)
        marker = f"{phase}:{round_index}:result"
        if session["announced"].get(marker):
            return
        client = self._poll_client(state)
        winner_value = None
        poll_view = None
        if client is not None and len(actions) >= 2 and self._budget_left(state, minimum=30.0):
            winner_value, poll_view = self._run_poll(state, case, session, phase,
                                                     question, actions, client,
                                                     round_index)
        if winner_value is None:
            # Solo mode: the seeded AI detective investigates so the case still
            # advances with zero participation (and the reveal stays verifiable).
            rng = random.Random(f"{session['seed']}:{phase}:{round_index}:solo")
            winner_value = ai_detective_choice(case, phase, rng, exclude=covered)
        if phase == "investigate":
            from .mystery_case import room_by_id
            from .mystery_narration import investigate_text

            room = room_by_id(case, winner_value)
            if room is None:
                raise MysteryCornerError("mystery poll winner is not a room")
            if room.id not in session["revealed_rooms"]:
                session["revealed_rooms"].append(room.id)
            clue = clue_for_room(case, room.id)
            fallback = investigate_text(room.name, clue.text if clue else None)
            overlay = f"調査「{room.name}」\n{clue.text if clue else '手がかりなし'}"
            facts = f"調査した部屋: {room.name}\n見つかった手がかり: {clue.text if clue else 'なし'}"
        else:
            from .mystery_narration import interrogate_text

            try:
                index = int(winner_value)
            except (TypeError, ValueError) as exc:
                raise MysteryCornerError("mystery poll winner is not a suspect") from exc
            if not 0 <= index < len(case.suspects):
                raise MysteryCornerError("mystery poll winner is out of range")
            suspect = case.suspects[index]
            if index not in session["revealed_suspects"]:
                session["revealed_suspects"].append(index)
            alibi = case.alibi_of(index)
            fallback = interrogate_text(suspect.name, alibi)
            overlay = f"尋問「{suspect.name}（{suspect.role}）」\n{alibi}"
            facts = f"尋問した容疑者: {suspect.name}（{suspect.role}）\nアリバイ: {alibi}"
        session["phase"] = phase
        poll_view = poll_view or {"question": question, "status": "solo"}
        self._write_view(state, case, session, phase=phase, poll=poll_view)
        self._speak(state, session, kind=phase, fallback=fallback, overlay=overlay,
                    facts=facts, marker=marker)
        session["announced"][marker] = True
        self._save(state)
        self._grace_display(state, POLL_DISPLAY_SECONDS)

    def _budget_left(self, state, *, minimum):
        remaining = self._remaining(state) - SESSION_RESERVE_S
        return remaining >= minimum

    def _run_poll(self, state, case, session, phase, question, actions, client,
                  round_index=0):
        """Create one poll, wait it out, return ``(winner_value, view)``.

        Any transport problem degrades to solo mode (``None`` winner): a
        failed vote never blocks the case or the reveal.
        """
        labels = [label for _value, label in actions]
        values = [value for value, _label in actions]
        poll_key = f"{phase}_{round_index}_poll_id"
        poll_id = session.get(poll_key)
        row = None
        if isinstance(poll_id, str) and poll_id:
            try:
                for candidate in client.list(poll_id):
                    if candidate.get("id") == poll_id:
                        row = candidate
                        break
            except Exception:
                state["last_error_code"] = "poll-status-failed"
                return None, None
        else:
            try:
                rows = client.create(question, labels, POLL_DURATION_S)
                row = rows[0] if rows else None
            except Exception:
                state["last_error_code"] = "poll-create-failed"
                return None, None
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                poll_id = row["id"]
                session[poll_key] = poll_id
                self._save(state)
        if not isinstance(row, dict):
            return None, None

        from .mystery_api import poll_choice_votes
        from .mystery_participation import winner as winning_choice

        deadline = self.clock() + POLL_DURATION_S + 15
        deadline = min(deadline, state["ends_at"] - SESSION_RESERVE_S)
        while True:
            votes = poll_choice_votes(row)
            poll_view = self._poll_view(question, labels, values, row, votes)
            self._write_view(state, case, session, phase=phase, poll=poll_view)
            status = row.get("status")
            if status != "ACTIVE" or self.clock() >= deadline:
                break
            self.sleep(self.poll_wait_poll_s)
            try:
                fresh = client.list(poll_id) if poll_id else []
            except Exception:
                state["last_error_code"] = "poll-status-failed"
                break
            row = next((item for item in fresh if item.get("id") == poll_id), row)
        votes = poll_choice_votes(row)
        # Decide the winner before building the final board payload so the
        # audience sees which choice carried the vote (#2494 display path);
        # a zero-vote poll still degrades to the seeded solo detective.
        decided = votes is not None and sum(votes) > 0
        winner = winning_choice(votes) if decided else None
        if winner is not None and not 0 <= winner < len(values):
            winner = None
        poll_view = self._poll_view(question, labels, values, row, votes, winner=winner)
        if not decided:
            return None, poll_view
        # Counters for the run ledger (trpg-core semantics, counts only).
        session["polls_held"] = (session.get("polls_held") or 0) + 1
        session["votes_total"] = (session.get("votes_total") or 0) + sum(votes)
        if winner is None:
            return None, poll_view
        return values[winner], poll_view

    @staticmethod
    def _poll_view(question, labels, values, row, votes, *, winner=None):
        if votes is None:
            votes = []
        status = row.get("status") if isinstance(row, dict) else None
        active = status == "ACTIVE"
        return {
            "question": question,
            "status": status if isinstance(status, str) else "unknown",
            "choices": [
                {"label": label, "value": value,
                 "votes": votes[index] if index < len(votes) else None,
                 "active": active,
                 "winner": winner is not None and index == winner}
                for index, (value, label) in enumerate(zip(values, labels))
            ],
        }

    # --- Final prediction ----------------------------------------------------

    def _phase_prediction(self, state, case, session):
        if session.get("prediction_done"):
            return
        remaining = self._remaining(state) - SESSION_RESERVE_S
        if remaining < PREDICTION_MIN_WINDOW_S:
            session["prediction_done"] = True
            session["prediction"] = {"status": "skipped"}
            self._save(state)
            return
        try:
            client, reason = self.prediction_client_factory(self.g)
        except Exception:
            client, reason = None, "configuration"
        if client is None:
            session["prediction_done"] = True
            session["prediction"] = {"status": "skipped", "reason": str(reason or "unconfigured")}
            self._save(state)
            return
        outcomes = [f"{s.name}（{s.role}）" for s in case.suspects]
        title = "AI探偵ミステリー：犯人は誰？"
        prediction_id = session.get("prediction_id")
        row = None
        if isinstance(prediction_id, str) and prediction_id:
            try:
                for candidate in client.list(prediction_id):
                    if candidate.get("id") == prediction_id:
                        row = candidate
                        break
            except Exception:
                state["last_error_code"] = "prediction-create-failed"
                session["prediction_done"] = True
                session["prediction"] = {"status": "skipped", "reason": "transport"}
                self._save(state)
                return
        else:
            try:
                rows = client.create(title, outcomes,
                                     max(PREDICTION_MIN_WINDOW_S,
                                         min(PREDICTION_WINDOW_S, int(remaining))))
                row = rows[0] if rows else None
            except Exception:
                state["last_error_code"] = "prediction-create-failed"
                session["prediction_done"] = True
                session["prediction"] = {"status": "skipped", "reason": "rejected-or-transport"}
                self._save(state)
                return
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                prediction_id = row["id"]
                session["prediction_id"] = prediction_id
                session["prediction_created_at"] = self.clock()
                self._save(state)
        if not isinstance(row, dict) or not isinstance(prediction_id, str):
            session["prediction_done"] = True
            session["prediction"] = {"status": "skipped", "reason": "invalid-response"}
            self._save(state)
            return
        session["phase"] = "prediction"
        # Wait out the betting window (Twitch locks automatically at its end).
        while True:
            self._write_view(state, case, session, phase="prediction",
                             prediction=self._prediction_view(row))
            status = row.get("status")
            if self._stop_requested():
                break
            if status in {"LOCKED", "RESOLVED", "CANCELED"} or self.clock() >= state["ends_at"] - SESSION_RESERVE_S:
                break
            self.sleep(PREDICTION_WAIT_POLL_S)
            try:
                fresh = client.list(prediction_id)
            except Exception:
                continue
            row = next((item for item in fresh if item.get("id") == prediction_id), row)
        session["prediction_row_titles"] = [o.get("title") for o in (row.get("outcomes") or [])
                                            if isinstance(o, dict)]
        self._ensure_prediction_settled(state, case, session)

    def _prediction_view(self, row):
        if not isinstance(row, dict):
            return {"status": "unknown"}
        status = row.get("status")
        return {"status": status if isinstance(status, str) else "unknown"}

    def _ensure_prediction_settled(self, state, case, session):
        """Settle or refund the recorded prediction; idempotent and bounded."""
        if session.get("prediction_done"):
            return
        prediction_id = session.get("prediction_id")
        if not isinstance(prediction_id, str) or not prediction_id:
            return
        try:
            client, reason = self.prediction_client_factory(self.g)
        except Exception:
            client, reason = None, "configuration"
        if client is None:
            session["prediction_done"] = True
            session["prediction"] = {"status": "skipped", "reason": str(reason or "unconfigured")}
            self._save(state)
            return
        row = None
        for attempt in range(PREDICTION_SETTLE_ATTEMPTS):
            try:
                fresh = client.list(prediction_id)
                row = next((item for item in fresh if item.get("id") == prediction_id), None)
            except Exception:
                row = None
            if isinstance(row, dict):
                status = row.get("status")
                if status == "ACTIVE":
                    try:
                        client.end(prediction_id, "LOCKED")
                    except Exception:
                        pass
                elif status in {"RESOLVED", "CANCELED"}:
                    self._record_prediction_result(state, case, session, row)
                    return
                else:  # LOCKED: decide and end.
                    decided = self._decide_prediction(state, case, session, client, prediction_id, row)
                    if decided:
                        return
            self.sleep(PREDICTION_WAIT_POLL_S)
        state["last_error_code"] = "prediction-settle-failed"

    def _decide_prediction(self, state, case, session, client, prediction_id, row):
        """LOCKED → RESOLVED (crowd payoff) or CANCELED (refund). Never mis-resolves."""
        culprit_label = f"{case.culprit.name}（{case.culprit.role}）"
        titles = session.get("prediction_row_titles")
        known_titles = titles if isinstance(titles, list) else []
        contested = _contested_outcomes(row)
        if contested is not None and contested <= 1:
            try:
                rows = client.end(prediction_id, "CANCELED")
            except Exception:
                return False
            resolved_row = rows[0] if rows else row
            self._record_participants(session, resolved_row)
            self._record_prediction_result(state, case, session, resolved_row,
                                           resolved=False)
            return True
        winning_id = None
        for outcome in (row.get("outcomes") or []):
            if isinstance(outcome, dict) and outcome.get("title") == culprit_label \
                    and isinstance(outcome.get("id"), str):
                winning_id = outcome["id"]
                break
        if winning_id is None and culprit_label not in known_titles:
            # The crowd bet on a board we cannot prove: refund, never mis-resolve.
            try:
                rows = client.end(prediction_id, "CANCELED")
            except Exception:
                return False
            resolved_row = rows[0] if rows else row
            self._record_participants(session, resolved_row)
            self._record_prediction_result(state, case, session, resolved_row,
                                           resolved=False)
            return True
        if winning_id is None:
            return False
        try:
            rows = client.end(prediction_id, "RESOLVED", winning_id)
        except Exception:
            return False
        resolved_row = rows[0] if rows else row
        crowd = _crowd_outcome_title(resolved_row)
        crowd_index = self._record_participants(session, resolved_row)
        self._record_prediction_result(state, case, session, resolved_row,
                                       resolved=True, hit=(crowd == culprit_label),
                                       hit_by_users=(None if crowd_index is None
                                                     else crowd_index == case.culprit_index))
        return True

    def _record_participants(self, session, row):
        """Record the per-user participation tally (fail-open, counts only).

        Twitch exposes only the top predictors per outcome; the ported trpg
        aggregation semantics (idempotent per user, tie → lowest index) give
        the crowd's pick by user count alongside the by-points payoff. The
        result feeds the run ledger, never the settlement itself.
        """
        try:
            from .mystery_participation import (
                participants_from_prediction,
                tally_votes,
                winner,
            )

            votes, order = participants_from_prediction(row or {})
            session["participants"] = len(order)
            if not order:
                return None
            tally = tally_votes(votes, choice_count=len(session.get("prediction_row_titles") or []))
            return winner(tally)
        except Exception:
            return None

    def _record_prediction_result(self, state, case, session, row, *, resolved=True,
                                  hit=None, hit_by_users=None):
        session["prediction_done"] = True
        session["prediction"] = {
            "status": row.get("status") if isinstance(row, dict) else "unknown",
            "resolved": bool(resolved),
            "hit": hit if hit is None or isinstance(hit, bool) else None,
            "hit_by_users": (hit_by_users
                             if hit_by_users is None or isinstance(hit_by_users, bool)
                             else None),
        }
        self._save(state)

    # --- Reveal ---------------------------------------------------------------

    def _phase_reveal(self, state, case, session):
        """Publish the answer and the full, deterministic explanation.

        #2757 B: the reveal is not a one-line noun dump. Four narrated parts
        walk through the verdict, the timeline/method, the motive, and the
        reasoning (the contradiction that breaks the culprit's alibi plus why
        every other suspect is cleared). The board carries the same facts in
        structured form; the text is built from engine-owned facts only, so no
        model can invent a detail (all four parts are fixed text).
        """
        session["phase"] = "reveal"
        revealed = reveal(case)
        if not verify_reveal(revealed, case):
            raise MysteryCornerError("mystery reveal does not match the case")
        session["revealed"] = True
        self._write_view(state, case, session, phase="reveal", reveal_step=1)
        from .mystery_narration import (
            reveal_motive_text,
            reveal_reasoning_text,
            reveal_story_text,
            reveal_text,
        )

        prediction = session.get("prediction") if isinstance(session.get("prediction"), dict) else {}
        hit = prediction.get("hit")
        if prediction.get("resolved") is not True:
            result_line = "予想は不成立・返金になりました"
        elif hit is True:
            result_line = "みなさんの指名は的中です！"
        elif hit is False:
            result_line = "みなさんの指名は外れました"
        else:
            result_line = "予想の精算結果は未確定です"

        culprit_label = f"{case.culprit.name}（{case.culprit.role}）"
        verdict_overlay = (
            f"犯人は {culprit_label}\n"
            f"手法：{case.method}／動機：{case.motive}\n"
            f"commit {case.commitment[:16]}… seed {case.seed}\n{result_line}"
        )
        story_overlay = ("事件の全容\n" + "\n".join(revealed["timeline"])
                         + f"\n手法：{case.method_how}／証拠：{case.method_evidence}")
        motive_overlay = (
            f"動機：{case.motive}\n{case.motive_reason}。\n"
            f"証拠：{case.motive_room}で見つかった「{case.motive_evidence}」"
            f"（{case.culprit.name}の名あり）"
        )
        reasoning_overlay = (
            "推理\n決定打：" + revealed["contradiction"] + "\n\n"
            + "\n".join(f"・{line}" for line in revealed["exclusions"])
        )
        parts = {
            "verdict": (reveal_text(case.culprit.name, case.method, case.motive),
                        verdict_overlay),
            "story": (reveal_story_text(case), story_overlay),
            "motive": (reveal_motive_text(case), motive_overlay),
            "reasoning": (reveal_reasoning_text(case), reasoning_overlay),
        }
        for step, (name, seconds) in enumerate(REVEAL_PARTS, start=1):
            self._write_view(state, case, session, phase="reveal", reveal_step=step)
            fallback, overlay = parts[name]
            self._speak(state, session, kind="reveal", fallback=fallback,
                        overlay=overlay, marker=f"reveal:{name}", allow_llm=False)
            self._grace_display(state, seconds)

    # --- Restore (yoso shape) -------------------------------------------------

    def _matches_active(self, canonical, identity):
        if canonical is None or not isinstance(canonical.get("active"), dict):
            return False
        try:
            actual = _identity(canonical["active"])
        except MysteryCornerError:
            return False
        return actual == identity

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
        except MysteryCornerError:
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

    def _restore(self, state, *, end_reason="duration", timeout_s=None,
                 allow_boundary_timeout_extension=True):
        identity = state.get("mystery_runtime_identity")
        if (not isinstance(identity, dict)
                or set(identity) != set(RUNTIME_IDENTITY_KEYS)
                or identity.get("game") != VIEW_NAME):
            raise MysteryCornerError("mystery runtime owner identity is missing before restore")
        if end_reason not in END_REASONS:
            end_reason = "duration"
        if state.get("status") != "restoring":
            state.update(status="restoring", restore_requested_at=self.clock(),
                         end_reason=end_reason)
            state["restore_request_id"] = str(uuid.uuid4())
            state["switch_request_id"] = state["restore_request_id"]
            self._save(state)
        else:
            end_reason = state.get("end_reason", end_reason)
        request_id = state.get("restore_request_id")
        if not isinstance(request_id, str) or request_id != state.get("switch_request_id"):
            raise MysteryCornerError("mystery restore request owner is invalid")
        canonical = self._canonical()
        if canonical is None:
            return "queued"
        if self._restore_receipt_finished(state, canonical):
            state.update(status="completed", completed_at=self.clock(), end_reason=end_reason,
                         restored_runtime_identity=(
                             _identity(canonical["active"])
                             if isinstance(canonical.get("active"), dict) else None
                         ))
            state.pop("switch_request_id", None)
            state.pop("switch_status", None)
            self._save(state)
            self._release_status_overlays(state)
            self._clear_program_flag(state)
            self._clear_stop_request()
            return "completed"
        if canonical.get("phase") == "draining" and self._matches_active(canonical, identity):
            pass  # GameSwitch accepts a durable request behind the active drain.
        elif not (_stable(canonical) and self._matches_active(canonical, identity)):
            if _stable(canonical):
                return self._mark_interrupted(state, "operator-moved-before-restore")
            raise MysteryCornerError("mystery restore source is not the owned runtime")

        previous = state.get("previous_game")
        payload = {"expected_source": identity}
        if previous is None:
            result = self.coordinator.stop(request_id=request_id, payload=payload,
                                           timeout_s=timeout_s)
        else:
            result = self.coordinator.switch(
                previous, request_id=request_id, payload=payload, timeout_s=timeout_s,
                allow_boundary_timeout_extension=allow_boundary_timeout_extension,
            )
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
            raise MysteryCornerError("mystery runtime restore did not succeed")
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
            raise MysteryCornerError("mystery restore result has no matching canonical owner")
        restored = _identity(canonical["active"]) if isinstance(canonical.get("active"), dict) else None
        state.update(status="completed", completed_at=self.clock(), end_reason=end_reason,
                     restored_runtime_identity=restored)
        state.pop("switch_request_id", None)
        state.pop("switch_status", None)
        self._save(state)
        self._release_status_overlays(state)
        self._clear_program_flag(state)
        self._clear_stop_request()
        return "completed"

    def recover_failed_restore(self):
        """Timer-safe restore replay for a finished view whose end switch failed."""
        state = self._read_state()
        if state.get("status") != "restoring":
            return "noop"
        request_id = state.get("restore_request_id")
        if not isinstance(request_id, str) or request_id != state.get("switch_request_id"):
            return "noop"
        record = state.get("restore_recovery")
        if not isinstance(record, dict) or not isinstance(record.get("request_id"), str):
            record = {"request_id": str(uuid.uuid4()), "original_request_id": request_id,
                      "created_at": self.clock()}
            state["restore_recovery"] = record
            self._save(state)
        if state.get("restore_request_id") != record["request_id"]:
            state["restore_request_id"] = record["request_id"]
            state["switch_request_id"] = record["request_id"]
            self._save(state)
        canonical = self._canonical()
        if canonical is None or canonical.get("phase") in {"stopping", "starting", "rolling_back"}:
            return "queued"
        return self._restore(state, end_reason=state.get("end_reason", "case-completed"),
                             timeout_s=RESTORE_RECOVERY_TIMEOUT_S,
                             allow_boundary_timeout_extension=False)

    # --- Rotation entry -------------------------------------------------------

    def run_rotation(self, request_id):
        request_id = self._request_id(request_id)
        state = self._read_state()
        if state.get("status") in {"completed", "interrupted", "failed", "idle"}:
            # A re-show that failed at a previous exit is retried on any
            # entry over a terminal state — same-request re-entry, recovery,
            # or the next rotation over this state file; once cleared, the
            # later entries call nothing again (review P2-1).
            self._release_status_overlays(state)
            self._clear_program_flag(state)
        if state.get("rotation_request_id") == request_id:
            status = state.get("status")
            if status in {"completed", "interrupted"}:
                return "completed"
            if status == "starting":
                if self._stop_requested():
                    switch_request_id = state.get("switch_request_id")
                    if (not isinstance(switch_request_id, str)
                            or switch_request_id != state.get("start_request_id")):
                        raise MysteryCornerError("mystery start owner has no matching switch request")
                    try:
                        receipt = self.store.receipts.load(switch_request_id)
                    except Exception:
                        raise MysteryCornerError("mystery start receipt cannot be verified") from None
                    if receipt is None:
                        return self._mark_interrupted(state, "operator-stopped-before-start")
                result = self._dispatch_start(state)
                if result is not None:
                    return result
            elif status == "active":
                return self._run_active_session(state)
            elif status == "restoring":
                return self._restore(state)
            else:
                raise MysteryCornerError("mystery rotation owner needs recovery")
        else:
            if state.get("status") not in {"idle", "completed", "interrupted"}:
                raise MysteryCornerError("another mystery execution owns the state")
            # A restore the previous run still owes survives the state
            # replacement: the next run keeps the obligation and retries it
            # on its exit paths even if OBS was down when the old run ended
            # (review P2).
            pending = state.get("status_overlay_hidden_sources")
            state = self._new_state(request_id)
            if state is None:
                return "queued"
            if isinstance(pending, list) and pending:
                state["status_overlay_hidden_sources"] = list(pending)
            if self._stop_requested():
                return self._mark_interrupted(state, "operator-stopped-before-start")
            self._save(state)
            result = self._dispatch_start(state)
            if result is not None:
                return result
        return self._run_active_session(state)

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
        except (OSError, ValueError, GameSwitchBusyError, MysteryCornerError):
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


#: Phases the broadcast payload may report (subset of the session machine).
_VIEW_PHASES = frozenset(
    {"intro", "investigate", "interrogate", "deduction", "prediction", "reveal", "done"})

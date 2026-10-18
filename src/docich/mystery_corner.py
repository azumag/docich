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
synchronous model every other corner uses): intro → investigate poll →
interrogate poll → deduction → culprit prediction → reveal → restore. A phase
that cannot run (no poll/prediction configuration, transport failure, or a
time budget too small) degrades to the seeded solo detective or is skipped,
but **once a prediction exists it is always settled or refunded** — the
commitment shown on air must never strand a bet.

Time bounds: ``ends_at`` (catalog duration) stays the hard cap; every wait is
clamped to the remaining budget minus the reveal/restore reserve. The reveal
always runs before the restore so the audience sees the answer the commitment
covered.
"""
from __future__ import annotations

import datetime as dt
import json
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
from .mystery_case import poll_actions, poll_question, public_board, reveal, verify_reveal
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

#: Time kept aside for the reveal display + the restore switch.
SESSION_RESERVE_S = 150.0
INTRO_SECONDS = 30
#: Twitch poll bounds are 15..1800s; the corner stays well inside them.
POLL_DURATION_S = 120
POLL_MIN_DURATION_S = 15
POLL_WAIT_POLL_S = 5.0
POLL_DISPLAY_SECONDS = 20
DEDUCTION_SECONDS = 45
PREDICTION_WINDOW_S = 300
PREDICTION_MIN_WINDOW_S = 30
PREDICTION_WAIT_POLL_S = 10.0
PREDICTION_SETTLE_ATTEMPTS = 5
REVEAL_GRACE_S = 60

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
                 poll_wait_poll_s=POLL_WAIT_POLL_S):
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
        try:
            return generate(session["seed"])
        except CaseError as exc:
            raise MysteryCornerError("mystery case generation failed") from exc

    def _remaining(self, state):
        ends_at = state.get("ends_at")
        if type(ends_at) not in (int, float) or isinstance(ends_at, bool):
            raise MysteryCornerError("mystery end time is invalid")
        return ends_at - self.clock()

    def _write_view(self, state, case, session, *, phase, poll=None, prediction=None):
        """Best-effort bounded broadcast payload write (never raises)."""
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
                "updated_at": self.clock(),
            }
            write_payload(Path(self.g.state_dir) / PAYLOAD_FILENAME, payload)
        except Exception:
            state["last_error_code"] = "view-write-failed"

    def _speak(self, state, session, *, kind, fallback, overlay, facts=None):
        """At-most-once narration for one phase marker; never raises."""
        marker = f"{kind}"
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
        if agents and kind != "reveal" and facts:
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
        session[f"{kind}_narration"] = {
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
            if interrupted is None and not self._stop_requested():
                self._phase_poll(state, case, session, "investigate")
                interrupted = slot_interrupted()
            if interrupted is None and not self._stop_requested():
                self._phase_poll(state, case, session, "interrogate")
                interrupted = slot_interrupted()
            if interrupted is None and not self._stop_requested():
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
            # Our runtime is gone; record the reveal in state without narration.
            if not session.get("revealed"):
                session["revealed"] = True
                self._write_view(state, case, session, phase="reveal")
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

    def _phase_deduction(self, state, case, session):
        session["phase"] = "deduction"
        self._write_view(state, case, session, phase="deduction")
        from .mystery_narration import deduction_text

        self._speak(state, session, kind="deduction", fallback=deduction_text(),
                    overlay="推理タイム：手がかりを整理して、最後の予想に備えてください")
        self._grace_display(state, DEDUCTION_SECONDS)

    def _phase_poll(self, state, case, session, phase):
        """One participatory vote; degrades to the seeded solo detective."""
        actions = poll_actions(case, phase)
        question = poll_question(case, phase)
        marker = f"{phase}:result"
        if session["announced"].get(marker):
            return
        client = self._poll_client(state)
        winner_value = None
        poll_view = None
        if client is not None and self._budget_left(state, minimum=30.0):
            winner_value, poll_view = self._run_poll(state, case, session, phase,
                                                     question, actions, client)
        if winner_value is None:
            # Solo mode: the seeded AI detective investigates so the case still
            # advances with zero participation (and the reveal stays verifiable).
            rng = random.Random(f"{session['seed']}:{phase}:solo")
            winner_value = ai_detective_choice(case, phase, rng)
        if phase == "investigate":
            from .mystery_case import clue_for_room, room_by_id
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
                    facts=facts)
        session["announced"][marker] = True
        self._save(state)
        self._grace_display(state, POLL_DISPLAY_SECONDS)

    def _budget_left(self, state, *, minimum):
        remaining = self._remaining(state) - SESSION_RESERVE_S
        return remaining >= minimum

    def _run_poll(self, state, case, session, phase, question, actions, client):
        """Create one poll, wait it out, return ``(winner_value, view)``.

        Any transport problem degrades to solo mode (``None`` winner): a
        failed vote never blocks the case or the reveal.
        """
        labels = [label for _value, label in actions]
        values = [value for value, _label in actions]
        poll_id = session.get(f"{phase}_poll_id")
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
                session[f"{phase}_poll_id"] = poll_id
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
        poll_view = self._poll_view(question, labels, values, row, votes)
        if votes is None or sum(votes) <= 0:
            return None, poll_view
        # Counters for the run ledger (trpg-core semantics, counts only).
        session["polls_held"] = (session.get("polls_held") or 0) + 1
        session["votes_total"] = (session.get("votes_total") or 0) + sum(votes)
        winner = winning_choice(votes)
        if winner is None or not 0 <= winner < len(values):
            return None, poll_view
        return values[winner], poll_view

    @staticmethod
    def _poll_view(question, labels, values, row, votes):
        if votes is None:
            votes = []
        status = row.get("status") if isinstance(row, dict) else None
        return {
            "question": question,
            "status": status if isinstance(status, str) else "unknown",
            "choices": [
                {"label": label, "value": value,
                 "votes": votes[index] if index < len(votes) else None}
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
        """Publish the answer and the verification facts. Fixed text only."""
        session["phase"] = "reveal"
        revealed = reveal(case)
        if not verify_reveal(revealed, case):
            raise MysteryCornerError("mystery reveal does not match the case")
        session["revealed"] = True
        self._write_view(state, case, session, phase="reveal")
        from .mystery_narration import reveal_text

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
        overlay = (
            f"犯人は {case.culprit.name}（{case.culprit.role}）\n"
            f"手法：{case.method}／動機：{case.motive}\n"
            f"commit {case.commitment[:16]}… seed {case.seed}\n{result_line}"
        )
        self._speak(state, session, kind="reveal",
                    fallback=reveal_text(case.culprit.name, case.method, case.motive),
                    overlay=overlay)
        self._grace_display(state, REVEAL_GRACE_S)

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
            state = self._new_state(request_id)
            if state is None:
                return "queued"
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

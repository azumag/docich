"""One-match Tsuitate corner lifecycle under the shared GameSwitch slot."""
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
    TERMINAL_RECEIPT_STATUSES,
    GameSwitchBusyError,
    GameSwitchCoordinator,
    GameSwitchStore,
    atomic_write_json,
)
from .tsuitate_beta_control import ControlError, call_beta_control
from .tsuitate_review import REVIEW_FAILURES
from .tsuitate_view import VIEW_NAME

STATE_FILENAME = "tsuitate_corner.json"
# Same bounded ceiling the retro restore recovery uses (#2199): the common
# scheduler owns the attempt budget and backoff, this caps one replay.
RESTORE_RECOVERY_TIMEOUT_S = 420.0
RUNTIME_IDENTITY_KEYS = ("game", "runtime_id", "generation", "lease_id")
PENDING_SWITCH_STATUSES = frozenset({"queued", "in_progress", "busy"})
ROLLBACK_ERROR_CODES = frozenset({
    ERROR_AGENT_START_FAILED,
    ERROR_PREPARE_FAILED,
    ERROR_QUIESCE_FAILED,
    ERROR_READINESS_TIMEOUT,
    ERROR_START_FAILED,
    # The request-wide deadline expiring during the start switch reports
    # ``timeout`` (``game_switch._failure_code``) and still runs the switch's
    # own rollback. The rolled-back receipt plus the stable previous owner is
    # the same proof the codes above are accepted for, so excluding this one
    # left such a start unterminalizable and latched the rotation (#2187).
    ERROR_TIMEOUT,
})
BETA_ACTIVE = frozenset({"queued", "playing", "draining"})
BETA_TERMINAL = frozenset({"stopped", "finished", "queue_timeout"})
# A single match must never hold the shared program slot forever. The queue
# wait and the connect/disconnect deadlines live in the Worker; this bound
# covers the match itself, which the remote site controls.
DEFAULT_MAX_MATCH_SECONDS = 1800.0
MAX_MATCH_SECONDS_LIMIT = 24 * 3600.0


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


def _sink_counts(value, total):
    """Bounded `sent`/`failed`/`skipped` counters for one announcement sink.

    A missing or malformed payload counts as "every item failed", which is what
    an injected announcer that raised looks like.
    """
    fallback = {"sent": 0, "failed": total, "skipped": 0}
    if not isinstance(value, dict):
        return fallback
    out = {}
    for key in ("sent", "failed", "skipped"):
        raw = value.get(key)
        out[key] = raw if type(raw) is int and 0 <= raw <= total else 0
    return out if sum(out.values()) <= total else fallback


class TsuitateCornerManager:
    #: Fixed 感想戦 boundary outcomes; `tsuitate_narration.BOUNDARY_OUTCOMES`
    #: is the source of truth and a regression test pins the parity.
    BOUNDARY_OUTCOMES = ("quiet", "timeout", "unavailable", "skipped")
    #: Fixed 感想戦 model outcomes; `tsuitate_narration.MODEL_FAILURES` is the
    #: source of truth and a regression test pins the parity.
    MODEL_FAILURES = ("none", "unsafe", "gate", "timeout", "rate_limit", "provider", "exception")

    def __init__(self, g, *, coordinator=None, control=None,
                 clock=time.time, sleep=time.sleep, poll_s=1.0,
                 max_match_seconds=DEFAULT_MAX_MATCH_SECONDS,
                 audio_enabled=False, review_agents="",
                 announce_intro=None, announce_review=None,
                 read_review=None, review_text=None, wait_boundary=None,
                 read_kifu=None, chunk_text=None, run_show=None, speak_chunk=None):
        self.g = g
        self.clock, self.sleep, self.poll_s = clock, sleep, float(poll_s)
        if (type(max_match_seconds) not in (int, float)
                or not 0 < float(max_match_seconds) <= MAX_MATCH_SECONDS_LIMIT):
            raise TsuitateCornerError("tsuitate max match seconds is out of range")
        if type(audio_enabled) is not bool:
            raise TsuitateCornerError("tsuitate audio_enabled must be boolean")
        if not isinstance(review_agents, str):
            raise TsuitateCornerError("tsuitate review_agents must be a string")
        for name, hook in (("announce_intro", announce_intro), ("announce_review", announce_review),
                           ("read_review", read_review), ("review_text", review_text),
                           ("wait_boundary", wait_boundary), ("read_kifu", read_kifu),
                           ("chunk_text", chunk_text), ("run_show", run_show),
                           ("speak_chunk", speak_chunk)):
            if hook is not None and not callable(hook):
                raise TsuitateCornerError(f"tsuitate {name} must be callable")
        self.max_match_seconds = float(max_match_seconds)
        # Speech is an explicit opt-in (`audio_enabled` in the catalog) and the
        # 感想戦 model chain is a separate opt-in (billed-run consent), exactly
        # like the other corner closings.
        self.audio_enabled = audio_enabled
        self.review_agents = review_agents
        self.announce_intro = announce_intro
        self.announce_review = announce_review
        self.read_review = read_review
        self.review_text = review_text
        # The 感想戦 waits for the shared audio queue's boundary (bounded,
        # fail-open) so it does not start inside a comment or announcement.
        self.wait_boundary = wait_boundary
        # The 感想戦 show: read the kifu, write it for the view, step it on air.
        self.read_kifu = read_kifu
        self.chunk_text = chunk_text
        self.run_show = run_show
        self.speak_chunk = speak_chunk
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
        # The view now owns the display: introduce the corner before the match
        # is found, exactly like the other narrated corners.
        self._announce_intro_once(state)
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
        # The public game id and the bot's colour are needed to read the public
        # replay for the 感想戦. Both are already public facts (the view shows
        # the colour) and the bridge validated their shape.
        game_id = status.get("gameId")
        if isinstance(game_id, str) and game_id:
            state["beta_game_id"] = game_id
        player_view = status.get("playerView")
        if isinstance(player_view, dict) and player_view.get("yourColor") in {"sente", "gote"}:
            state["beta_color"] = player_view["yourColor"]
        # Keep only the settled post-game summary, and only for the run this
        # corner owns. A result belonging to another run never reaches state.
        if state["beta_run_matches"] is True and status.get("state") == "finished":
            result = status.get("gameResult")
            if isinstance(result, dict):
                state["beta_result"] = {
                    key: result[key] for key in
                    ("outcome", "reason", "endedAt", "moveNumber", "resultConfidence")
                    if key in result
                }
        state.pop("last_error_code", None)
        self._save(state)
        return status

    # --- narration ---------------------------------------------------------

    @staticmethod
    def _added(previous, key, value):
        current = previous.get(key) if isinstance(previous, dict) else None
        return (current if type(current) is int else 0) + value

    def _record_announcements(self, state, result, *, overlay_total, speech_total):
        """Add this announcement to the run's bounded sink counters.

        The introduction and the 感想戦 are two announcements, so the counters
        are per-run totals: with speech on, a fully delivered run shows
        ``overlay_announcements.sent=2`` and ``narration.enqueued=2``.
        """
        overlay = _sink_counts((result or {}).get("overlay"), overlay_total)
        previous_overlay = state.get("overlay_announcements")
        state["overlay_announcements"] = {
            key: self._added(previous_overlay, key, overlay[key])
            for key in ("sent", "failed", "skipped")
        }
        if self.audio_enabled:
            speech = _sink_counts((result or {}).get("speech"), speech_total)
            previous = state.get("narration")
            state["narration"] = {
                "enqueued": self._added(previous, "enqueued", speech["sent"]),
                "delivery_failed": self._added(previous, "delivery_failed", speech["failed"]),
                "skipped": self._added(previous, "skipped", speech["skipped"]),
            }
        self._save(state)

    def _announce_intro_once(self, state):
        """Publish the corner introduction once per corner request.

        Best-effort and at-most-once: the attempt is recorded before the side
        effect so a crash or a resumed owner can never speak it twice.
        """
        request_id = state.get("rotation_request_id")
        if not isinstance(request_id, str) or state.get("intro_request_id") == request_id:
            return
        state["intro_request_id"] = request_id
        self._save(state)
        try:
            if self.announce_intro is None:
                from .tsuitate_narration import announce_intro

                result = announce_intro(self.g, request_id=request_id,
                                        audio_enabled=self.audio_enabled)
            else:
                result = self.announce_intro(self.g, request_id=request_id,
                                             audio_enabled=self.audio_enabled)
        except Exception:
            result = None
        self._record_announcements(state, result, overlay_total=1, speech_total=1)

    def _review_summary(self, state, terminal):
        """Read the verified public replay of this match.

        Returns ``(summary, failure)``.  The game id is the identity of the
        replay and is never read from the settled-result projection; the bot's
        colour is only a cross-check and may be recovered from the replay itself.
        """
        game_id = state.get("beta_game_id")
        color = state.get("beta_color")
        if not isinstance(game_id, str) or not game_id:
            return None, "invalid-game-id"
        try:
            reader = self.read_review
            if reader is None:
                from .tsuitate_review import read_summary_detail

                summary, failure = read_summary_detail(
                    game_id, terminal, color=color if color in {"sente", "gote"} else None)
            else:
                summary = reader(game_id, terminal, color=color)
                failure = "ok" if isinstance(summary, dict) else "unavailable"
        except Exception:
            summary, failure = None, "read-failed"
        if not isinstance(summary, dict):
            return None, failure if failure in REVIEW_FAILURES else "unavailable"
        return summary, "ok"

    def _wait_for_review_boundary(self):
        """Bounded wait for the shared audio queue's boundary before the 感想戦.

        Returns a fixed outcome for the state/diagnostics. Speech off means
        nothing is enqueued, so there is nothing to align with.
        """
        if not self.audio_enabled:
            return "skipped"
        try:
            if self.wait_boundary is None:
                from .tsuitate_narration import wait_for_audio_boundary

                outcome = wait_for_audio_boundary(self.g)
            else:
                outcome = self.wait_boundary(self.g)
        except Exception:
            return "unavailable"
        return outcome if outcome in self.BOUNDARY_OUTCOMES else "unavailable"

    def _review_kifu(self, state, terminal):
        """The verified kifu of this match, or ``None``.

        ``shogi_board.reconstruct`` only returns a board when it reproduced the
        site's own final position, so an unverified move list shows no board.
        """
        game_id = state.get("beta_game_id")
        color = state.get("beta_color")
        if not isinstance(game_id, str) or not game_id:
            return None
        try:
            reader = self.read_kifu
            if reader is None:
                from .tsuitate_review import read_kifu_detail

                detail, failure = read_kifu_detail(
                    game_id, terminal, color=color if color in {"sente", "gote"} else None)
            else:
                detail = reader(game_id, terminal, color=color)
                failure = "ok" if isinstance(detail, dict) else "unavailable"
        except Exception:
            return None
        return detail if isinstance(detail, dict) else None

    def _chunk_line(self, moves, start, end):
        """One commentary line for a stretch of the kifu (model, else fixed)."""
        try:
            if self.chunk_text is not None:
                outcome = self.chunk_text(self.g, moves, start, end)
                return outcome[0], outcome[1] if len(outcome) > 1 else "fallback"
            from .tsuitate_narration import chunk_commentary, review_agents

            agents = self.review_agents or review_agents(self.g)
            return chunk_commentary(self.g, agents, moves, start, end)
        except Exception:
            from .tsuitate_show import fallback_chunk_text

            return fallback_chunk_text(moves, start, end), "fallback"

    def _run_review_show(self, state, terminal, summary, request_id):
        """Step the kifu on screen with per-stretch commentary.

        Best effort and bounded: the view renders whatever was published, the
        corner never fails on it, and the whole show is capped by
        ``tsuitate_show.MAX_SHOW_SECONDS``.
        """
        from .tsuitate_show import (
            MAX_SHOW_SECONDS, STEP_SECONDS, build_show, chunk_ranges, step_plan,
            write_show,
        )

        detail = self._review_kifu(state, terminal)
        if detail is None:
            return None
        moves = detail["kifu"]["moves"]
        result = {
            "outcome": terminal.get("outcome"),
            "reason": terminal.get("reason"),
            "moveNumber": terminal.get("moveNumber"),
            "resultConfidence": terminal.get("resultConfidence"),
        }
        identity = state.get("view_runtime_identity") or {}
        payload = build_show(moves, color=detail["color"],
                             opponent=(summary or {}).get("opponentName"),
                             result=result, request_id=request_id,
                             runtime_id=identity.get("runtime_id"),
                             generation=identity.get("generation"))
        commentary = {"ai": 0, "fallback": 0}
        for start, end in chunk_ranges(payload["move_count"]):
            text, kind = self._chunk_line(moves, start, end)
            payload["commentary"][str(start)] = text
            commentary[kind if kind in commentary else "fallback"] += 1
        write_show(self.g.state_dir, payload)
        started = self.clock()
        plan = step_plan(payload, step_seconds=STEP_SECONDS)
        for chunk in plan:
            if self.clock() - started > MAX_SHOW_SECONDS:
                break
            write_show(self.g.state_dir, payload, index=chunk["start"])
            self._speak_chunk(chunk["text"], request_id)
            for number in range(chunk["start"], chunk["end"] + 1):
                write_show(self.g.state_dir, payload, index=number)
                self.sleep(STEP_SECONDS)
                if self.clock() - started > MAX_SHOW_SECONDS:
                    break
            # Let the spoken stretch finish before the next one starts.
            self._wait_for_review_boundary()
        write_show(self.g.state_dir, payload, index=payload["move_count"], done=True)
        return {
            "moves": payload["move_count"],
            "board": any(move.get("board") for move in moves),
            "commentary_ai": commentary["ai"],
            "commentary_fallback": commentary["fallback"],
            "seconds": max(0, int(self.clock() - started)),
        }

    def _speak_chunk(self, text, request_id):
        """Enqueue one commentary line; never raises."""
        if not self.audio_enabled or not isinstance(text, str) or not text:
            return False
        try:
            if self.speak_chunk is not None:
                self.speak_chunk(self.g, text, request_id)
                return True
            from .tsuitate_narration import REVIEW_CONTEXT
            from .trading.soren_output import enqueue_audio_text

            enqueue_audio_text(self.g, text, context=REVIEW_CONTEXT, speaker="")
            return True
        except Exception:
            return False

    def _announce_review_once(self, state):
        """Publish the 感想戦 once per corner request, after the result settled.

        The attempt is recorded before the side effect. The public replay is read
        transiently for the narration only: the raw move list is never stored,
        and only bounded counters and the spoken text survive in the state.
        """
        request_id = state.get("rotation_request_id")
        if not isinstance(request_id, str) or state.get("review_request_id") == request_id:
            return
        terminal = state.get("beta_result")
        if not isinstance(terminal, dict) or not terminal:
            return
        state["review_request_id"] = request_id
        self._save(state)
        terminal = dict(terminal)
        since = state.get("beta_active_since")
        if isinstance(since, (int, float)) and not isinstance(since, bool):
            terminal["durationMs"] = max(0, int((self.clock() - since) * 1000))
        summary, review_failure = self._review_summary(state, terminal)
        try:
            if self.review_text is None:
                from .tsuitate_narration import review_agents, review_text

                agents = self.review_agents or review_agents(self.g)
                text, kind, model_failure = review_text(self.g, summary, terminal, agents=agents)
            else:
                outcome = self.review_text(self.g, summary, terminal,
                                           agents=self.review_agents)
                # Injected hooks keep the older two-tuple contract.
                text, kind = outcome[0], outcome[1]
                model_failure = outcome[2] if len(outcome) > 2 else "none"
        except Exception:
            from .tsuitate_narration import fallback_text

            text, kind, model_failure = fallback_text(summary, terminal), "fallback", "exception"
        if not isinstance(text, str) or not text:
            return
        if kind not in {"ai", "fallback"}:
            kind = "fallback"
        if model_failure not in self.MODEL_FAILURES:
            model_failure = "provider" if model_failure else "none"
        from .tsuitate_narration import announce_review, overlay_body

        # Wait for the shared audio queue's boundary before the 感想戦 starts, so
        # it does not cut into a comment or announcement that is playing. The
        # wait is bounded and fails open: a busy queue never holds the shared
        # program slot and never loses the 感想戦. Only the spoken half needs it.
        boundary = self._wait_for_review_boundary()
        # Step the kifu on screen with its per-stretch commentary first, then
        # close with the spoken summary. Both are bounded and best-effort.
        try:
            show = (self.run_show(state, terminal, summary, request_id)
                    if self.run_show is not None
                    else self._run_review_show(state, terminal, summary, request_id))
        except Exception:
            show = None
        body = overlay_body(text, terminal)
        try:
            if self.announce_review is None:
                result = announce_review(self.g, text, request_id=request_id, body=body,
                                         audio_enabled=self.audio_enabled)
            else:
                result = self.announce_review(self.g, text, request_id=request_id, body=body,
                                              audio_enabled=self.audio_enabled)
        except Exception:
            result = None
        self._record_announcements(state, result, overlay_total=1, speech_total=1)
        # Bounded counters only: the move list, the board and the model prompt
        # never reach the state file.
        state["review"] = {
            "source": "replay" if summary is not None else "result-only",
            "kind": kind,
            "failure": review_failure,
            "boundary": boundary,
            "model_failure": model_failure,
            "show": show if isinstance(show, dict) else None,
            "moves": terminal.get("moveNumber") if type(terminal.get("moveNumber")) is int else None,
        }
        self._save(state)

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

    def _match_deadline_expired(self, state):
        """Whether this run has held the shared slot past the match bound.

        The first observation of an active beta run starts the clock, so a view
        switch or queue wait never consumes the match's own budget. The stamp is
        durable, so a restart cannot silently extend a corner's lifetime.
        """
        since = state.get("beta_active_since")
        if not isinstance(since, (int, float)) or isinstance(since, bool):
            since = self.clock()
            state["beta_active_since"] = since
            self._save(state)
        return self.clock() - since >= self.max_match_seconds

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
                if self._match_deadline_expired(state):
                    # Reuse the reviewed stop path rather than a new exit: the
                    # current game is drained to a terminal result first, and the
                    # restore below is unchanged.
                    if state.get("beta_timeout_requested") is not True:
                        state.update(beta_timeout_requested=True,
                                     beta_timeout_requested_at=self.clock())
                        self._save(state)
                    if not self._request_beta_stop(state):
                        return "queued"
                self.sleep(self.poll_s)
                continue
            if beta_state not in BETA_TERMINAL:
                raise TsuitateCornerError("beta returned an unknown lifecycle state")

            reason = {
                "finished": "game-completed",
                "queue_timeout": "queue-timeout",
                # An explicit operator stop is the more useful diagnosis, so it
                # wins over the deadline that may have run alongside it.
                "stopped": ("manual" if should_stop
                            else "match-timeout" if state.get("beta_timeout_requested") is True
                            else "beta-stopped"),
            }[beta_state]
            if state.get("view_lost") is True:
                return self._mark_interrupted(state, state.get("end_reason", "operator-moved"))
            # The settled result is known while the result card is still on
            # screen: read the public replay once and speak the 感想戦 before the
            # display returns to the base game. Both are best-effort and bounded;
            # the restore below is never failed by a missing public page.
            self._announce_review_once(state)
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

    def _restore(self, state, *, end_reason, timeout_s=None,
                 allow_boundary_timeout_extension=True):
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

    def recover_failed_restore(self):
        """Timer-safe restore replay for a finished view whose end switch failed.

        A terminal-failed restore request cannot be resumed (its receipt is
        terminal), so the first attempt records one fresh bounded replay request
        durably and later attempts reuse that exact id; only the recorded
        previous game is targeted. No match is started, no beta control call
        runs and no boundary extension is allowed. The common scheduler owns the
        attempt budget and backoff (#2199).
        """
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
        return self._restore(state, end_reason=state.get("end_reason", "game-completed"),
                             timeout_s=RESTORE_RECOVERY_TIMEOUT_S,
                             allow_boundary_timeout_extension=False)

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
                elif code == ERROR_ROLLBACK_FAILED:
                    # The start failed *and* its own rollback failed, so
                    # game-switch was left in ``failed`` with the previous
                    # runtime retained for a reviewed recovery.  A later
                    # recovery restored it.  Accept a terminal ``failed``
                    # receipt for this exact request only together with the
                    # canonical state that proves the restore finished: a
                    # stable owner running the exact previous game, nothing
                    # still in flight (no candidate/previous/retiring), and a
                    # generation that is not older than the one this corner
                    # started from.  A canonical that owns any other game, a
                    # lingering in-flight runtime, or a mismatched receipt
                    # keeps the slot latched.
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

    def settle_released_failure(self, request_id):
        """Retire a failed owner after rotation proved its slot released.

        Mirrors the paper/doushi contract (#2341): the state's own recorded
        request is the only candidate (a release record for a *newer* request
        is never permission to retire an older owner, #2352), a state that
        already became active is out of scope, and the canonical owner is
        re-checked here.

        An **absent** receipt is tolerated while a **non-terminal** one
        refuses. Delegating to :meth:`reconcile_failed_start` was too strict
        (#2655): it demands the terminal receipt that proves the rollback, and
        the 2026-10-16 receipt is old enough for the bounded receipt store to
        prune, so the owner stayed latched forever (#1992). Doushi's reviewed
        settle tolerates the same absent receipt for the same reason.

        A refusal is recorded as one fixed-vocabulary reason in the state so
        the next diagnosis does not have to guess which proof failed.
        """
        try:
            request_id = self._request_id(request_id)
        except TsuitateCornerError:
            return False
        try:
            with self.store.lock(exclusive=True):
                state = self._read_state()
                refusal = self._settle_refusal(state, request_id)
                if refusal is not None:
                    self._record_settle_refusal(state, refusal)
                    return False
                state.update(
                    status="interrupted",
                    completed_at=state.get("completed_at") or self.clock(),
                    end_reason="released-failure-before-retry",
                )
                state.pop("settle_refused_reason", None)
                state.pop("switch_request_id", None)
                state.pop("switch_status", None)
                self._save(state)
                self._clear_stop_request()
                return True
        except (OSError, ValueError, GameSwitchBusyError, TsuitateCornerError):
            return False

    def _settle_refusal(self, state, request_id):
        """Return the fixed reason this released owner cannot be retired."""

        if state.get("status") != "failed":
            return "not-failed"
        if state.get("rotation_request_id") != request_id:
            return "other-owner"
        if state.get("started_at") is not None:
            return "became-active"
        if state.get("view_runtime_identity") is not None:
            return "became-active"
        canonical, missing = self.store.canonical.load()
        if missing or not _stable(canonical):
            return "canonical-unstable"
        if (
            canonical.get("operation") is not None
            or canonical.get("request_id") is not None
            or canonical.get("deadline_at") is not None
        ):
            return "canonical-busy"
        active = canonical.get("active")
        if not isinstance(active, dict) or active.get("game") == VIEW_NAME:
            return "canonical-owns-view"
        receipt = self.store.receipts.load(request_id)
        if (isinstance(receipt, dict)
                and receipt.get("status") not in TERMINAL_RECEIPT_STATUSES):
            # A queued request also lands here: receipts.queued() derives from
            # these same rows, so a separate FIFO branch would be unreachable.
            return "receipt-open"
        return None

    def _record_settle_refusal(self, state, reason):
        """Persist one fixed reason without rewriting an unchanged value."""

        if state.get("settle_refused_reason") == reason:
            return
        state["settle_refused_reason"] = reason
        self._save(state)

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

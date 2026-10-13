"""Scheduled NetHack program corner.

The existing ``nethack`` CLI game remains the runtime of record and every game
lifecycle transition continues to go through ``GameSwitchCoordinator``.  When
the canonical game opts into ``persistent_run``, this layer also links each
program session to the durable expedition history in ``nethack_run``.

The graphical spectator renderer and gameplay policy are later phases of #490;
run history is intentionally independent from both presentation and AI.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import stat
import sys
import tomllib
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import ConfigError, GlobalConfig, load_game, load_global
from .corner_boundary import CornerWaitExpired, program_slot
from .nethack_run import NethackRunError, NethackRunStore
from .nethack_source import (
    SourceEvidenceError, new_session_context, runtime_identity,
    validate_session_context, verify_restoration,
)
from .retro_corner import CornerResult, RetroCornerError, RetroCornerManager, _safe_detail
from .trading.soren_output import enqueue_audio_text, enqueue_chat

GAME_NAME = "nethack"
STATE_FILE = "nethack_corner.json"
LOCK_FILE = "locks/nethack-corner.lock"
TICK_GUARD_FILE = "locks/nethack-corner-tick.lock"
DELIVERY_SOURCE = "nethack-corner"

# Viewer-facing claims stay capability-accurate: P1 adds durable run history,
# but graphical tiles and automated AI play are still later phases.
ANNOUNCE_TEXT = "NetHackコーナーです。ダンジョン探索をお送りします。"
END_ANNOUNCE_TEXT = "NetHackコーナーはここまでです。ありがとうございました。"

_VALID_WEEKDAYS = frozenset(range(7))

# A run boundary is detected from the visible TTY.  Unknown screens never end
# the corner early; they only count toward the stall guard.  These markers are
# the reviewed end-of-run prompts; deaths can otherwise sit on the "Do you want
# your possessions identified?" prompt until the stall guard fires.
_TERMINAL_SCREEN_MARKERS = (
    "you die",
    "you have died",
    "do you want your possessions identified",
    "you ascend",
    "you have ascended",
    "do you want to see what you had",
)


def _is_terminal_screen(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _TERMINAL_SCREEN_MARKERS)


class NethackCornerError(RetroCornerError):
    """User-facing failure in the scheduled NetHack corner."""


@dataclass(frozen=True)
class NethackCornerConfig:
    enabled: bool = False
    start_hour: int = 22
    duration_minutes: int = 30
    timezone: str = "Asia/Tokyo"
    weekdays: tuple[int, ...] = ()
    games: tuple[str, ...] = (GAME_NAME,)
    # NetHack runs have no natural minute boundary, so the corner ends on one
    # completed run (death/ascension) instead of a fixed duration.  A hang
    # guard ends the corner when the visible TTY has not changed for
    # ``stall_timeout_minutes``.
    run_boundary: bool = True
    stall_timeout_minutes: int = 10
    poll_interval_s: float = 5.0
    # RetroCornerManager calls the improve hook at finish. P0/P1 override that
    # hook; P3/P5 later add a bounded NetHack-specific improvement pipeline.
    improve_agents: str = ""
    improve_matches: int = 2
    improve_margin_pct: float = 10.0


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _raw_config(g: GlobalConfig) -> dict:
    if not g.config_path.is_file():
        return {}
    try:
        with g.config_path.open("rb") as stream:
            raw = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise NethackCornerError(
            f"nethack corner設定を読み込めません: {_safe_detail(exc)}"
        ) from exc
    if not isinstance(raw, dict):
        raise NethackCornerError("nethack corner設定のrootはtableである必要があります")
    return raw


def load_nethack_corner_config(g: GlobalConfig) -> NethackCornerConfig:
    raw = _raw_config(g).get("nethack_corner", {})
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise NethackCornerError("[nethack_corner] はtableである必要があります")

    weekdays_raw = raw.get("weekdays")
    if weekdays_raw is None:
        weekdays: tuple[int, ...] = ()
    else:
        if (
            not isinstance(weekdays_raw, list)
            or not weekdays_raw
            or any(type(day) is not int for day in weekdays_raw)
        ):
            raise NethackCornerError(
                "nethack_corner.weekdays は0(Mon)-6(Sun)の整数リストである必要があります"
            )
        if any(day not in _VALID_WEEKDAYS for day in weekdays_raw):
            raise NethackCornerError(
                "nethack_corner.weekdays は0(Mon)-6(Sun)の整数リストである必要があります"
            )
        if len(set(weekdays_raw)) != len(weekdays_raw):
            raise NethackCornerError("nethack_corner.weekdays に重複があります")
        weekdays = tuple(weekdays_raw)

    games_raw = raw.get("games", [GAME_NAME])
    if games_raw is None:
        games_raw = [GAME_NAME]
    if not isinstance(games_raw, list) or games_raw != [GAME_NAME]:
        raise NethackCornerError(
            f"nethack_corner.games は[{GAME_NAME!r}]固定です"
        )

    cfg = NethackCornerConfig(
        enabled=raw.get("enabled", False),
        start_hour=raw.get("start_hour", 22),
        duration_minutes=raw.get("duration_minutes", 30),
        timezone=raw.get("timezone", "Asia/Tokyo"),
        weekdays=weekdays,
        games=(GAME_NAME,),
        run_boundary=raw.get("run_boundary", True),
        stall_timeout_minutes=raw.get("stall_timeout_minutes", 10),
        poll_interval_s=raw.get("poll_interval_s", 5.0),
    )
    if type(cfg.enabled) is not bool:
        raise NethackCornerError("nethack_corner.enabled はtrue/falseである必要があります")
    if type(cfg.start_hour) is not int or not 0 <= cfg.start_hour <= 23:
        raise NethackCornerError("nethack_corner.start_hour は0-23の整数である必要があります")
    if type(cfg.duration_minutes) is not int or not 1 <= cfg.duration_minutes <= 720:
        raise NethackCornerError(
            "nethack_corner.duration_minutes は1-720の整数である必要があります"
        )
    if type(cfg.run_boundary) is not bool:
        raise NethackCornerError("nethack_corner.run_boundary はtrue/falseである必要があります")
    if type(cfg.stall_timeout_minutes) is not int or not 1 <= cfg.stall_timeout_minutes <= 1440:
        raise NethackCornerError(
            "nethack_corner.stall_timeout_minutes は1-1440の整数である必要があります"
        )
    if (
        type(cfg.poll_interval_s) not in (int, float)
        or not 0.5 <= cfg.poll_interval_s <= 60.0
    ):
        raise NethackCornerError(
            "nethack_corner.poll_interval_s は0.5-60の数値である必要があります"
        )
    if not isinstance(cfg.timezone, str) or not cfg.timezone.strip():
        raise NethackCornerError(
            "nethack_corner.timezone はIANA timezone文字列である必要があります"
        )
    try:
        ZoneInfo(cfg.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise NethackCornerError(
            f"nethack_corner.timezone が不正です: {cfg.timezone!r}"
        ) from exc
    return cfg


class NethackCornerManager(RetroCornerManager):
    """Fixed-game NetHack corner with isolated schedule and run history."""

    # 独自の終了告知を finish 直後に呼ぶため、ベースの結果まとめ投稿は使わない。
    announce_end_result = False

    def __init__(
        self,
        g: GlobalConfig,
        *,
        config: NethackCornerConfig | None = None,
        coordinator=None,
        now: Callable[[], dt.datetime] | None = None,
        sleep=None,
        active_game_reader=None,
        ensure_runtime=None,
        chat: Callable[[str], None] | None = None,
        voice: Callable[[str], None] | None = None,
        spawn=None,
        stream_game: Callable[[str], None] | None = None,
        runtime_screen: Callable[[], str | None] | None = None,
    ):
        if chat is None:
            chat = lambda text: enqueue_chat(g, text, source=DELIVERY_SOURCE)
        kwargs: dict = {
            "config": config or load_nethack_corner_config(g),
            "coordinator": coordinator,
            "active_game_reader": active_game_reader,
            "ensure_runtime": ensure_runtime,
            "chat": chat,
        }
        if now is not None:
            kwargs["now"] = now
        if sleep is not None:
            kwargs["sleep"] = sleep
        if spawn is not None:
            kwargs["spawn"] = spawn
        if stream_game is not None:
            kwargs["stream_game"] = stream_game
        super().__init__(g, **kwargs)
        self.state_path = Path(g.state_dir) / STATE_FILE
        self.lock_path = Path(g.state_dir) / LOCK_FILE
        self.tick_guard_path = Path(g.state_dir) / TICK_GUARD_FILE
        self._voice = voice or (
            lambda text: enqueue_audio_text(g, text, context="nethack:announce")
        )
        self._run_store = NethackRunStore.from_global(g)
        self._run_history_error: str | None = None
        self._runtime_screen = runtime_screen or self._default_runtime_screen

    def _default_runtime_screen(self) -> str | None:
        """Read-only capture of the committed NetHack game TTY.

        Only the canonical committed runtime is followed, and its
        generation-owned presentation window ownership is verified before
        anything is captured.  The capture itself must come from the runtime's
        birth window: ``game-g<N>`` merely runs the xterm that mirrors the game,
        so watching it saw a frozen banner while the game was being played --
        the corner then hit ``stall_timeout_minutes`` from its own start every
        time and never saw a death/ascension screen.  Any uncertainty returns
        None (counts as no progress), never as an input or a switch.
        """
        try:
            from .nethack_spectator_live import active_nethack_runtime, process_window_name
            from .tmux import Tmux
        except Exception:
            return None
        try:
            store = getattr(self.coordinator, "store", None)
            state, _migrated = store.canonical.load()
        except Exception:
            return None
        try:
            runtime = active_nethack_runtime(state)
        except Exception:
            return None
        if runtime is None:
            return None
        try:
            tmux = Tmux(runtime.adapter_session)
            if tmux.read_window_ownership(runtime.target) != runtime.ownership:
                return None
            name = process_window_name(tmux.list_windows(), runtime)
            if name is None:
                return None
            return tmux.capture_pane_checked(f"{runtime.adapter_session}:{name}")
        except Exception:
            return None

    def _wait_and_finish(self, state: dict[str, object]) -> CornerResult:
        if not self.config.run_boundary:
            return super()._wait_and_finish(state)
        return self._wait_run_boundary(state)

    def _wait_run_boundary(self, state: dict[str, object]) -> CornerResult:
        interval = float(self.config.poll_interval_s)
        stall_s = int(self.config.stall_timeout_minutes) * 60
        last_text: str | None = None
        unchanged_since = self._local_now()
        while True:
            stopped = self._rotation_stop_result()
            if stopped is not None:
                return stopped
            self._sleep(interval)
            with self._locked():
                latest = self._read_state()
            if latest.get("status") != "active":
                return self._state_result(latest)
            text = self._runtime_screen()
            now = self._local_now()
            if text is not None and text != last_text:
                last_text = text
                unchanged_since = now
            if text is not None and _is_terminal_screen(text):
                reason = "terminal"
            elif (now - unchanged_since).total_seconds() >= stall_s:
                reason = "stalled"
            else:
                continue
            with self._locked():
                latest = self._read_state()
                if latest.get("status") != "active":
                    return self._state_result(latest)
                latest["finish_reason"] = reason
                return self._finish_locked(latest, now)

    def recover_failed_rotation(self) -> CornerResult:
        """Settle the one proven failed automatic NetHack restore.

        Admission/cleanup/replay are serialized by the rotation and NetHack
        owner locks. Every resumed invocation must reuse the durable original
        restore request, source identity, and replay deadline. In particular,
        an immutable rolled-back receipt that reports incomplete cleanup is
        never altered: a separately persisted *later* cleanup proof is needed
        before existing replay logic may re-evaluate that receipt.
        """
        from .adapters.nethack import _is_dead_disclosure_prompt
        from .corner_rotation import CornerRotationManager, timestamp

        rotation = CornerRotationManager(self.g)
        with rotation.locked() as rotation_owned:
            if not rotation_owned:
                return CornerResult("queued", game=GAME_NAME, detail="rotation lock busy")
            with self._tick_guard() as single:
                if not single:
                    return CornerResult("queued", game=GAME_NAME, detail="NetHack tick is active")
                with self._locked():
                    state = self._read_state()
                    adopted = self._reconcile_adopted_return_locked(state, rotation)
                    if adopted is not None:
                        return adopted
                    if (state.get("game") == GAME_NAME
                            and state.get("status") in {"failed", "interrupted"}
                            and self._legacy_return_applies(state, rotation)):
                        return self._reconcile_legacy_return_locked(state, rotation)
                    request_id = state.get("rotation_request_id")
                    restore_id = state.get("switch_request_id")
                    status = state.get("status")
                    if (status not in {"failed", "interrupted"}
                            or state.get("game") != GAME_NAME
                            or state.get("previous_game") != "sorengame"
                            or not isinstance(request_id, str) or not request_id
                            or not isinstance(restore_id, str) or not restore_id
                            or request_id == restore_id
                            or not state.get("completed_at")):
                        return CornerResult("failed", game=GAME_NAME,
                                            detail="not a proven failed automatic restore")
                    ledger = rotation.load(timestamp(rotation.clock()))
                    pending = ledger.get("pending")
                    if (ledger.get("status") != "recovery_required"
                            or ledger.get("manual_pending") is not None
                            or not isinstance(pending, dict)
                            or pending.get("corner") != GAME_NAME
                            or pending.get("request_id") != request_id):
                        return CornerResult("failed", game=GAME_NAME,
                                            detail="rotation reservation differs")

                    original = self.store.receipts.load(restore_id)
                    result = original.get("result") if isinstance(original, dict) else None
                    if (not isinstance(result, dict)
                            or original.get("request_id") != restore_id
                            or original.get("operation") != "switch"
                            or original.get("target") != "sorengame"
                            or original.get("status") != "rolled_back"
                            or result.get("request_id") != restore_id
                            or result.get("operation") != "switch"
                            or result.get("status") != "rolled_back"
                            or result.get("from_game") != GAME_NAME
                            or result.get("to_game") != "sorengame"
                            or type(result.get("generation")) is not int
                            or result.get("generation") != original.get("generation")
                            or type(result.get("restored_generation")) is not int
                            or result.get("cleanup_pending") not in (None, False, True)):
                        return CornerResult("failed", game=GAME_NAME,
                                            detail="original restore receipt unproven")
                    canonical, missing = self.store.canonical.load()
                    if missing:
                        return CornerResult("failed", game=GAME_NAME,
                                            detail="canonical unavailable")

                    owner_keys = ("game", "runtime_id", "generation", "lease_id")
                    proof = state.get("restore_cleanup")
                    proof_valid = bool(
                        isinstance(proof, dict)
                        and set(proof) == {
                            "schema_version", "rotation_request_id",
                            "restore_request_id", "source",
                            "original_generation",
                        }
                        and proof.get("schema_version") == 1
                        and proof.get("rotation_request_id") == request_id
                        and proof.get("restore_request_id") == restore_id
                        and proof.get("original_generation") == result["generation"]
                        and isinstance(proof.get("source"), dict)
                        and set(proof["source"]) == set(owner_keys)
                        and proof["source"].get("game") == GAME_NAME
                        and proof["source"].get("generation") == result["restored_generation"]
                        and all(isinstance(proof["source"].get(key), str)
                                and bool(proof["source"].get(key))
                                for key in ("runtime_id", "lease_id"))
                    )
                    if proof is not None and not proof_valid:
                        return CornerResult("failed", game=GAME_NAME,
                                            detail="later cleanup evidence differs")

                    # Persist an intent before touching retiring Soren so a
                    # crash after canonical cleanup but before saving the
                    # result cannot erase the last correlated owner identity.
                    attempt = state.get("restore_cleanup_attempt")
                    attempt_valid = bool(
                        isinstance(attempt, dict)
                        and set(attempt) == {
                            "schema_version", "rotation_request_id",
                            "restore_request_id", "original_generation",
                            "source", "retiring",
                        }
                        and attempt.get("schema_version") == 1
                        and attempt.get("rotation_request_id") == request_id
                        and attempt.get("restore_request_id") == restore_id
                        and attempt.get("original_generation") == result["generation"]
                        and isinstance(attempt.get("source"), dict)
                        and set(attempt["source"]) == set(owner_keys)
                        and attempt["source"].get("game") == GAME_NAME
                        and attempt["source"].get("generation") == result["restored_generation"]
                        and isinstance(attempt.get("retiring"), list)
                        and bool(attempt["retiring"])
                        and all(isinstance(entry, dict)
                                and set(entry) == {
                                    "game", "adapter", "runtime_id",
                                    "generation", "lease_id",
                                }
                                and entry["game"] == "sorengame"
                                and entry["adapter"] == "soren"
                                and type(entry["generation"]) is int
                                and all(isinstance(entry.get(key), str)
                                        and bool(entry[key])
                                        for key in ("runtime_id", "lease_id"))
                                for entry in attempt["retiring"])
                    )
                    if attempt is not None and not attempt_valid:
                        return CornerResult("failed", game=GAME_NAME,
                                            detail="cleanup attempt ownership unproven")

                    record = state.get("restore_recovery")
                    if status == "interrupted":
                        # The prior operation committed a terminal corner before
                        # rotation latched its completion. Verify the same
                        # replay's landing; do not touch a game or mint an ID.
                        if not proof_valid or record is None:
                            return CornerResult("failed", game=GAME_NAME,
                                                detail="terminal cleanup evidence missing")
                        validated = self._restore_recovery_record(
                            state, game=GAME_NAME, previous="sorengame"
                        )
                        if (validated is None
                                or validated["expected_source"] != proof["source"]):
                            return CornerResult("failed", game=GAME_NAME,
                                                detail="terminal replay record unproven")
                        try:
                            replay = self._restore_recovery_receipt(
                                validated, game=GAME_NAME, previous="sorengame"
                            )
                        except Exception:
                            return CornerResult("failed", game=GAME_NAME,
                                                detail="terminal replay receipt unproven")
                        if (not isinstance(replay, dict)
                                or replay.get("status") != "succeeded"
                                or not self._restore_landed_proved(canonical, replay)):
                            return CornerResult("failed", game=GAME_NAME,
                                                detail="terminal restore landing unproven")
                        return CornerResult("succeeded", game=GAME_NAME,
                                            previous_game="sorengame",
                                            detail="same replay already committed")

                    if record is not None:
                        # A previous invocation persisted a bounded replay.
                        # It may now own a draining coordinator or have already
                        # committed Soren. The base recovery verifies its exact
                        # payload, receipt, and phase; the source must equal the
                        # cleanup proof of *this* rotation, or it is foreign.
                        if not proof_valid:
                            return CornerResult("failed", game=GAME_NAME,
                                                detail="pending replay cleanup unproven")
                        validated = self._restore_recovery_record(
                            state, game=GAME_NAME, previous="sorengame"
                        )
                        if (validated is None
                                or validated["expected_source"] != proof["source"]):
                            return CornerResult("failed", game=GAME_NAME,
                                                detail="pending replay record unproven")
                        try:
                            replay = self._restore_recovery_receipt(
                                validated, game=GAME_NAME, previous="sorengame"
                            )
                        except Exception:
                            return CornerResult("failed", game=GAME_NAME,
                                                detail="pending replay receipt unproven")
                        # A terminal failed/rolled-back replay may have renewed
                        # the CLI source's lease. The generic retro operator
                        # can then mint a replacement R2, but this fixed
                        # NetHack owner operation is authorized only for the
                        # original S0 -> R1 transition. Do not allocate R2,
                        # silently accept an updated lease, or launch a new
                        # expedition. Require separate owner investigation.
                        if (isinstance(replay, dict)
                                and replay.get("status") in {"failed", "rolled_back"}):
                            return CornerResult("failed", game=GAME_NAME,
                                                detail="terminal replay requires new owner review")
                        return self._recover_restore_failed(
                            state, late_cleanup_proved=True, one_shot_replay=True,
                        ) or CornerResult(
                            "failed", game=GAME_NAME,
                            detail="pending replay receipt unproven",
                        )

                    source = canonical.get("active")
                    retiring = canonical.get("retiring")
                    if (canonical.get("phase") != "ready"
                            or canonical.get("request_id") is not None
                            or canonical.get("candidate") is not None
                            or canonical.get("previous") is not None
                            or not isinstance(source, dict)
                            or source.get("game") != GAME_NAME
                            or source.get("adapter") != "cli"
                            or not isinstance(retiring, list)
                            or any(not isinstance(rt, dict)
                                   or rt.get("game") != "sorengame"
                                   or rt.get("adapter") != "soren"
                                   for rt in retiring)
                            or canonical.get("last_result") != result
                            or result["restored_generation"] != source.get("generation")):
                        return CornerResult("failed", game=GAME_NAME,
                                            detail="canonical rollback source unproven")
                    source_identity = {key: source.get(key) for key in owner_keys}
                    if attempt_valid and attempt["source"] != source_identity:
                        return CornerResult("failed", game=GAME_NAME,
                                            detail="cleanup attempt source differs")
                    if proof_valid and proof["source"] != source_identity:
                        return CornerResult("failed", game=GAME_NAME,
                                            detail="cleaned source identity differs")
                    if not _is_dead_disclosure_prompt(
                        self._runtime_screen() or ""
                    ):
                        # The generic analytics marker 'you die' can remain
                        # after life-saving. An HP-positive or unknown TTY is
                        # never authority to end a game. The adapter's
                        # exact HP-zero disclosure is the only entry gate.
                        return CornerResult("failed", game=GAME_NAME,
                                            detail="NetHack dead disclosure unproven")
                    if not proof_valid:
                        # A pending-cleanup original receipt requires a positive
                        # retiring source on the first attempt. Otherwise a
                        # stale receipt plus an already-empty retiring list
                        # could fabricate successful cleanup.
                        if result.get("cleanup_pending") is True and not retiring and not attempt_valid:
                            return CornerResult("failed", game=GAME_NAME,
                                                detail="late cleanup evidence missing")
                        if not attempt_valid and retiring:
                            state["restore_cleanup_attempt"] = {
                                "schema_version": 1,
                                "rotation_request_id": request_id,
                                "restore_request_id": restore_id,
                                "original_generation": result["generation"],
                                "source": source_identity,
                                "retiring": [
                                    {key: rt.get(key) for key in
                                     ("game", "adapter", "runtime_id", "generation", "lease_id")}
                                    for rt in retiring
                                ],
                            }
                            self._write_state(state)
                        if attempt_valid and retiring:
                            tracked = [
                                {key: rt.get(key) for key in
                                 ("game", "adapter", "runtime_id", "generation", "lease_id")}
                                for rt in retiring
                            ]
                            if tracked != attempt["retiring"]:
                                return CornerResult("failed", game=GAME_NAME,
                                                    detail="retiring ownership changed")
                        recovered = self.coordinator.recover(
                            timeout_s=120.0, expected_snapshot=canonical,
                        )
                        if (getattr(recovered, "status", None) != "succeeded"
                                or getattr(recovered, "cleanup_pending", True) is not False):
                            return CornerResult("queued", game=GAME_NAME,
                                                detail="retiring resource cleanup pending")
                        verified, missing = self.store.canonical.load()
                        keys = ("game", "adapter", "runtime_id", "generation", "lease_id")
                        after = verified.get("active")
                        if (missing or not self._restore_canonical_clean(
                                verified, late_cleanup_proved=True,
                            )
                                or not isinstance(after, dict)
                                or any(after.get(key) != source.get(key) for key in keys)
                                or self._restore_replay_source_proved(
                                    verified, original, late_cleanup_proved=True,
                                ) is None):
                            return CornerResult("failed", game=GAME_NAME,
                                                detail="restore source changed or cleanup unproven")
                        state["restore_cleanup"] = {
                            "schema_version": 1,
                            "rotation_request_id": request_id,
                            "restore_request_id": restore_id,
                            "source": source_identity,
                            "original_generation": result["generation"],
                        }
                        # The original immutable receipt may still indicate
                        # cleanup_pending. This separate fact is durable
                        # before preparing any new replay request.
                        self._write_state(state)
                    return self._recover_restore_failed(
                        state, late_cleanup_proved=True, one_shot_replay=True,
                    ) or CornerResult(
                        "failed", game=GAME_NAME, detail="restore receipt unproven"
                    )

    def _legacy_return_applies(self, state, rotation) -> bool:
        """Route an owner to the legacy contract; caller holds rotation/tick/corner locks.

        An owner without previous_game is legacy by format. One that records
        previous_game is routed here only when the read-only legacy proof
        already holds for it (or a legacy record was already started), so the
        modern restore recovery keeps every owner whose receipts carry source
        identities. Nothing is written.
        """
        from .game_switch import GameSwitchBusyError
        from .corner_rotation import timestamp
        from .nethack_return import LEGACY_PREVIOUS_GAME, RECORD_KEY, legacy_return_proof

        previous = state.get("previous_game", LEGACY_PREVIOUS_GAME)
        if "previous_game" not in state or RECORD_KEY in state:
            return previous == LEGACY_PREVIOUS_GAME
        if previous != LEGACY_PREVIOUS_GAME:
            return False
        try:
            with self.store.lock(exclusive=False):
                now = self._local_now().astimezone(dt.timezone.utc)
                ledger = rotation.load(timestamp(rotation.clock()))
                legacy_return_proof(Path(self.g.state_dir), state, ledger,
                                    player=self._run_store.settings.player_name, now=now)
            return True
        except GameSwitchBusyError:
            return True
        except Exception:
            return False

    def _adopted_return_runtime_checks(self, state, canonical):
        """Bounded read-only source absence and target liveness; never cleanup."""
        from .adapters.soren import SorenCoordinatorAdapter
        from .game_switch import RuntimeSpec
        from .naming import runtime_id_generation, validate_runtime_id
        from .tmux import Tmux
        import time
        original = validate_runtime_id(state.get("rotation_runtime_id"))
        # CLI generations have private sessions; a failed tmux inventory must
        # not be mistaken for an absent source. The normal shared server is
        # the one used by this coordinator's automatic CLI runtimes.
        raw = Tmux._run_bounded(["list-sessions", "-F", "#{session_name}"],
                                operation="automatic return source inventory",
                                max_output_bytes=65536, timeout_s=5.0)
        absent = f"docich-game-g{runtime_id_generation(original)}" not in raw.decode().splitlines()
        active = canonical.get("active") or {}
        if active.get("game") != "sorengame" or active.get("adapter") != "soren":
            return {"source_absent": absent, "target_alive": False}
        adapter = SorenCoordinatorAdapter(self.g, load_game(self.g, "sorengame"),
                    RuntimeSpec.from_runtime(Path(self.g.state_dir), active))
        return {"source_absent": absent, "target_alive": adapter.alive(time.monotonic() + 15, None)}

    def _reconcile_adopted_return_locked(self, state, rotation) -> CornerResult | None:
        """Owner-fenced adopted candidate return reconciliation; never replay a game.

        Caller holds rotation, tick and corner locks. The shared canonical
        lock remains held through evidence preparation and the terminal write.
        A crash may leave only a prepared record; retries must reprove the same
        complete snapshot and immutable evidence before committing it.
        """
        from .game_switch import GameSwitchBusyError
        from .corner_rotation import timestamp
        from .nethack_return import ADOPTED_RECORD_KEY, ReturnUnproven, adopted_return_proof, adopted_record_matches

        tracked = ADOPTED_RECORD_KEY in state
        eligible = False
        try:
            with self.store.lock(exclusive=False):
                canonical, missing = self.store.canonical.load()
                result = canonical.get("last_result")
                eligible = (not missing and isinstance(result, dict)
                            and result.get("operation") == "recover"
                            and isinstance(result.get("active_runtime"), dict))
                if not tracked and not eligible:
                    return None
                now = self._local_now().astimezone(dt.timezone.utc)
                ledger = rotation.load(timestamp(rotation.clock()))
                player = self._run_store.settings.player_name

                def prove():
                    return adopted_return_proof(
                        Path(self.g.state_dir), state, ledger, player=player, now=now,
                        **self._adopted_return_runtime_checks(state, canonical),
                    )

                proof = prove()
                record = state.get(ADOPTED_RECORD_KEY)
                if record is not None:
                    phase = adopted_record_matches(record, proof, now=now)
                    if state["status"] == "interrupted" and phase == "committed":
                        if prove() != proof:
                            raise ReturnUnproven("adopted return evidence changed")
                        return CornerResult("succeeded", game=GAME_NAME,
                                            previous_game="sorengame",
                                            detail="same adopted return already committed")
                    if state["status"] != "failed" or phase != "prepared":
                        raise ReturnUnproven("adopted return record phase differs")
                else:
                    if state["status"] != "failed":
                        raise ReturnUnproven("adopted terminal evidence missing")
                    # Re-read all evidence even with the honest-actor locks:
                    # external file drift is never silently accepted.
                    if prove() != proof:
                        raise ReturnUnproven("adopted return evidence changed")
                    state[ADOPTED_RECORD_KEY] = {
                        **proof, "phase": "prepared", "prepared_at": now.isoformat(),
                    }
                    self._write_state(state)
                if prove() != proof:
                    raise ReturnUnproven("adopted return evidence changed")
                state[ADOPTED_RECORD_KEY]["phase"] = "committed"
                state["status"] = "interrupted"
                self._write_state(state)
                return CornerResult("succeeded", game=GAME_NAME,
                                    previous_game="sorengame",
                                    detail="adopted return reconciled without resource operations")
        except GameSwitchBusyError:
            return CornerResult("queued", game=GAME_NAME, detail="canonical lock busy")
        except Exception:
            # Ordinary legacy/rollback callers never enter this separate proof.
            if not tracked and not eligible:
                return None
            return CornerResult("failed", game=GAME_NAME,
                                detail="adopted return evidence unproven or changed")

    def _reconcile_legacy_return_locked(self, state, rotation) -> CornerResult:
        """Owner-approved legacy metadata reconciliation; never replay a game.

        Caller holds rotation, tick and corner locks. The shared canonical
        lock remains held through evidence preparation and the terminal write.
        A crash may leave only a prepared record; retries must reprove the same
        complete snapshot and immutable evidence before committing it.
        """
        from .game_switch import GameSwitchBusyError
        from .corner_rotation import timestamp
        from .nethack_return import RECORD_KEY, ReturnUnproven, legacy_return_proof, saved_record_matches

        try:
            with self.store.lock(exclusive=False):
                now = self._local_now().astimezone(dt.timezone.utc)
                ledger = rotation.load(timestamp(rotation.clock()))
                player = self._run_store.settings.player_name

                def prove():
                    return legacy_return_proof(
                        Path(self.g.state_dir), state, ledger, player=player, now=now,
                    )

                proof = prove()
                record = state.get(RECORD_KEY)
                if record is not None:
                    phase = saved_record_matches(record, proof, now=now)
                    if state["status"] == "interrupted" and phase == "committed":
                        if prove() != proof:
                            raise ReturnUnproven("legacy return evidence changed")
                        return CornerResult("succeeded", game=GAME_NAME,
                                            previous_game="sorengame",
                                            detail="same legacy return already committed")
                    if state["status"] != "failed" or phase != "prepared":
                        raise ReturnUnproven("legacy return record phase differs")
                else:
                    if state["status"] != "failed":
                        raise ReturnUnproven("legacy terminal evidence missing")
                    # Re-read all evidence even with the honest-actor locks:
                    # external file drift is never silently accepted.
                    if prove() != proof:
                        raise ReturnUnproven("legacy return evidence changed")
                    state[RECORD_KEY] = {
                        **proof, "phase": "prepared", "prepared_at": now.isoformat(),
                    }
                    self._write_state(state)
                if prove() != proof:
                    raise ReturnUnproven("legacy return evidence changed")
                state[RECORD_KEY]["phase"] = "committed"
                state["status"] = "interrupted"
                state.pop("switch_status", None)
                self._write_state(state)
                return CornerResult("succeeded", game=GAME_NAME,
                                    previous_game="sorengame",
                                    detail="legacy return reconciled without resource operations")
        except GameSwitchBusyError:
            return CornerResult("queued", game=GAME_NAME, detail="canonical lock busy")
        except Exception:
            # Never export raw paths, receipts, player names, or exception text.
            return CornerResult("failed", game=GAME_NAME,
                                detail="legacy return evidence unproven or changed")

    @contextmanager
    def legacy_return_recovery_guard(self, reservation):
        """Fence legacy terminal observation through rotation's ledger save.

        Rotation already holds its owner lock. Modern observations retain the
        existing contract; a legacy owner must reprove its committed snapshot
        while holding tick/corner/shared canonical locks until ledger commit.
        """
        from .corner_rotation import RotationError
        from .nethack_return import (
            RECORD_KEY, legacy_return_proof, read_record, saved_record_matches,
            ADOPTED_RECORD_KEY, adopted_return_proof, adopted_record_matches,
        )

        state = self._read_state()
        adopted = ADOPTED_RECORD_KEY in state
        record_key = ADOPTED_RECORD_KEY if adopted else RECORD_KEY
        proof_fn = adopted_return_proof if adopted else legacy_return_proof
        record_fn = adopted_record_matches if adopted else saved_record_matches
        if (not adopted and RECORD_KEY not in state and
                ("previous_game" in state or state.get("game") != GAME_NAME)):
            yield
            return
        # Keep the try/except outside the yield: errors from rotation itself
        # must preserve their original classification and cannot be masked.
        from contextlib import ExitStack
        with ExitStack() as held:
            try:
                if not held.enter_context(self._tick_guard()):
                    raise RotationError("legacy NetHack tick is active", kind="execution-unverified")
                held.enter_context(self._locked())
                held.enter_context(self.store.lock(exclusive=False))
                state = self._read_state()
                root = Path(self.g.state_dir)
                ledger = read_record(root, ("corner_rotation.json",))
                if ledger.get("pending") != reservation:
                    raise ValueError("legacy rotation reservation changed")
                now = self._local_now().astimezone(dt.timezone.utc)
                canonical, _missing = self.store.canonical.load()
                def prove():
                    facts = self._adopted_return_runtime_checks(state, canonical) if adopted else {}
                    return proof_fn(root, state, ledger,
                                    player=self._run_store.settings.player_name, now=now, **facts)
                proof = prove()
                if (state.get("status") != "interrupted"
                        or record_fn(state.get(record_key), proof, now=now) != "committed"
                        or prove() != proof):
                    raise ValueError("legacy terminal return unproven")
            except Exception:
                raise RotationError("legacy terminal return unproven or changed",
                                    kind="execution-unverified") from None
            yield True

    def _validate_games(self, names: list[str] | None = None) -> None:
        """Validate the existing CLI runtime without pretending an AI exists yet.

        ``names`` carries the target-override contract of
        ``RetroCornerManager._begin_locked`` (a common rotation dispatch always
        passes the selected game). This manager owns exactly one game, so any
        other requested target is rejected instead of silently accepted --
        accepting no argument at all made every rotation dispatch die with a
        TypeError before the corner could record any state (#986).
        """
        if names is not None and list(names) != [GAME_NAME]:
            # Exact ownership, not membership: a superset such as
            # ``["nethack", "ninvaders"]` would silently pass an unvalidated
            # extra target (#998).
            detail = "、".join(str(name) for name in names) if names else "(空)"
            raise NethackCornerError(
                f"nethack corner対象は{GAME_NAME}だけが有効です: {detail}"
            )
        try:
            game = load_game(self.g, GAME_NAME)
        except Exception as exc:
            raise NethackCornerError(
                f"nethack corner対象を読み込めません ({GAME_NAME}): {_safe_detail(exc)}"
            ) from exc
        if game.adapter != "cli":
            raise NethackCornerError(
                f"nethack corner対象は既存CLI NetHackに限定されます: {GAME_NAME}"
            )

    def _remember_run_in_state(
        self, state: dict[str, object], run: dict[str, object] | None
    ) -> None:
        if run is None:
            return
        for source, target in (
            ("run_id", "run_id"),
            ("expedition", "expedition"),
            ("status", "run_status"),
            ("score", "run_score"),
            ("turns", "run_turns"),
            ("max_depth", "run_max_depth"),
            ("death_reason", "run_death_reason"),
            ("dump_file", "run_dump_file"),
        ):
            if source in run:
                state[target] = run[source]
        if self._run_history_error:
            state["run_history_error"] = self._run_history_error
        else:
            state.pop("run_history_error", None)

    def _prepare_start_state(self, state: dict[str, object], *, scheduled: bool) -> None:
        if "rotation_request_id" in state:
            origin = state.get("rotation_origin", "rotation")
            try:
                ledger = json.loads(
                    (Path(self.g.state_dir) / "corner_rotation.json").read_text(encoding="utf-8")
                )
                reservation = ledger.get(
                    "manual_pending" if origin == "manual" else "pending"
                )
                if (origin not in {"manual", "rotation"}
                        or not isinstance(reservation, dict)
                        or reservation.get("corner") != GAME_NAME
                        or reservation.get("request_id") != state["rotation_request_id"]):
                    origin = "unknown"
            except (OSError, ValueError, AttributeError, TypeError):
                origin = "unknown"
        else:
            origin = "scheduled" if scheduled else "manual"
        state["corner_context"] = new_session_context(
            origin, state["switch_request_id"]
        )

    def _transition_to(
        self,
        current: str | None,
        target: str,
        *,
        request_id: str | None = None,
    ):
        probe: dict[str, object] | None = None
        if self._run_store is not None and target == GAME_NAME:
            try:
                probe = self._prepare_start_with_reconcile(current)
            except NethackRunError as exc:
                raise NethackCornerError(
                    f"NetHack run continuityを確認できません: {_safe_detail(exc)}"
                ) from exc

        result = super()._transition_to(current, target, request_id=request_id)
        if getattr(result, "status", None) in {"queued", "in_progress", "busy"}:
            return result

        if self._run_store is not None and probe is not None:
            try:
                state = self._read_state()
                context = validate_session_context(state.get("corner_context"))
                if context["start_request_id"] != request_id:
                    raise NethackCornerError("NetHack start request identity changed")
                self._run_store.record_started(
                    probe, now=self._local_now(), corner_context=context,
                    runtime=self._confirmed_started_runtime(result),
                )
                self._run_history_error = None
            except Exception as exc:
                # The coordinator transition already succeeded. Do not destroy
                # or roll back a live game solely because analytics/history
                # persistence failed; surface the error in corner state instead.
                self._run_history_error = _safe_detail(exc)
        return result

    def _confirmed_started_runtime(self, result) -> dict[str, object] | None:
        """Pin a start to the committed canonical runtime, if readable."""
        receipt = getattr(result, "receipt", None)
        store = getattr(self.coordinator, "store", None)
        if not isinstance(receipt, Mapping) or store is None:
            return None
        try:
            with store.lock(exclusive=False):
                canonical, _ = store.canonical.load()
                active = runtime_identity(canonical.get("active"))
                if (active["game"] == GAME_NAME
                        and active["generation"] == receipt.get("generation")
                        and active["runtime_id"] == receipt.get("runtime_id")
                        and receipt.get("request_id") == result.request_id
                        and receipt.get("status") == "succeeded"):
                    return active
        except Exception:
            return None
        return None

    def _prepare_start_with_reconcile(self, current: str | None) -> dict[str, object]:
        """Close an unreachable leftover run before starting a new expedition.

        A failed corner finish (for example a switch-back boundary timeout)
        can leave the run history ``active`` after the NetHack runtime and save
        are both gone.  ``NethackRunStore.prepare_start`` correctly fails
        closed on that state, but with no runtime/save there is nothing left to
        protect, and without reconciliation every future start would be blocked
        forever.  Close it explicitly as ``ended_unknown`` (never invent a
        death) and retry once.  When NetHack is still the canonical game the
        original fail-closed error is preserved.
        """
        assert self._run_store is not None
        is_nethack = current == GAME_NAME
        try:
            return self._run_store.prepare_start(
                current_is_nethack=is_nethack, now=self._local_now()
            )
        except NethackRunError:
            if is_nethack:
                raise
        try:
            reconciled = self._run_store.record_finished(
                now=self._local_now(), nethack_still_active=False
            )
        except NethackRunError as exc:
            raise NethackRunError(f"到達不能runを回収できません: {_safe_detail(exc)}") from exc
        print(
            f"[nethack-corner] reconciled unreachable run status={reconciled.get('status')}",
            file=sys.stderr,
        )
        return self._run_store.prepare_start(
            current_is_nethack=False, now=self._local_now()
        )

    def _announce_start_locked(self, state: dict[str, object]) -> None:
        if state.get("announced"):
            return
        run: dict[str, object] | None = None
        if self._run_store is not None:
            try:
                run = self._run_store.current()
            except Exception as exc:
                self._run_history_error = _safe_detail(exc)
            self._remember_run_in_state(state, run)

        text = ANNOUNCE_TEXT
        expedition = state.get("expedition")
        if type(expedition) is int and expedition > 0:
            text = f"第{expedition}次遠征。{ANNOUNCE_TEXT}"
        try:
            self._chat(text)
        except Exception as exc:
            state["announce_error"] = _safe_detail(exc)
            return
        try:
            self._voice(text)
        except Exception as exc:
            state["voice_error"] = _safe_detail(exc)
        state["announced"] = True
        state.pop("announce_error", None)

    def _end_announcement(self, state: dict[str, object]) -> str:
        status = state.get("run_status")
        expedition = state.get("expedition")
        prefix = f"第{expedition}次遠征は" if type(expedition) is int else "今回の遠征は"
        if status == "suspended":
            return f"{prefix}生存しています。続きは次回です。"
        if status == "ascended":
            return f"{prefix}昇天しました。NetHack攻略成功です。"
        if status == "dead":
            reason = state.get("run_death_reason")
            if isinstance(reason, str) and reason:
                return f"{prefix}終了しました。記録上の死因は、{reason}です。"
            return f"{prefix}終了しました。死因は記録から確認します。"
        if status == "ended_unknown":
            return f"{prefix}終了しました。終了理由は記録から確定できませんでした。"
        return END_ANNOUNCE_TEXT

    def _announce_end_locked(self, state: dict[str, object]) -> None:
        if state.get("end_announced"):
            return
        text = self._end_announcement(state)
        try:
            self._chat(text)
        except Exception as exc:
            state["end_announce_error"] = _safe_detail(exc)
        try:
            self._voice(text)
        except Exception as exc:
            state["end_voice_error"] = _safe_detail(exc)
        state["end_announced"] = True

    def _finish_locked(
        self, state: dict[str, object], completed_at: dt.datetime
    ) -> CornerResult:
        source_session = self._source_session_before_restore(state)
        source = None
        if self._run_store is not None:
            try:
                canonical, _ = self.coordinator.store.canonical.load()
                active = canonical.get("active")
                if isinstance(active, dict) and active.get("game") == GAME_NAME:
                    source = dict(active)
            except Exception:
                pass  # Missing analytics evidence never changes the switch.
        terminal_run = None
        eligible = (state.get("status") in {"active", "restoring"}
                    and state.get("finish_reason") == "terminal"
                    and isinstance(state.get("run_id"), str))
        if eligible:
            from .corner_ownership import verify_runtime
            verify_runtime(self.store, state, GAME_NAME)
            # Freeze the first observation across queued restore retries. The
            # later restore/write time must not admit a new rollback adventure.
            state.setdefault("terminal_observed_at", completed_at.isoformat())

        def record_terminal():
            if not eligible or self._run_store is None:
                return None
            try:
                run = self._run_store.record_confirmed_terminal(
                    expected_run_id=state["run_id"], now=self._local_now(),
                    terminal_observed_at=dt.datetime.fromisoformat(state["terminal_observed_at"]),
                )
                if run is not None:
                    self._run_history_error = None
                    self._remember_run_in_state(state, run)
                return run
            except Exception as exc:
                self._run_history_error = _safe_detail(exc)
                state["run_history_error"] = self._run_history_error
                return None

        # Preserve proved death before restore: rollback may create another
        # NetHack process, and restore failures must not erase this expedition.
        terminal_run = record_terminal()
        try:
            result = super()._finish_locked(state, completed_at)
        except Exception:
            # A terminal observation uses the bounded xlog path below. The
            # process-exit fallback must not override that cutoff or its error.
            if not eligible and state.get("terminal_observed_at") is None:
                self._remember_failed_restore_terminal(state, source, completed_at)
            raise
        finally:
            # xlog can arrive after the terminal screen. Recheck after a failed
            # or queued restore too, without masking the coordinator's error.
            if terminal_run is None and state.get("status") != "interrupted":
                terminal_run = record_terminal()
            if eligible:
                try:
                    self._write_state(state)
                except Exception:
                    pass
        if (self._run_store is not None and result.status == "completed"
                and terminal_run is None):
            try:
                run = self._run_store.record_finished(
                    now=completed_at,
                    nethack_still_active=self._active_game_reader() == GAME_NAME,
                )
                self._run_history_error = None
                self._remember_run_in_state(state, run)
            except Exception as exc:
                # GameSwitchCoordinator has already completed its safe switch.
                # History failure is visible and retryable, not a reason to
                # disturb the saved/terminal game state after the fact.
                self._run_history_error = _safe_detail(exc)
                state["run_history_error"] = self._run_history_error
        if result.status == "completed":
            self._record_post_restore_source(state, source_session)
        if result.status in {"completed", "interrupted"}:
            self._announce_end_locked(state)
        try:
            self._write_state(state)
        except Exception:
            pass
        return result

    def _remember_failed_restore_terminal(self, state, source, completed_at) -> None:
        if self._run_store is None or not isinstance(source, dict):
            return
        run_id = state.get("run_id")
        request_id = state.get("switch_request_id")
        if not isinstance(run_id, str) or not isinstance(request_id, str):
            return
        expected_runtime = state.get("rotation_runtime_id")
        if expected_runtime is not None and source.get("runtime_id") != expected_runtime:
            return
        try:
            from .naming import runtime_directory
            from .adapters.nethack import BOUNDARY_RESULT_FILENAME

            canonical, _ = self.coordinator.store.canonical.load()
            previous = canonical.get("previous")
            keys = ("game", "runtime_id", "generation", "lease_id")
            if (canonical.get("active") is not None or not isinstance(previous, dict)
                    or any(previous.get(key) != source.get(key) for key in keys)):
                return
            # This is the adapter's durable, request-bound acknowledgement of
            # actual process exit, NOT an old TTY message or a missing pane.
            path = runtime_directory(self.g.state_dir, source["runtime_id"]) / BOUNDARY_RESULT_FILENAME
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as handle:
                metadata = os.fstat(handle.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 4096:
                    return
                raw = handle.read(4097)
            if len(raw) > 4096:
                return
            boundary = json.loads(raw)
            if (not isinstance(boundary, dict)
                    or type(boundary.get("schema_version")) is not int
                    or boundary["schema_version"] != 1
                    or boundary.get("request_id") != request_id
                    or boundary.get("player_name") != self._run_store.settings.player_name
                    or type(boundary.get("generation")) is not int
                    or any(boundary.get(key) != source.get(key) for key in keys[:-1])
                    or boundary.get("outcome") not in {"ended", "suspended"}):
                return
            run = self._run_store.record_finished(
                now=completed_at, nethack_still_active=False, expected_run_id=run_id,
            )
            self._remember_run_in_state(state, run)
            self._write_state(state)
        except Exception as exc:
            # Preserve the original failure and never mutate runtime state or
            # turn a failed restore into a completed corner.
            state["run_history_error"] = _safe_detail(exc)
            try:
                self._write_state(state)
            except Exception:
                pass

    def _observed_runtime(self) -> dict[str, object] | None:
        store = getattr(self.coordinator, "store", None)
        if store is None:
            return None
        try:
            with store.lock(exclusive=False):
                canonical, _ = store.canonical.load()
                return runtime_identity(canonical.get("active"))
        except Exception:
            return None

    def _source_session_before_restore(
        self, state: dict[str, object]
    ) -> dict[str, object] | None:
        """Pin the run/session and NetHack runtime before the restore runs.

        Analytics only: any failure leaves the snapshot empty and the source
        unrecorded; it never affects the switch or terminal history.
        """
        if self._run_store is None or not isinstance(state.get("run_id"), str):
            return None
        try:
            run = self._run_store.load_run(state["run_id"])
            sessions = run.get("sessions")
            session = sessions[-1] if isinstance(sessions, list) and sessions else None
            if not isinstance(session, dict) or not isinstance(session.get("session_id"), str):
                return None
            return {
                "run_id": run["run_id"],
                "session_id": session["session_id"],
                "runtime": session.get("runtime"),
                "before": self._observed_runtime(),
            }
        except Exception:
            return None

    def _record_post_restore_source(
        self, state: dict[str, object], snapshot: dict[str, object] | None
    ) -> None:
        """Seal a post-restore observation onto the finished session.

        Never authorizes improvement (authorized_mode=off). The restore has
        already completed; failures are recorded as a fixed status only.
        """
        if self._run_store is None:
            return
        if snapshot is None:
            state["source_evidence_status"] = "session_unverified"
            return
        restoration = None
        store = getattr(self.coordinator, "store", None)
        request_id = state.get("restore_request_id")
        try:
            if store is None or not isinstance(request_id, str):
                raise SourceEvidenceError("restore_unverified")
            with store.lock(exclusive=False):
                receipt = store.receipts.load(request_id)
                canonical, _ = store.canonical.load()
            restoration = verify_restoration(
                request_id=request_id, source_runtime=snapshot["runtime"],
                before=snapshot["before"], receipt=receipt, canonical=canonical,
                previous_game=state.get("previous_game"),
            )
            status = "recorded"
        except SourceEvidenceError as exc:
            status = str(exc)
        except Exception:
            status = "source_internal_error"
        try:
            run = self._run_store.attach_post_restore_source(
                run_id=snapshot["run_id"], session_id=snapshot["session_id"],
                restoration=restoration,
                finish_reason=state.get("finish_reason", "unknown"),
            )
            sessions = run.get("sessions")
            session = sessions[-1] if isinstance(sessions, list) and sessions else {}
            if restoration is not None:
                status = ("recorded" if session.get("post_restore_source")
                          else session.get("post_restore_source_error", "unverified"))
        except Exception:
            status = "source_internal_error" if restoration is not None else status
        state["source_evidence_status"] = status

    def _spawn_improve_once(self, state: dict[str, object]) -> None:
        # P1 records evidence only. P3/P5 add a bounded, testable strategy
        # improvement pipeline over these records.
        return

    def _due_on_weekday(self, now: dt.datetime) -> bool:
        return not self.config.weekdays or now.weekday() in self.config.weekdays

    def tick(self) -> CornerResult:
        from .corner_catalog import rotation_enabled
        if rotation_enabled(self.g):
            return super().tick()
        with self._tick_guard() as single:
            if not single:
                return CornerResult("noop", detail="already-running")
            now = self._local_now()
            restoring = self._retry_restoring_tick(now)
            if restoring is not None:
                return restoring
            starting = self._retry_starting_tick(now)
            if starting is not None:
                return starting
            if not self.config.enabled:
                with self._locked():
                    self._reconcile_stale_locked(now)
                return CornerResult("noop", detail="disabled")
            if now.hour != self.config.start_hour or not self._due_on_weekday(now):
                with self._locked():
                    self._reconcile_stale_locked(now)
                return CornerResult("noop", detail="outside-window")
            end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0)
            try:
                with program_slot(
                    self.g,
                    self.state_path,
                    requested_at=now.timestamp(),
                    wait_deadline_ts=end_of_day.timestamp(),
                    wait_boundary=False,
                    sleep=self._sleep,
                    now=lambda: self._local_now().timestamp(),
                ):
                    # The slot may have been queued behind another corner. Re-read
                    # wall clock before firing so a late grant cannot start NetHack
                    # outside its configured hour/day.
                    return self._scheduled_tick(self._local_now())
            except CornerWaitExpired:
                return CornerResult("expired", detail="program-wait-expired")

    def _scheduled_tick(self, now: dt.datetime) -> CornerResult:
        if now.hour != self.config.start_hour or not self._due_on_weekday(now):
            with self._locked():
                self._reconcile_stale_locked(now)
            return CornerResult("noop", detail="outside-window")
        with self._locked():
            state, result = self._begin_locked(now, scheduled=True)
        if result is not None:
            return result
        assert state is not None
        return self._wait_and_finish(state)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docich nethack-corner")
    parser.add_argument("--config", metavar="PATH")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("tick")
    sub.add_parser("start")
    sub.add_parser("stop")
    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")
    sub.add_parser("recover")
    sub.add_parser("recover-failed-rotation")
    return parser


def _recover_json(result) -> str:
    return json.dumps(
        {
            "status": result.status,
            "operation": result.operation,
            "error_code": result.error_code,
            "detail": result.detail,
            "cleanup_pending": result.cleanup_pending,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config_path = Path(args.config) if args.config else None
    try:
        g = load_global(_repo_root(), config_path)
        manager = NethackCornerManager(g)
        if args.command == "status":
            state = manager.status()
            if args.json:
                print(json.dumps(state, ensure_ascii=False, separators=(",", ":")))
            else:
                print(
                    "nethack-corner: "
                    f"status={state.get('status')} game={state.get('game')} "
                    f"previous={state.get('previous_game')} ends_at={state.get('ends_at')}"
                )
            return 0
        if args.command == "recover-failed-rotation":
            result = manager.recover_failed_rotation()
            print(json.dumps({"status": result.status, "game": GAME_NAME},
                             separators=(",", ":")))
            return 0 if result.status == "succeeded" else 4
        if args.command == "recover":
            result = manager.coordinator.recover()
            print(_recover_json(result))
            return 0
        result = getattr(manager, args.command)()
        print(
            json.dumps(
                {
                    "status": result.status,
                    "game": result.game,
                    "previous_game": result.previous_game,
                    "detail": result.detail,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 0
    except (ConfigError, RetroCornerError, NethackRunError, RuntimeError) as exc:
        print(f"docich: エラー: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

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
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import ConfigError, GlobalConfig, load_game, load_global
from .corner_boundary import CornerWaitExpired, program_slot
from .nethack_run import NethackRunError, NethackRunStore
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

    def _validate_games(self) -> None:
        """Validate the existing CLI runtime without pretending an AI exists yet."""
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
                self._run_store.record_started(probe, now=self._local_now())
                self._run_history_error = None
            except Exception as exc:
                # The coordinator transition already succeeded. Do not destroy
                # or roll back a live game solely because analytics/history
                # persistence failed; surface the error in corner state instead.
                self._run_history_error = _safe_detail(exc)
        return result

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
        result = super()._finish_locked(state, completed_at)
        if self._run_store is not None and result.status == "completed":
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
        self._announce_end_locked(state)
        try:
            self._write_state(state)
        except Exception:
            pass
        return result

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

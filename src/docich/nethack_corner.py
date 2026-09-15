"""Scheduled NetHack program corner.

This module deliberately owns only program scheduling/orchestration.  The
existing ``nethack`` CLI game remains the runtime of record and every game
lifecycle transition continues to go through ``GameSwitchCoordinator``.

The graphical spectator renderer, persistent run store, and gameplay policy
are added in later phases of #490.  Keeping this P0 layer small lets those
features evolve without weakening the existing text-observation contract.
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
from .retro_corner import CornerResult, RetroCornerError, RetroCornerManager, _safe_detail
from .trading.soren_output import enqueue_audio_text, enqueue_chat

GAME_NAME = "nethack"
STATE_FILE = "nethack_corner.json"
LOCK_FILE = "locks/nethack-corner.lock"
TICK_GUARD_FILE = "locks/nethack-corner-tick.lock"
DELIVERY_SOURCE = "nethack-corner"

ANNOUNCE_TEXT = (
    "NetHackコーナーです。ダンジョン探索を続けます。"
    "生き残れば次回へ続き、倒れた場合はその冒険の記録を残します。"
)
END_ANNOUNCE_TEXT = "NetHackコーナーはここまでです。続きは次回の探索でお送りします。"

_VALID_WEEKDAYS = frozenset(range(7))


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
    # RetroCornerManager calls the improve hook at finish. P0 overrides that
    # hook, but keep these fields for a stable config shape for later phases.
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
    )
    if type(cfg.enabled) is not bool:
        raise NethackCornerError("nethack_corner.enabled はtrue/falseである必要があります")
    if type(cfg.start_hour) is not int or not 0 <= cfg.start_hour <= 23:
        raise NethackCornerError("nethack_corner.start_hour は0-23の整数である必要があります")
    if type(cfg.duration_minutes) is not int or not 1 <= cfg.duration_minutes <= 720:
        raise NethackCornerError(
            "nethack_corner.duration_minutes は1-720の整数である必要があります"
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
    """Fixed-game NetHack corner with state isolated from every other corner."""

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
        super().__init__(g, **kwargs)
        self.state_path = Path(g.state_dir) / STATE_FILE
        self.lock_path = Path(g.state_dir) / LOCK_FILE
        self.tick_guard_path = Path(g.state_dir) / TICK_GUARD_FILE
        self._voice = voice or (
            lambda text: enqueue_audio_text(g, text, context="nethack:announce")
        )

    def _validate_games(self) -> None:
        """P0 validates the existing runtime without pretending an AI exists yet."""
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

    def _announce_start_locked(self, state: dict[str, object]) -> None:
        if state.get("announced"):
            return
        try:
            self._chat(ANNOUNCE_TEXT)
        except Exception as exc:
            state["announce_error"] = _safe_detail(exc)
            return
        try:
            self._voice(ANNOUNCE_TEXT)
        except Exception as exc:
            state["voice_error"] = _safe_detail(exc)
        state["announced"] = True
        state.pop("announce_error", None)

    def _announce_end_locked(self, state: dict[str, object]) -> None:
        if state.get("end_announced"):
            return
        try:
            self._chat(END_ANNOUNCE_TEXT)
        except Exception as exc:
            state["end_announce_error"] = _safe_detail(exc)
        try:
            self._voice(END_ANNOUNCE_TEXT)
        except Exception as exc:
            state["end_voice_error"] = _safe_detail(exc)
        state["end_announced"] = True

    def _finish_locked(
        self, state: dict[str, object], completed_at: dt.datetime
    ) -> CornerResult:
        result = super()._finish_locked(state, completed_at)
        self._announce_end_locked(state)
        try:
            self._write_state(state)
        except Exception:
            pass
        return result

    def _spawn_improve_once(self, state: dict[str, object]) -> None:
        # P0 has no gameplay policy to evolve. P3/P5 will introduce a bounded,
        # testable NetHack-specific improvement pipeline.
        return

    def _due_on_weekday(self, now: dt.datetime) -> bool:
        return not self.config.weekdays or now.weekday() in self.config.weekdays

    def tick(self) -> CornerResult:
        with self._tick_guard() as single:
            if not single:
                return CornerResult("noop", detail="already-running")
            if not self.config.enabled:
                with self._locked():
                    self._reconcile_stale_locked(self._local_now())
                return CornerResult("noop", detail="disabled")
            now = self._local_now()
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
                    return self._scheduled_tick(now)
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
    except (ConfigError, RetroCornerError, RuntimeError) as exc:
        print(f"docich: エラー: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

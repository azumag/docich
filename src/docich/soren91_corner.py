"""Scheduled Soren91 corner (Phase 3 定時コーナー).

The corner owns scheduling/orchestration state only. All game lifecycle changes
still go through GameSwitchCoordinator, preserving transactional switching,
rollback, and canonical state contracts — the same lifecycle as
RetroCornerManager, which this manager subclasses.

Differences from the retro corner:

* fixed game ``soren91`` (Mac remote-renderer adapter), configured by
  ``[soren91_corner]`` instead of ``[retro_corner]``;
* own state/lock/tick-guard files so a scheduled run never consumes the
  retro/paper slots;
* every tick serializes with the other corners through ``program_slot``
  (``wait_boundary=False``: Soren本編 linkage is overlay-only, so no Soren
  cycle boundary is waited on — ``require_round_boundary=false`` stays);
* the steady state is canonical idle (``active=None``): Soren本編 runs
  externally on :99, the corner overlays the Soren91 window, and ``stop``
  hands the viewport back to Soren本編;
* viewer-facing announce text is fixed (no internal vocabulary);
* a failed ``start``/``switch`` triggers one ``coordinator.recover()`` and a
  single retry before the run is marked failed (the manual runner's path).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .adapters import make_coordinator_adapter
from .config import ConfigError, GlobalConfig, load_game, load_global
from .corner_boundary import CornerWaitExpired, program_slot
from .game_switch import GameSwitchCoordinator, GameSwitchStore
from .retro_corner import (
    CornerResult,
    RetroCornerError,
    RetroCornerManager,
    _safe_detail,
)
from .trading.soren_output import enqueue_chat

GAME_NAME = "soren91"

STATE_FILE = "soren91_corner.json"
LOCK_FILE = "locks/soren91-corner.lock"
TICK_GUARD_FILE = "locks/soren91-corner-tick.lock"

# 視聴者向け開始告知。内部語 (Macレンダラー/CDP/SRT等) は含めない。
ANNOUNCE_TEXT = "ソ連ゲーム91、メリケンAIのコーナーです。"

# Chat delivery scope: sink-side duplicate suppression keys on (text, source),
# so the scheduled corner posts under its own source and never replays or
# consumes another corner's deliveries. The in-state ``announced`` flag guards
# the manager side (crash-safe: set only after the post succeeds).
DELIVERY_SOURCE = "soren91-corner"

# Monday=0 .. Sunday=6, matching datetime.weekday().
_VALID_WEEKDAYS = frozenset(range(7))


class Soren91CornerError(RetroCornerError):
    """User-facing failure in the scheduled Soren91 corner."""


@dataclass(frozen=True)
class Soren91CornerConfig:
    enabled: bool = False
    start_hour: int = 21
    duration_minutes: int = 30
    timezone: str = "Asia/Tokyo"
    weekdays: tuple[int, ...] = ()
    games: tuple[str, ...] = (GAME_NAME,)
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
        raise Soren91CornerError(f"soren91 corner設定を読み込めません: {_safe_detail(exc)}") from exc
    if not isinstance(raw, dict):
        raise Soren91CornerError("soren91 corner設定のrootはtableである必要があります")
    return raw


def load_soren91_corner_config(g: GlobalConfig) -> Soren91CornerConfig:
    raw = _raw_config(g).get("soren91_corner", {})
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise Soren91CornerError("[soren91_corner] はtableである必要があります")

    weekdays_raw = raw.get("weekdays", None)
    if weekdays_raw is None:
        weekdays: tuple[int, ...] = ()
    else:
        if (
            not isinstance(weekdays_raw, list)
            or not weekdays_raw
            or any(type(day) is not int for day in weekdays_raw)
        ):
            raise Soren91CornerError(
                "soren91_corner.weekdays は0(Mon)-6(Sun)の整数リストである必要があります"
            )
        if any(day not in _VALID_WEEKDAYS for day in weekdays_raw):
            raise Soren91CornerError(
                "soren91_corner.weekdays は0(Mon)-6(Sun)の整数リストである必要があります"
            )
        if len(set(weekdays_raw)) != len(weekdays_raw):
            raise Soren91CornerError("soren91_corner.weekdays に重複があります")
        weekdays = tuple(weekdays_raw)

    games_raw = raw.get("games", [GAME_NAME])
    if games_raw is None:
        games_raw = [GAME_NAME]
    if not isinstance(games_raw, list) or [str(name) for name in games_raw] != [GAME_NAME]:
        raise Soren91CornerError(
            f"soren91_corner.games は[{GAME_NAME!r}]固定です (定時コーナーはsoren91専用)"
        )

    cfg = Soren91CornerConfig(
        enabled=raw.get("enabled", False),
        start_hour=raw.get("start_hour", 21),
        duration_minutes=raw.get("duration_minutes", 30),
        timezone=raw.get("timezone", "Asia/Tokyo"),
        weekdays=weekdays,
        games=(GAME_NAME,),
        improve_agents=raw.get("improve_agents", ""),
        improve_matches=raw.get("improve_matches", 2),
        improve_margin_pct=raw.get("improve_margin_pct", 10.0),
    )
    if type(cfg.enabled) is not bool:
        raise Soren91CornerError("soren91_corner.enabled はtrue/falseである必要があります")
    if type(cfg.start_hour) is not int or not 0 <= cfg.start_hour <= 23:
        raise Soren91CornerError("soren91_corner.start_hour は0-23の整数である必要があります")
    if type(cfg.duration_minutes) is not int or not 1 <= cfg.duration_minutes <= 720:
        raise Soren91CornerError("soren91_corner.duration_minutes は1-720の整数である必要があります")
    if not isinstance(cfg.timezone, str) or not cfg.timezone.strip():
        raise Soren91CornerError("soren91_corner.timezone はIANA timezone文字列である必要があります")
    try:
        ZoneInfo(cfg.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise Soren91CornerError(f"soren91_corner.timezone が不正です: {cfg.timezone!r}") from exc
    if not isinstance(cfg.improve_agents, str):
        raise Soren91CornerError("soren91_corner.improve_agents は文字列である必要があります")
    if type(cfg.improve_matches) is not int or not 1 <= cfg.improve_matches <= 10:
        raise Soren91CornerError("soren91_corner.improve_matches は1-10の整数である必要があります")
    if (
        isinstance(cfg.improve_margin_pct, bool)
        or not isinstance(cfg.improve_margin_pct, (int, float))
        or not 0 <= float(cfg.improve_margin_pct) <= 100
    ):
        raise Soren91CornerError("soren91_corner.improve_margin_pct は0-100の数値である必要があります")
    return Soren91CornerConfig(
        enabled=cfg.enabled,
        start_hour=cfg.start_hour,
        duration_minutes=cfg.duration_minutes,
        timezone=cfg.timezone,
        weekdays=cfg.weekdays,
        games=(GAME_NAME,),
        improve_agents=cfg.improve_agents,
        improve_matches=cfg.improve_matches,
        improve_margin_pct=float(cfg.improve_margin_pct),
    )


class Soren91CornerManager(RetroCornerManager):
    """Scheduled Soren91 corner reusing the retro lifecycle (start/stop/restore)."""

    def __init__(
        self,
        g: GlobalConfig,
        *,
        config: Soren91CornerConfig | None = None,
        coordinator=None,
        now: Callable[[], dt.datetime] | None = None,
        sleep=None,
        active_game_reader=None,
        ensure_runtime=None,
        chat: Callable[[str], None] | None = None,
        spawn=None,
    ):
        if chat is None:
            chat = lambda text: enqueue_chat(g, text, source=DELIVERY_SOURCE)
        kwargs: dict = {
            "config": config or load_soren91_corner_config(g),
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

    # --- lifecycle customizations -------------------------------------------

    def _validate_games(self) -> None:
        try:
            game = load_game(self.g, GAME_NAME)
        except Exception as exc:
            raise RetroCornerError(
                f"soren91 corner対象を読み込めません ({GAME_NAME}): {_safe_detail(exc)}"
            ) from exc
        if game.adapter != "soren91":
            raise RetroCornerError(
                f"soren91 corner対象はsoren91ゲームに限定されます: {GAME_NAME}"
            )

    def _announce_start_locked(self, state: dict[str, object]) -> None:
        """視聴者向け開始告知 (固定文面)。lock 保持中に呼ぶ。

        投稿失敗はコーナー自体を失敗させない。結果は state に記録する。
        """
        if state.get("announced"):
            return
        game = state.get("game")
        if not isinstance(game, str) or not game:
            return
        try:
            self._chat(ANNOUNCE_TEXT)
        except Exception as exc:
            state["announce_error"] = _safe_detail(exc)
            return
        state["announced"] = True
        state.pop("announce_error", None)

    def _transition_to(self, current: str | None, target: str) -> None:
        try:
            super()._transition_to(current, target)
            return
        except RetroCornerError:
            recover = getattr(self.coordinator, "recover", None)
            if recover is None:
                raise
            # A previous run may have left canonical phase=failed (e.g. a rolled
            # back test). Recover once, then retry once — the manual runner's
            # path. Anything still failing marks the run failed as usual.
            try:
                recovered = recover()
            except Exception:
                raise
            if getattr(recovered, "status", None) != "succeeded":
                raise
            super()._transition_to(current, target)

    def _spawn_improve_once(self, state: dict[str, object]) -> None:
        """終了時改善は未連携 (残作業)。finish を壊さず理由だけ記録する。"""
        agents = (self.config.improve_agents or "").strip()
        if not agents:
            return
        state["improve_job"] = {"spawned": False, "reason": "soren91-improve-not-supported"}

    # --- scheduling ----------------------------------------------------------

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
    parser = argparse.ArgumentParser(prog="docich soren91-corner")
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
        manager = Soren91CornerManager(g)
        if args.command == "status":
            state = manager.status()
            if args.json:
                print(json.dumps(state, ensure_ascii=False, separators=(",", ":")))
            else:
                print(
                    "soren91-corner: "
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

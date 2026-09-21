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
import os
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .adapters import make_coordinator_adapter
from .config import ConfigError, GlobalConfig, load_game, load_global
from .corner_boundary import CornerWaitExpired, program_slot
from .game_switch import (
    ERROR_RECOVERY_REQUIRED,
    GameSwitchCoordinator,
    GameSwitchStore,
    RuntimeSpec,
)
from .retro_corner import (
    CornerResult,
    RetroCornerError,
    RetroCornerTransitionError,
    RetroCornerManager,
    _safe_detail,
)
from .trading.soren_output import enqueue_audio_text, enqueue_chat

GAME_NAME = "soren91"

STATE_FILE = "soren91_corner.json"
LOCK_FILE = "locks/soren91-corner.lock"
TICK_GUARD_FILE = "locks/soren91-corner-tick.lock"

# 視聴者向け開始告知。内部語 (Macレンダラー/CDP/SRT等) は含めない。
ANNOUNCE_TEXT = "ソ連ゲーム91、メリケンAIのコーナーです。今日も91人対戦で、資本主義の力を見せてやりましょう。しばらくお付き合いください。"
END_ANNOUNCE_TEXT = "ソ連ゲーム91コーナーはここまでです。最後までお付き合いいただき、ありがとうございました。また次のコーナーでお会いしましょう。"

# チャット限定のライセンス通知。音声の開始告知には付けない。
LICENSE_NOTICE = "【91人対戦】ソ連ゲーム91 - たアケイク https://unityroom.com/games/sorengame91"
CHAT_ANNOUNCE_TEXT = f"{ANNOUNCE_TEXT} {LICENSE_NOTICE}"

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
        voice: Callable[[str], None] | None = None,
        spawn=None,
        agent_alive_probe: Callable[[], bool | None] | None = None,
        agent_liveness_poll_s: float = 30.0,
        agent_liveness_strikes: int = 2,
        agent_liveness_timeout_s: float = 5.0,
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
        try:
            raw = (load_game(g, GAME_NAME).raw.get("soren91") or {})
            self.voicevox_speaker = os.environ.get("SOREN91_VOICEVOX_SPEAKER") or str(
                raw.get("voicevox_speaker", 14)
            )
        except Exception:
            self.voicevox_speaker = os.environ.get("SOREN91_VOICEVOX_SPEAKER", "14")
        self._voice = voice or (
            lambda text: enqueue_audio_text(
                self.g, text, context="soren91:announce", speaker=self.voicevox_speaker
            )
        )
        if not (isinstance(agent_liveness_poll_s, (int, float)) and agent_liveness_poll_s > 0):
            raise Soren91CornerError("agent_liveness_poll_s は正の秒数である必要があります")
        if type(agent_liveness_strikes) is not int or agent_liveness_strikes < 1:
            raise Soren91CornerError("agent_liveness_strikes は1以上の整数である必要があります")
        if not (
            isinstance(agent_liveness_timeout_s, (int, float)) and agent_liveness_timeout_s > 0
        ):
            raise Soren91CornerError("agent_liveness_timeout_s は正の秒数である必要があります")
        self._agent_liveness_poll_s = float(agent_liveness_poll_s)
        self._agent_liveness_strikes = agent_liveness_strikes
        self._agent_liveness_timeout_s = float(agent_liveness_timeout_s)
        self._agent_alive_probe = agent_alive_probe or self._active_agent_alive

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
            self._chat(CHAT_ANNOUNCE_TEXT)
        except Exception as exc:
            state["announce_error"] = _safe_detail(exc)
            return
        # Speak the announcement in the Meriken voice (best-effort).
        try:
            self._voice(ANNOUNCE_TEXT)
        except Exception as exc:
            state["voice_error"] = _safe_detail(exc)
        state["announced"] = True
        state.pop("announce_error", None)

    def _announce_end_locked(self, state: dict[str, object]) -> None:
        """視聴者向け終了告知 (固定文面)。lock 保持中に呼ぶ。

        投稿/読み上げ失敗はコーナー自体を失敗させない。結果は state に記録する。
        """
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

    def _finish_locked(self, state: dict[str, object], completed_at: dt.datetime) -> CornerResult:
        result = super()._finish_locked(state, completed_at)
        # 終了のひとことを Meriken の声で (best-effort)。state は書き直して残す。
        self._announce_end_locked(state)
        try:
            self._write_state(state)
        except Exception:
            pass
        return result

    def _transition_to(
        self,
        current: str | None,
        target: str,
        *,
        request_id: str | None = None,
    ):
        try:
            return super()._transition_to(current, target, request_id=request_id)
        except RetroCornerTransitionError as exc:
            # The shared manager owns the exact canonical-failed retry. Do
            # not perform a second recovery attempt for recovery_required;
            # in particular, never turn a live boundary wait into a reset.
            if exc.error_code == ERROR_RECOVERY_REQUIRED:
                raise
            recover = getattr(self.coordinator, "recover", None)
            if recover is None:
                raise
            try:
                recovered = recover()
            except Exception:
                raise
            if getattr(recovered, "status", None) != "succeeded":
                raise
            return super()._transition_to(current, target, request_id=request_id)
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
            return super()._transition_to(current, target, request_id=request_id)

    def _spawn_improve_once(self, state: dict[str, object]) -> None:
        """終了時改善は未連携 (残作業)。finish を壊さず理由だけ記録する。"""
        agents = (self.config.improve_agents or "").strip()
        if not agents:
            return
        state["improve_job"] = {"spawned": False, "reason": "soren91-improve-not-supported"}

    # --- run-window supervision ---------------------------------------------

    def _probe_agent_alive(self) -> bool | None:
        """Best-effort agent liveness: True/False, or None when undeterminable."""
        try:
            result = self._agent_alive_probe()
        except Exception:  # noqa: BLE001 - an indeterminate probe must not end the run
            return None
        if result is True or result is False:
            return result
        return None

    def _active_agent_alive(self) -> bool | None:
        """Liveness of the active runtime's agent (bot) through its adapter.

        Returns None when canonical is not this corner's game or the check
        cannot be made, so an indeterminate probe never ends a healthy corner.
        """
        try:
            state, _missing = self.store.canonical.load()
            if state.get("phase") != "ready":
                return None
            active = state.get("active")
            if not isinstance(active, dict) or active.get("game") != GAME_NAME:
                return None
            spec = RuntimeSpec.from_runtime(self.g.state_dir, active)
            adapter = make_coordinator_adapter(self.g, spec)
        except Exception:  # noqa: BLE001 - liveness is best-effort
            return None
        check = getattr(adapter, "agent_alive", None)
        if check is None:
            return None
        try:
            deadline = time.monotonic() + self._agent_liveness_timeout_s
            return bool(check(deadline, None))
        except Exception:  # noqa: BLE001 - liveness is best-effort
            return None

    def _wait_and_finish(self, state: dict[str, object]) -> CornerResult:
        """Run the slot while watching the in-window bot (soren91-specific).

        The sustained corner keeps its runtime for the whole slot, so a bot that
        exits mid-run would otherwise leave dead air until ``ends_at``. Poll the
        generation-owned agent window and end the corner (restoring the previous
        game) once it is confirmed gone. A single miss is tolerated and an
        indeterminate probe never ends a healthy corner.
        """
        ends_at = self._parse_ends_at(state)
        if ends_at is None:
            raise RetroCornerError("soren91 corner ends_atが不正です")
        strikes = 0
        while True:
            remaining = max(0.0, (ends_at - self._local_now()).total_seconds())
            if remaining <= 0.0:
                return self._finish_active_or_state()
            before = self._local_now()
            self._sleep(min(self._agent_liveness_poll_s, remaining))
            after = self._local_now()
            if after <= before:
                # The sleeper did not move the clock (no-op test double). With
                # no passage of time there is nothing to supervise over, so
                # honor the base single-sleep contract and finish now.
                return self._finish_active_or_state()
            latest = self._read_state()
            if latest.get("status") != "active":
                return self._state_result(latest)
            alive = self._probe_agent_alive()
            if alive is False:
                strikes += 1
                if strikes >= self._agent_liveness_strikes:
                    with self._locked():
                        latest = self._read_state()
                        if latest.get("status") != "active":
                            return self._state_result(latest)
                        latest["early_end_reason"] = "soren91-agent-not-alive"
                        print(
                            "[soren91-corner] agent window not alive; ending corner early",
                            file=sys.stderr,
                        )
                        return self._finish_locked(latest, self._local_now())
            elif alive is True:
                strikes = 0
            # None (undeterminable): keep the slot rather than end on doubt.

    def _finish_active_or_state(self) -> CornerResult:
        """Finish the active corner, or return the current state result."""
        with self._locked():
            latest = self._read_state()
            if latest.get("status") != "active":
                return self._state_result(latest)
            return self._finish_locked(latest, self._local_now())

    # --- scheduling ----------------------------------------------------------

    def _due_on_weekday(self, now: dt.datetime) -> bool:
        return not self.config.weekdays or now.weekday() in self.config.weekdays

    def tick(self) -> CornerResult:
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

"""Daily Meriken AI retro-game program slot.

The corner owns scheduling/orchestration state only. All game lifecycle changes
still go through GameSwitchCoordinator, preserving transactional switching,
round-boundary waits, rollback, and canonical state contracts.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import sys
import time
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .adapters import make_coordinator_adapter
from .config import ConfigError, GlobalConfig, load_game, load_global
from .game_switch import GameSwitchCoordinator, GameSwitchStore, atomic_write_json
from .naming import NameValidationError, validate_game_name
from .trading.soren_output import enqueue_chat, enqueue_speech

STATE_SCHEMA_VERSION = 1
STATE_FILE = "retro_corner.json"
LOCK_FILE = "locks/retro-corner.lock"
TICK_GUARD_FILE = "locks/retro-corner-tick.lock"
TERMINAL_STATUSES = {"completed", "interrupted", "failed"}


class RetroCornerError(RuntimeError):
    """User-facing failure in the daily retro corner."""


@dataclass(frozen=True)
class RetroCornerConfig:
    enabled: bool = False
    require_program_boundary: bool = False
    start_hour: int = 19
    duration_minutes: int = 30
    timezone: str = "Asia/Tokyo"
    games: list[str] = field(default_factory=lambda: ["gnurobots"])
    improve_agents: str = ""
    improve_matches: int = 2
    improve_margin_pct: float = 10.0


@dataclass(frozen=True)
class CornerResult:
    status: str
    game: str | None = None
    previous_game: str | None = None
    detail: str | None = None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _safe_detail(value: BaseException | str) -> str:
    return str(value).replace("\n", " ")[:240]


def _raw_config(g: GlobalConfig) -> dict:
    if not g.config_path.is_file():
        return {}
    try:
        with g.config_path.open("rb") as stream:
            raw = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RetroCornerError(f"retro corner設定を読み込めません: {_safe_detail(exc)}") from exc
    if not isinstance(raw, dict):
        raise RetroCornerError("retro corner設定のrootはtableである必要があります")
    return raw


def load_retro_corner_config(g: GlobalConfig) -> RetroCornerConfig:
    raw = _raw_config(g).get("retro_corner", {})
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise RetroCornerError("[retro_corner] はtableである必要があります")

    cfg = RetroCornerConfig(
        enabled=raw.get("enabled", False),
        require_program_boundary=raw.get("require_program_boundary", False),
        start_hour=raw.get("start_hour", 19),
        duration_minutes=raw.get("duration_minutes", 30),
        timezone=raw.get("timezone", "Asia/Tokyo"),
        games=raw.get("games", ["gnurobots"]),
        improve_agents=raw.get("improve_agents", ""),
        improve_matches=raw.get("improve_matches", 2),
        improve_margin_pct=raw.get("improve_margin_pct", 10.0),
    )
    if type(cfg.require_program_boundary) is not bool:
        raise RetroCornerError("require_program_boundary must be boolean")
    if type(cfg.enabled) is not bool:
        raise RetroCornerError("retro_corner.enabled はtrue/falseである必要があります")
    if type(cfg.start_hour) is not int or not 0 <= cfg.start_hour <= 23:
        raise RetroCornerError("retro_corner.start_hour は0-23の整数である必要があります")
    if type(cfg.duration_minutes) is not int or not 1 <= cfg.duration_minutes <= 720:
        raise RetroCornerError("retro_corner.duration_minutes は1-720の整数である必要があります")
    if not isinstance(cfg.timezone, str) or not cfg.timezone.strip():
        raise RetroCornerError("retro_corner.timezone はIANA timezone文字列である必要があります")
    try:
        ZoneInfo(cfg.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise RetroCornerError(f"retro_corner.timezone が不正です: {cfg.timezone!r}") from exc
    if (
        not isinstance(cfg.games, list)
        or not cfg.games
        or not all(isinstance(name, str) for name in cfg.games)
    ):
        raise RetroCornerError("retro_corner.games は空でないゲーム名リストである必要があります")
    if not isinstance(cfg.improve_agents, str):
        raise RetroCornerError("retro_corner.improve_agents は文字列である必要があります")
    if type(cfg.improve_matches) is not int or not 1 <= cfg.improve_matches <= 10:
        raise RetroCornerError("retro_corner.improve_matches は1-10の整数である必要があります")
    if (
        isinstance(cfg.improve_margin_pct, bool)
        or not isinstance(cfg.improve_margin_pct, (int, float))
        or not 0 <= float(cfg.improve_margin_pct) <= 100
    ):
        raise RetroCornerError("retro_corner.improve_margin_pct は0-100の数値である必要があります")
    try:
        games = [validate_game_name(name) for name in cfg.games]
    except NameValidationError as exc:
        raise RetroCornerError(f"retro_corner.games に不正なゲーム名があります: {exc}") from exc
    if len(games) != len(set(games)):
        raise RetroCornerError("retro_corner.games に重複があります")
    return RetroCornerConfig(
        enabled=cfg.enabled,
        require_program_boundary=cfg.require_program_boundary,
        start_hour=cfg.start_hour,
        duration_minutes=cfg.duration_minutes,
        timezone=cfg.timezone,
        games=games,
        improve_agents=cfg.improve_agents,
        improve_matches=cfg.improve_matches,
        improve_margin_pct=float(cfg.improve_margin_pct),
    )


def select_game(games: list[str], local_date: dt.date) -> str:
    if not games:
        raise RetroCornerError("retro corner対象ゲームがありません")
    return games[local_date.toordinal() % len(games)]


def corner_intro(g: GlobalConfig, game_name: str) -> str:
    """開始時チャット投稿用のゲーム説明文。toml [corner] intro、無ければ定型文。"""
    try:
        game = load_game(g, game_name)
    except Exception:
        return f"{game_name}をお送りします。"
    raw = game.raw.get("corner", {}) if isinstance(game.raw, dict) else {}
    if isinstance(raw, dict) and isinstance(raw.get("intro"), str) and raw["intro"].strip():
        return raw["intro"].strip()
    return f"{game.title}をお送りします。"


def describe_strategy_change(state_dir, game_name: str) -> str:
    """今回戦略と前回戦略の差分サマリ。履歴が無ければ初回扱いの一文を返す。"""
    from .resolver import strategy_path

    current_path = strategy_path(state_dir, game_name)
    try:
        current = json.loads(Path(current_path).read_text(encoding="utf-8"))
        if not isinstance(current, dict):
            current = {}
    except (OSError, ValueError):
        current = {}
    history_dir = Path(state_dir) / "resolver" / "history"
    try:
        snapshots = sorted(history_dir.glob("*.json"))
    except OSError:
        snapshots = []
    previous = {}
    if snapshots:
        try:
            data = json.loads(snapshots[-1].read_text(encoding="utf-8"))
            if isinstance(data, dict):
                previous = data
        except (OSError, ValueError):
            previous = {}
    if not previous:
        return "改善済みの最新戦略でお送りします。"
    changes = []
    for key in sorted(set(previous) | set(current)):
        old, new = previous.get(key), current.get(key)
        if old == new or not isinstance(old, (int, float)) or not isinstance(new, (int, float)):
            continue
        changes.append(f"{key} {old}→{new}")
    if not changes:
        return "前回と同じ戦略でお送りします。"
    shown = "、".join(changes[:3])
    extra = f"ほか{len(changes) - 3}件" if len(changes) > 3 else ""
    return f"前回から戦略を調整しました（{shown}{extra}）。"


class RetroCornerManager:
    def __init__(
        self,
        g: GlobalConfig,
        *,
        config: RetroCornerConfig | None = None,
        coordinator=None,
        now: Callable[[], dt.datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        active_game_reader: Callable[[], str | None] | None = None,
        ensure_runtime: Callable[[], None] | None = None,
        chat: Callable[[str], None] | None = None,
        speech: Callable[[str, str], None] | None = None,
        spawn=None,
    ):
        self.g = g
        self.config = config or load_retro_corner_config(g)
        self.tz = ZoneInfo(self.config.timezone)
        self.store = GameSwitchStore(g.state_dir)
        self.coordinator = coordinator or GameSwitchCoordinator(
            self.store, lambda spec: make_coordinator_adapter(g, spec)
        )
        self._now = now or (lambda: dt.datetime.now(self.tz))
        self._sleep = sleep
        self._active_game_reader = active_game_reader or self._canonical_active_game
        self._ensure_runtime = ensure_runtime or self._default_ensure_runtime
        self._chat = chat or (lambda text: enqueue_chat(self.g, text, source="retro-corner"))
        self._speech = speech or (
            lambda text, event_id: enqueue_speech(self.g, text, event_id=event_id)
        )
        self._spawn = spawn or self._default_spawn_improve_proc
        self.state_path = Path(g.state_dir) / STATE_FILE
        self.lock_path = Path(g.state_dir) / LOCK_FILE
        self.tick_guard_path = Path(g.state_dir) / TICK_GUARD_FILE

    def _default_ensure_runtime(self) -> None:
        # Import lazily so ``python -m docich retro-corner`` can route here
        # before the large legacy CLI module is imported. cmd_up is the single
        # existing contract for creating the shared tmux session and validating
        # an external display; with docich.soren-live.toml it does NOT own Xvfb,
        # audio, or FFmpeg.
        from .cli import cmd_up

        rc = cmd_up(self.g)
        if rc != 0:
            raise RetroCornerError(f"docich up が失敗しました (rc={rc})")

    def _local_now(self) -> dt.datetime:
        value = self._now()
        if value.tzinfo is None:
            return value.replace(tzinfo=self.tz)
        return value.astimezone(self.tz)

    def _canonical_active_game(self) -> str | None:
        state, _missing = self.store.canonical.load()
        phase = state.get("phase")
        if phase == "idle":
            return None
        if phase != "ready":
            raise RetroCornerError(f"ゲーム切替が安定phaseではありません: {phase!r}")
        active = state.get("active")
        if not isinstance(active, dict) or not isinstance(active.get("game"), str):
            raise RetroCornerError("canonical active gameを解決できません")
        return active["game"]

    @contextmanager
    def _tick_guard(self) -> Iterator[bool]:
        """同一コーナーの重複 tick を program 待ち行列に積まず弾く単一飛行 guard。"""
        self.tick_guard_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.tick_guard_path.parent, 0o700)
        handle = self.tick_guard_path.open("a+", encoding="utf-8")
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
            else:
                try:
                    yield True
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def _announce_start_locked(self, state: dict[str, object]) -> None:
        """開始時チャット投稿＋読み上げ (ゲーム説明＋今回戦略の前回比較)。

        lock 保持中に呼ぶ。投稿/読み上げの失敗はコーナー自体を失敗させず、
        それぞれ独立に state へ記録して次回再試行できるようにする。
        """
        game = state.get("game")
        if not isinstance(game, str) or not game:
            return
        text = (
            "レトロゲームコーナーです。本日は"
            + corner_intro(self.g, game)
            + describe_strategy_change(self.g.state_dir, game)
        )
        event_id = f"retro-corner:{state.get('date', 'nodate')}:start"
        if not state.get("announced"):
            try:
                self._chat(text)
                state["announced"] = True
                state.pop("announce_error", None)
            except Exception as exc:
                state["announce_error"] = _safe_detail(exc)
        if not state.get("announced_speech"):
            try:
                self._speech(text, event_id)
                state["announced_speech"] = True
                state.pop("announce_speech_error", None)
            except Exception as exc:
                state["announce_speech_error"] = _safe_detail(exc)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.lock_path.parent, 0o700)
        handle = self.lock_path.open("a+", encoding="utf-8")
        os.chmod(self.lock_path, 0o600)
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RetroCornerError("retro corner処理が既に実行中です") from exc
            yield
        finally:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    @staticmethod
    def _default_state() -> dict[str, object]:
        return {
            "schema_version": STATE_SCHEMA_VERSION,
            "status": "idle",
            "date": None,
            "game": None,
            "previous_game": None,
            "started_at": None,
            "ends_at": None,
            "completed_at": None,
            "last_error": None,
        }

    def _read_state(self) -> dict[str, object]:
        if not self.state_path.is_file():
            return self._default_state()
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RetroCornerError(f"retro corner stateを読み込めません: {_safe_detail(exc)}") from exc
        if not isinstance(state, dict) or state.get("schema_version") != STATE_SCHEMA_VERSION:
            raise RetroCornerError("retro corner state schemaが不正です")
        if state.get("status") not in {"idle", "waiting", "starting", "active", "completed", "interrupted", "failed"}:
            raise RetroCornerError("retro corner state statusが不正です")
        return state

    def _write_state(self, state: dict[str, object]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_path.parent, 0o700)
        atomic_write_json(self.state_path, state)

    def _validate_games(self) -> None:
        for name in self.config.games:
            try:
                game = load_game(self.g, name)
            except Exception as exc:
                raise RetroCornerError(
                    f"retro corner対象ゲームを読み込めません ({name}): {_safe_detail(exc)}"
                ) from exc
            if game.adapter != "cli":
                raise RetroCornerError(f"retro corner対象はCLIゲームに限定されます: {name}")
            corner_raw = game.raw.get("corner", {}) if isinstance(game.raw, dict) else {}
            self_play = isinstance(corner_raw, dict) and corner_raw.get("self_play") is True
            if game.agent.enabled is not True and not self_play:
                raise RetroCornerError(f"retro corner対象はagent.enabled=trueが必要です: {name}")

    @staticmethod
    def _require_success(result, action: str) -> None:
        if getattr(result, "status", None) != "succeeded":
            detail = (
                getattr(result, "detail", None)
                or getattr(result, "error_code", None)
                or "unknown"
            )
            raise RetroCornerError(f"{action} に失敗しました: {_safe_detail(detail)}")

    def _transition_to(self, current: str | None, target: str) -> None:
        if current == target:
            return
        if current is None:
            self._require_success(self.coordinator.start(target), f"{target} start")
        else:
            self._require_success(self.coordinator.switch(target), f"{current}->{target} switch")

    @staticmethod
    def _state_result(state: dict[str, object]) -> CornerResult:
        game = state.get("game")
        previous = state.get("previous_game")
        detail = state.get("last_error")
        return CornerResult(
            str(state.get("status") or "idle"),
            game=game if isinstance(game, str) else None,
            previous_game=previous if isinstance(previous, str) else None,
            detail=detail if isinstance(detail, str) else None,
        )

    def _default_spawn_improve_proc(self, argv: list[str], log_path: Path) -> None:
        import subprocess

        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        # 終了時改善の LLM 実実行は live 設定の improve_agents 非空が合意の記録。
        # 子プロセスへ明示許可を引き継ぐ (tick の service 環境には無いため)。
        env = dict(os.environ)
        env["DOCICH_ALLOW_REAL_AI"] = "1"
        with open(log_path, "ab") as log_fh:
            subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                cwd=str(_repo_root()),
                env=env,
            )

    def _spawn_improve_once(self, state: dict[str, object]) -> None:
        """終了時改善ジョブを切り離して起動する。失敗しても finish を壊さない。"""
        agents = (self.config.improve_agents or "").strip()
        date_str = state.get("date")
        if not agents or not isinstance(date_str, str) or not date_str:
            return
        try:
            dt.date.fromisoformat(date_str)
        except ValueError:
            state["improve_job"] = {"spawned": False, "error": f"日付が不正です: {date_str}"}
            return
        log_path = Path(self.g.state_dir) / "logs" / f"retro-corner-improve-{date_str}.log"
        argv = [
            sys.executable, "-m", "docich", "--config", str(self.g.config_path),
            "retro-corner", "improve-once", "--date", date_str,
        ]
        try:
            self._spawn(argv, log_path)
            state["improve_job"] = {"spawned": True, "date": date_str, "log": str(log_path)}
        except Exception as exc:
            state["improve_job"] = {"spawned": False, "error": _safe_detail(exc)}

    def improve_once(
        self,
        date_str: str,
        *,
        agents: str | None = None,
        matches: int | None = None,
        margin_pct: float | None = None,
        dry_run: bool = False,
    ) -> dict:
        from .corner_improve import run_corner_improve

        try:
            game = select_game(self.config.games, dt.date.fromisoformat(date_str))
        except ValueError as exc:
            raise RetroCornerError(f"日付が不正です: {date_str}") from exc
        agents = self.config.improve_agents if agents is None else agents
        matches = self.config.improve_matches if matches is None else matches
        margin_pct = float(self.config.improve_margin_pct) if margin_pct is None else margin_pct
        if type(matches) is not int or not 1 <= matches <= 10:
            raise RetroCornerError("improve matches は1-10の整数である必要があります")
        if (
            isinstance(margin_pct, bool)
            or not isinstance(margin_pct, (int, float))
            or not 0 <= float(margin_pct) <= 100
        ):
            raise RetroCornerError("improve margin-pct は0-100の数値である必要があります")
        return run_corner_improve(
            self.g,
            game=game,
            date_str=date_str,
            agents=agents,
            matches=matches,
            margin_pct=float(margin_pct),
            dry_run=dry_run,
        )

    def _finish_locked(self, state: dict[str, object], completed_at: dt.datetime) -> CornerResult:
        game = state.get("game")
        previous = state.get("previous_game")
        if not isinstance(game, str):
            raise RetroCornerError("active retro corner stateにgameがありません")
        current = self._active_game_reader()
        if current != game:
            state.update(
                status="interrupted",
                completed_at=completed_at.isoformat(),
                last_error=None,
            )
            self._write_state(state)
            return self._state_result(state)

        try:
            if previous is None:
                self._require_success(self.coordinator.stop(), "retro corner stop")
            elif isinstance(previous, str) and previous != game:
                self._require_success(
                    self.coordinator.switch(previous), f"{game}->{previous} restore"
                )
            state.update(
                status="completed",
                completed_at=completed_at.isoformat(),
                last_error=None,
            )
            self._spawn_improve_once(state)
            self._write_state(state)
            return self._state_result(state)
        except Exception as exc:
            state.update(
                status="failed",
                completed_at=completed_at.isoformat(),
                last_error=_safe_detail(exc),
            )
            self._write_state(state)
            if isinstance(exc, RetroCornerError):
                raise
            raise RetroCornerError(_safe_detail(exc)) from exc

    def _parse_ends_at(self, state: dict[str, object]) -> dt.datetime | None:
        raw = state.get("ends_at")
        if not isinstance(raw, str) or not raw:
            return None
        try:
            value = dt.datetime.fromisoformat(raw)
        except ValueError:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=self.tz)
        return value.astimezone(self.tz)

    def _reconcile_stale_locked(self, now: dt.datetime) -> None:
        state = self._read_state()
        if state.get("status") != "active":
            return
        ends_at = self._parse_ends_at(state)
        if ends_at is None or now >= ends_at:
            self._ensure_runtime()
            self._finish_locked(state, now)

    def _begin_locked(
        self, now: dt.datetime, *, scheduled: bool
    ) -> tuple[dict[str, object] | None, CornerResult | None]:
        self._reconcile_stale_locked(now)
        existing = self._read_state()
        if existing.get("status") == "active":
            if scheduled:
                return None, CornerResult("noop", detail="already-active")
            raise RetroCornerError("retro cornerは既にactiveです")
        if (
            scheduled
            and existing.get("date") == now.date().isoformat()
            and existing.get("status") in TERMINAL_STATUSES
        ):
            return None, CornerResult("noop", detail="already-ran-today")

        self._validate_games()
        self._ensure_runtime()
        previous = self._active_game_reader()
        target = select_game(self.config.games, now.date())
        ends_at = now + dt.timedelta(minutes=self.config.duration_minutes)
        state = {
            "schema_version": STATE_SCHEMA_VERSION,
            "status": "starting",
            "date": now.date().isoformat(),
            "game": target,
            "previous_game": previous,
            "started_at": now.isoformat(),
            "ends_at": ends_at.isoformat(),
            "completed_at": None,
            "last_error": None,
        }
        self._write_state(state)
        try:
            self._transition_to(previous, target)
            started = self._local_now()
            state.update(status="active", started_at=started.isoformat(),
                         ends_at=(started + dt.timedelta(minutes=self.config.duration_minutes)).isoformat())
            self._announce_start_locked(state)
            self._write_state(state)
        except Exception as exc:
            state.update(
                status="failed",
                completed_at=self._local_now().isoformat(),
                last_error=_safe_detail(exc),
            )
            self._write_state(state)
            if isinstance(exc, RetroCornerError):
                raise
            raise RetroCornerError(_safe_detail(exc)) from exc
        return state, None

    def _wait_and_finish(self, state: dict[str, object]) -> CornerResult:
        ends_at = self._parse_ends_at(state)
        if ends_at is None:
            raise RetroCornerError("retro corner ends_atが不正です")
        remaining = max(0.0, (ends_at - self._local_now()).total_seconds())
        self._sleep(remaining)
        with self._locked():
            latest = self._read_state()
            if latest.get("status") != "active":
                return self._state_result(latest)
            return self._finish_locked(latest, self._local_now())

    def start(self) -> CornerResult:
        with self._locked():
            state, result = self._begin_locked(self._local_now(), scheduled=False)
        if result is not None:
            return result
        assert state is not None
        return self._wait_and_finish(state)

    def stop(self) -> CornerResult:
        with self._locked():
            state = self._read_state()
            if state.get("status") != "active":
                return CornerResult("noop", detail="not-active")
            self._ensure_runtime()
            return self._finish_locked(state, self._local_now())

    def tick(self) -> CornerResult:
        with self._tick_guard() as single:
            if not single:
                return CornerResult("noop", detail="already-running")
            if self.config.require_program_boundary:
                return self._boundary_tick()
            return self._legacy_tick()

    def _boundary_tick(self) -> CornerResult:
        from .corner_boundary import CornerWaitExpired, program_slot
        now = self._local_now()
        if not self.config.enabled:
            return CornerResult("noop", detail="disabled")
        end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=0)
        deadline = end_of_day.timestamp()
        # Phase 1: record/refresh the boundary request without owning the
        # program slot, so a corner waiting on its boundary never blocks
        # another corner that is already ready.
        requested_at = None
        wait_boundary = False
        with self._locked():
            state = self._read_state()
            status = state.get("status")
            if status not in ("active", "starting"):
                if status == "waiting" and state.get("date") != now.date().isoformat():
                    # A previous day's request that never reached a boundary
                    # (e.g. the tick was killed mid-wait). Never fire it late.
                    self._write_state(self._default_state())
                    return CornerResult("expired", detail="stale-request")
                if status != "waiting":
                    if now.hour < self.config.start_hour:
                        return CornerResult("noop", detail="outside-window")
                    if state.get("date") == now.date().isoformat() and status in TERMINAL_STATUSES:
                        return CornerResult("noop", detail="already-ran-today")
                    state = self._default_state()
                    state.update(status="waiting", date=now.date().isoformat(), requested_at=now.timestamp())
                    self._write_state(state)
                requested_at = state.get("requested_at")
                if isinstance(requested_at, bool) or not isinstance(requested_at, (int, float)):
                    requested_at = now.timestamp()
                    state["requested_at"] = requested_at
                    self._write_state(state)
                wait_boundary = True
        try:
            with program_slot(
                self.g,
                self.state_path,
                requested_at=requested_at if requested_at is not None else now.timestamp(),
                wait_deadline_ts=deadline,
                wait_boundary=wait_boundary,
                sleep=self._sleep,
                now=lambda: self._local_now().timestamp(),
            ) as root:
                return self._boundary_tick_locked(now, root)
        except CornerWaitExpired:
            return CornerResult("expired", detail="program-wait-expired")

    def _boundary_tick_locked(self, now, root) -> CornerResult:
        with self._locked():
            state = self._read_state()
            if state.get("status") == "starting":
                current = self._active_game_reader()
                if current not in (state.get("previous_game"), state.get("game")):
                    state.update(status="interrupted", completed_at=self._local_now().isoformat())
                    self._write_state(state)
                    return self._state_result(state)
                self._transition_to(current, state["game"])
                started = self._local_now()
                state.update(status="active", started_at=started.isoformat(),
                             ends_at=(started + dt.timedelta(minutes=self.config.duration_minutes)).isoformat())
                self._announce_start_locked(state)
                self._write_state(state)
            if state.get("status") == "active":
                active = state
            else:
                active = None
        if active is not None:
            return self._wait_and_finish(active)
        with self._locked():
            active, result = self._begin_locked(self._local_now(), scheduled=True)
            if active is not None:
                active["date"] = state["date"]
                self._write_state(active)
        return result if result is not None else self._wait_and_finish(active)

    def _legacy_tick(self) -> CornerResult:
        now = self._local_now()
        if not self.config.enabled or now.hour != self.config.start_hour:
            with self._locked():
                self._reconcile_stale_locked(now)
            return CornerResult("noop", detail="outside-window")
        with self._locked():
            state, result = self._begin_locked(now, scheduled=True)
        if result is not None:
            return result
        assert state is not None
        return self._wait_and_finish(state)

    def status(self) -> dict[str, object]:
        with self._locked():
            return self._read_state()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docich retro-corner")
    parser.add_argument("--config", metavar="PATH")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("tick")
    sub.add_parser("start")
    sub.add_parser("stop")
    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")
    once = sub.add_parser("improve-once")
    once.add_argument("--date", required=True, help="対象コーナー日 (YYYY-MM-DD)")
    once.add_argument("--agents", default=None, help="LLM委任先 (既定は設定値)")
    once.add_argument("--matches", type=int, default=None)
    once.add_argument("--margin-pct", type=float, default=None)
    once.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config_path = Path(args.config) if args.config else None
    try:
        g = load_global(_repo_root(), config_path)
        manager = RetroCornerManager(g)
        if args.command == "status":
            state = manager.status()
            if args.json:
                print(json.dumps(state, ensure_ascii=False, separators=(",", ":")))
            else:
                print(
                    "retro-corner: "
                    f"status={state.get('status')} game={state.get('game')} "
                    f"previous={state.get('previous_game')} ends_at={state.get('ends_at')}"
                )
            return 0
        if args.command == "improve-once":
            from .corner_improve import CornerImproveError
            try:
                summary = manager.improve_once(
                    args.date, agents=args.agents, matches=args.matches,
                    margin_pct=args.margin_pct, dry_run=args.dry_run,
                )
            except CornerImproveError as exc:
                print(f"docich: エラー: {exc}", file=sys.stderr)
                return 2
            print(json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
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

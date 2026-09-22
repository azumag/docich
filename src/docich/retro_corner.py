"""Meriken AI retro-game program slot (daily, hourly lottery, or rolling rotation).

The corner owns scheduling/orchestration state only. All game lifecycle changes
still go through GameSwitchCoordinator, preserving transactional switching,
round-boundary waits, rollback, and canonical state contracts.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import inspect
import json
import os
import random
import shutil
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
from .game_switch import (
    ERROR_RECOVERY_REQUIRED,
    GameSwitchCoordinator,
    GameSwitchStore,
    atomic_write_json,
    new_request_id,
)
from .naming import NameValidationError, validate_game_name
from .procs import user_bus_env
from .trading.soren_output import enqueue_chat

STATE_SCHEMA_VERSION = 1
STATE_FILE = "retro_corner.json"
LOCK_FILE = "locks/retro-corner.lock"
TICK_GUARD_FILE = "locks/retro-corner-tick.lock"
TERMINAL_STATUSES = {"completed", "interrupted", "failed"}
# 抽選モード: 各時の抽選は lottery_minute から この分数の間に届いた最初の tick だけが行う
# (毎分 tick の取りこぼしに耐えつつ、遅すぎる抽選で次の正時に食い込ませない)。
LOTTERY_DRAW_WINDOW_MINUTES = 10
# 抽選コーナーは次の正時の前に必ず終わらせる (毎正時に始まる固定枠のコーナーを塞がない)。
LOTTERY_LATEST_END_MINUTE = 55
# rolling rotation も、設定された固定時刻コーナーへ枠を返すために同じ
# 5分の引き継ぎ余白を確保する。
ROTATION_FIXED_SLOT_BUFFER_MINUTES = 5
# rolling rotation が先読みする、program lock を使う固定時刻コーナー。
FIXED_CORNER_SECTIONS = ("paper_corner", "soren91_corner", "nethack_corner")
# starting のまま残った (tick が落ちた) 状態を割り込み扱いにするまでの猶予。
STARTING_STALE_MINUTES = 10
# active slot中のゲーム専用agent監視間隔。共通配信基盤やゲーム本体は触らない。
AGENT_REPAIR_POLL_SECONDS = 30.0
PENDING_SWITCH_STATUSES = {"queued", "in_progress", "busy"}


class RetroCornerError(RuntimeError):
    """User-facing failure in the daily retro corner."""


class RetroCornerTransitionError(RetroCornerError):
    """A coordinator transition failed with a machine-readable error code."""

    def __init__(self, message: str, *, error_code: str | None = None):
        super().__init__(message)
        self.error_code = error_code


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
    daily_each_game: bool = False
    randomize_start: bool = False
    start_window_minutes: int = 30
    target_matches: int = 3
    # mode="lottery": 固定枠を持たず、毎時 lottery_minute 分に確率 lottery_probability で
    # 発火を抽選し、当たれば遊べるゲームを無作為に選んで target_matches 試合 (上限
    # duration_minutes) 遊んで元のゲームへ戻る。他コーナーが進行中/待機中なら抽選を取りやめる。
    mode: str = "daily"
    lottery_probability: float = 0.3
    lottery_minute: int = 5
    lottery_wait_minutes: int = 10
    lottery_avoid_repeat: bool = True
    # mode="rotation": 24時間を登録ゲーム数で割った間隔で発火し、直近24時間に
    # 選ばれたゲームを候補から外す。登録ゲーム数が変わっても実効間隔を再計算する。
    rotation_period_hours: float = 24.0
    rotation_wait_minutes: int = 10


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

    from .corner_catalog import rotation_enabled, load_catalog
    rotation_has_nethack = False
    if rotation_enabled(g):
        raw = dict(raw)
        raw["games"] = [c.game for c in load_catalog(g) if c.adapter == "game"] or ["gnurobots"]
        rotation_has_nethack = any(c.game == "nethack" for c in load_catalog(g))

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
        daily_each_game=raw.get("daily_each_game", False),
        randomize_start=raw.get("randomize_start", False),
        start_window_minutes=raw.get("start_window_minutes", 30),
        target_matches=raw.get("target_matches", 3),
        mode=raw.get("mode", "daily"),
        lottery_probability=raw.get("lottery_probability", 0.3),
        lottery_minute=raw.get("lottery_minute", 5),
        lottery_wait_minutes=raw.get("lottery_wait_minutes", 10),
        lottery_avoid_repeat=raw.get("lottery_avoid_repeat", True),
        rotation_period_hours=raw.get("rotation_period_hours", 24.0),
        rotation_wait_minutes=raw.get(
            "rotation_wait_minutes", raw.get("lottery_wait_minutes", 10)
        ),
    )
    for key in ("daily_each_game", "randomize_start", "lottery_avoid_repeat"):
        if type(getattr(cfg, key)) is not bool:
            raise RetroCornerError(f"{key} must be boolean")
    if cfg.mode not in ("daily", "lottery", "rotation"):
        raise RetroCornerError('retro_corner.mode must be "daily", "lottery", or "rotation"')
    if (
        isinstance(cfg.lottery_probability, bool)
        or not isinstance(cfg.lottery_probability, (int, float))
        or not 0 < float(cfg.lottery_probability) <= 1
    ):
        raise RetroCornerError("lottery_probability must be a number in (0, 1]")
    if type(cfg.lottery_minute) is not int or not 0 <= cfg.lottery_minute <= 50:
        raise RetroCornerError("lottery_minute must be 0-50")
    if type(cfg.lottery_wait_minutes) is not int or not 0 <= cfg.lottery_wait_minutes <= 30:
        raise RetroCornerError("lottery_wait_minutes must be 0-30")
    if (
        isinstance(cfg.rotation_period_hours, bool)
        or not isinstance(cfg.rotation_period_hours, (int, float))
        or not 1 <= float(cfg.rotation_period_hours) <= 168
    ):
        raise RetroCornerError("rotation_period_hours must be a number in [1, 168]")
    if type(cfg.rotation_wait_minutes) is not int or not 0 <= cfg.rotation_wait_minutes <= 30:
        raise RetroCornerError("rotation_wait_minutes must be 0-30")
    if type(cfg.start_window_minutes) is not int or not 1 <= cfg.start_window_minutes <= 1440:
        raise RetroCornerError("start_window_minutes must be 1-1440")
    if type(cfg.target_matches) is not int or not 1 <= cfg.target_matches <= 100:
        raise RetroCornerError("target_matches must be 1-100")
    if type(cfg.require_program_boundary) is not bool:
        raise RetroCornerError("require_program_boundary must be boolean")
    if type(cfg.enabled) is not bool:
        raise RetroCornerError("retro_corner.enabled はtrue/falseである必要があります")
    if type(cfg.start_hour) is not int or not 0 <= cfg.start_hour <= 23:
        raise RetroCornerError("retro_corner.start_hour は0-23の整数である必要があります")
    if type(cfg.duration_minutes) is not int or not 1 <= cfg.duration_minutes <= 720:
        raise RetroCornerError("retro_corner.duration_minutes は1-720の整数である必要があります")
    if (
        cfg.mode == "lottery"
        and cfg.lottery_minute + cfg.lottery_wait_minutes + cfg.duration_minutes > LOTTERY_LATEST_END_MINUTE
    ):
        # 最悪でも「抽選 → 境界待ち上限 → 上限時間」で次の正時の前に終わる。
        raise RetroCornerError(
            "lottery mode: lottery_minute + lottery_wait_minutes + duration_minutes must be <= "
            f"{LOTTERY_LATEST_END_MINUTE} so a drawn corner ends before the next hour "
            "(fixed-slot corners start on the hour)"
        )
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
    nethack_corner = _raw_config(g).get("nethack_corner")
    if (
        cfg.mode == "rotation"
        and ("nethack" in cfg.games or rotation_has_nethack)
        and isinstance(nethack_corner, dict)
        and nethack_corner.get("enabled") is True
    ):
        raise RetroCornerError(
            "rotation modeでNetHackを登録する場合、専用の[nethack_corner]は無効にしてください"
        )
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
        daily_each_game=cfg.daily_each_game,
        randomize_start=cfg.randomize_start,
        start_window_minutes=cfg.start_window_minutes,
        target_matches=cfg.target_matches,
        mode=cfg.mode,
        lottery_probability=float(cfg.lottery_probability),
        lottery_minute=cfg.lottery_minute,
        lottery_wait_minutes=cfg.lottery_wait_minutes,
        lottery_avoid_repeat=cfg.lottery_avoid_repeat,
        rotation_period_hours=float(cfg.rotation_period_hours),
        rotation_wait_minutes=cfg.rotation_wait_minutes,
    )


def select_game(games: list[str], local_date: dt.date) -> str:
    if not games:
        raise RetroCornerError("retro corner対象ゲームがありません")
    return games[local_date.toordinal() % len(games)]


def scheduled_start(cfg: RetroCornerConfig, game: str, day: dt.date) -> dt.datetime:
    """Stable across interpreter restarts; never schedules beyond the local day."""
    start = dt.datetime.combine(day, dt.time(cfg.start_hour), ZoneInfo(cfg.timezone))
    seconds = min(cfg.start_window_minutes * 60, (24 - cfg.start_hour) * 3600)
    offset = random.Random(f"{day.isoformat()}|{game}").randrange(seconds) if cfg.randomize_start else 0
    return start + dt.timedelta(seconds=offset)


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
        spawn=None,
        rng: random.Random | None = None,
        stream_game: Callable[[str], None] | None = None,
        agent_repair: Callable[[str], bool | None] | None = None,
    ):
        self.g = g
        self.config = config or load_retro_corner_config(g)
        self.tz = ZoneInfo(self.config.timezone)
        self.store = GameSwitchStore(g.state_dir)
        if coordinator is None:
            from .stream_category import commit_hook

            coordinator = GameSwitchCoordinator(
                self.store,
                lambda spec: make_coordinator_adapter(g, spec),
                post_commit=commit_hook(g),
            )
        self.coordinator = coordinator
        # The production coordinator owns the post-commit category hook.
        # Legacy/test coordinators may not expose it, so retain the local
        # callback only for those paths and never announce a switch twice.
        self._coordinator_announces_stream = callable(
            getattr(coordinator, "post_commit", None)
        )
        self._now = now or (lambda: dt.datetime.now(self.tz))
        self._sleep = sleep
        self._active_game_reader = active_game_reader or self._canonical_active_game
        self._ensure_runtime = ensure_runtime or self._default_ensure_runtime
        self._chat = chat or (lambda text: enqueue_chat(self.g, text, source="retro-corner"))
        self._spawn = spawn or self._default_spawn_improve_proc
        self._rng = rng or random.Random()
        self._stream_game = stream_game or self._default_stream_game
        self._agent_repair = agent_repair or self._default_repair_active_agent
        self.state_path = Path(g.state_dir) / STATE_FILE
        self.lock_path = Path(g.state_dir) / LOCK_FILE
        self.tick_guard_path = Path(g.state_dir) / TICK_GUARD_FILE

    def _default_repair_active_agent(self, game: str) -> bool | None:
        """Repair only the active game's generation-owned agent window."""

        repair = getattr(self.coordinator, "repair_active_agent", None)
        if not callable(repair):
            # Older test doubles and non-agent corners simply have no
            # watchdog capability; the coordinator remains the source of
            # truth for whether a repair is safe.
            return None
        try:
            return repair(game=game)
        except Exception as exc:  # noqa: BLE001 - a watchdog must not end a slot
            print(
                f"[retro-corner] agent repair failed game={game} detail={_safe_detail(exc)}",
                file=sys.stderr,
            )
            return False

    def _repair_active_agent(self, state: dict[str, object]) -> bool | None:
        game = state.get("game")
        if not isinstance(game, str) or not game:
            return None
        try:
            return self._agent_repair(game)
        except Exception as exc:  # noqa: BLE001 - injected probes must be fail-closed
            print(
                f"[retro-corner] agent repair failed game={game} detail={_safe_detail(exc)}",
                file=sys.stderr,
            )
            return False

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
        """開始時チャット投稿 (ゲーム説明＋今回戦略の前回比較)。lock 保持中に呼ぶ。

        投稿失敗はコーナー自体を失敗させない。結果は state に記録する。
        """
        if self._scripted_hanjuku(state):
            state['ends_at'] = None
            state['target_matches'] = 1
            state['end_condition'] = 'game_over_or_screen_stalled'
        if state.get("announced"):
            return
        game = state.get("game")
        if not isinstance(game, str) or not game:
            return
        lead = "今回は" if getattr(self.config, "mode", "daily") == "lottery" else "本日は"
        text = (
            "レトロゲームコーナーです。" + lead
            + corner_intro(self.g, game)
            + describe_strategy_change(self.g.state_dir, game)
        )
        try:
            self._chat(text)
        except Exception as exc:
            state["announce_error"] = _safe_detail(exc)
            return
        state["announced"] = True
        state.pop("announce_error", None)

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
            "last_error_code": None,
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
        if state.get("status") not in {
            "idle", "waiting", "starting", "active", "restoring",
            "completed", "interrupted", "failed",
        }:
            raise RetroCornerError("retro corner state statusが不正です")
        return state

    def _write_state(self, state: dict[str, object]) -> None:
        if state.get("status") == "active":
            from .corner_ownership import bind_runtime
            bind_runtime(self.store, state, state.get("game"))
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_path.parent, 0o700)
        atomic_write_json(self.state_path, state)

    def _due_game(self, now: dt.datetime, state: dict) -> str | None:
        games = self.config.games if getattr(self.config, "daily_each_game", False) else [select_game(self.config.games, now.date())]
        attempted = state.get("daily_attempts", {}).get(now.date().isoformat(), [])
        for game in sorted(games, key=lambda name: (scheduled_start(self.config, name, now.date()), name)):
            if game not in attempted and now >= scheduled_start(self.config, game, now.date()):
                return game
        return None

    def _validate_games(self, names: list[str] | None = None) -> None:
        for name in names if names is not None else self.config.games:
            try:
                game = load_game(self.g, name)
            except Exception as exc:
                raise RetroCornerError(
                    f"retro corner対象ゲームを読み込めません ({name}): {_safe_detail(exc)}"
                ) from exc
            retro = game.raw.get("retro_corner", {})
            if not isinstance(retro, dict):
                raise RetroCornerError(f"{name} の [retro_corner] はtableである必要があります")
            enabled = retro.get("enabled", True)
            if type(enabled) is not bool:
                raise RetroCornerError(f"{name} の retro_corner.enabled はtrue/falseが必要です")
            if not enabled:
                raise RetroCornerError(f"retro corner対象は登録済みですが無効です: {name}")
            if game.adapter not in {"cli", "retroarch"}:
                raise RetroCornerError(
                    f"retro corner対象はCLIまたはRetroArchゲームに限定されます: {name}"
                )
            if game.adapter == "retroarch" and game.lifecycle.require_round_boundary is not True:
                raise RetroCornerError(
                    f"RetroArchのretro corner対象はlifecycle.require_round_boundary=trueが必要です: {name}"
                )
            corner_raw = game.raw.get("corner", {}) if isinstance(game.raw, dict) else {}
            self_play = isinstance(corner_raw, dict) and corner_raw.get("self_play") is True
            if game.agent.enabled is not True and not self_play:
                raise RetroCornerError(f"retro corner対象はagent.enabled=trueが必要です: {name}")

    @staticmethod
    def _require_success(result, action: str) -> None:
        if getattr(result, "status", None) != "succeeded":
            error_code = getattr(result, "error_code", None)
            detail = (
                getattr(result, "detail", None)
                or error_code
                or "unknown"
            )
            raise RetroCornerTransitionError(
                f"{action} に失敗しました: {_safe_detail(detail)}",
                error_code=error_code if isinstance(error_code, str) else None,
            )

    def _canonical_phase(self) -> str | None:
        """Read only the canonical phase used to gate automatic recovery."""

        canonical, _missing = self.store.canonical.load()
        phase = canonical.get("phase")
        return phase if isinstance(phase, str) else None

    def _recover_canonical_failure(self):
        """Recover one canonical ``failed`` state without touching a live drain.

        ``GameSwitchCoordinator.recover`` already contains the detailed
        cleanup contract.  This wrapper only decides whether the corner is
        allowed to call it: an in-progress boundary or an explicitly
        unrecoverable canonical state is never guessed at or force-reset.
        """

        recover = getattr(self.coordinator, "recover", None)
        if not callable(recover):
            return None
        canonical, _missing = self.store.canonical.load()
        if canonical.get("phase") != "failed":
            return None
        previous = canonical.get("previous")
        active = canonical.get("active")
        abandon_program_view = (
            isinstance(previous, dict)
            and previous.get("adapter") == "program"
            and active is None
        )
        try:
            return recover(abandon_program_view=abandon_program_view)
        except TypeError:
            # Keep compatibility with narrow test doubles and older deployed
            # coordinator shims; the real coordinator accepts this keyword.
            return recover()

    @staticmethod
    def _transition_error_code(exc: BaseException) -> str | None:
        value = getattr(exc, "error_code", None)
        return value if isinstance(value, str) else None

    def _transition_recovery_allowed(self, exc: BaseException) -> bool:
        """Return true only for the exact canonical-failure retry contract."""

        return (
            self._transition_error_code(exc) == ERROR_RECOVERY_REQUIRED
            and self._canonical_phase() == "failed"
        )

    def _transition_once(
        self,
        current: str | None,
        target: str,
        *,
        request_id: str | None = None,
    ):
        """Execute one transition attempt, without implicit recovery."""

        if current == target:
            # A queued switch can become redundant after an operator or a
            # recovery process has already brought the target back.  Let the
            # coordinator terminalize that receipt so it cannot block later
            # FIFO requests; a fresh request still remains a local no-op.
            receipt = self.store.receipts.load(request_id) if request_id else None
            if receipt is None:
                return None
            recorded_operation = receipt.get("operation")
            if recorded_operation == "switch":
                result = self._invoke_coordinator(
                    self.coordinator.switch, target, request_id=request_id
                )
                action = f"{current}->{target} switch"
            elif recorded_operation == "start":
                result = self._invoke_coordinator(
                    self.coordinator.start, target, request_id=request_id
                )
                action = f"{target} start"
            else:
                return None
            if getattr(result, "status", None) in PENDING_SWITCH_STATUSES:
                return result
            self._require_success(result, action)
            self._announce_after_switch(target)
            return result
        if current is None:
            result = self._invoke_coordinator(
                self.coordinator.start, target, request_id=request_id
            )
            action = f"{target} start"
        else:
            result = self._invoke_coordinator(
                self.coordinator.switch, target, request_id=request_id
            )
            action = f"{current}->{target} switch"
        if getattr(result, "status", None) in PENDING_SWITCH_STATUSES:
            return result
        self._require_success(result, action)
        self._announce_after_switch(target)
        return result

    def _transition_to(
        self,
        current: str | None,
        target: str,
        *,
        request_id: str | None = None,
    ):
        """Transition once, then recover/retry only a canonical failed state."""

        try:
            return self._transition_once(current, target, request_id=request_id)
        except RetroCornerTransitionError as exc:
            if not self._transition_recovery_allowed(exc):
                raise
            recovered = self._recover_canonical_failure()
            if getattr(recovered, "status", None) != "succeeded":
                raise
            return self._transition_once(current, target, request_id=request_id)

    @staticmethod
    def _invoke_coordinator(method, target=None, *, request_id: str | None = None):
        """Call old test doubles and current coordinators with one request ID."""

        try:
            parameters = inspect.signature(method).parameters.values()
            accepts_request_id = any(
                parameter.name == "request_id"
                or parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in parameters
            )
        except (TypeError, ValueError):
            accepts_request_id = True
        kwargs = (
            {"request_id": request_id}
            if request_id is not None and accepts_request_id
            else {}
        )
        return method(**kwargs) if target is None else method(target, **kwargs)

    def _announce_stream_game(self, game: str | None) -> None:
        """Let the stream's category/title follow the game that now runs.

        Strictly best-effort, and only after the coordinator has committed the
        switch: a stale category is cosmetic, whereas failing a switch because
        Twitch was unreachable would take the game itself down.
        """
        if not isinstance(game, str) or not game:
            return
        try:
            self._stream_game(game)
        except Exception as exc:
            print(
                f"[stream-game] status=failed game={game} detail={_safe_detail(exc)}",
                file=sys.stderr,
            )

    def _announce_after_switch(self, game: str | None) -> None:
        """Announce only when the coordinator has no post-commit hook."""
        if not self._coordinator_announces_stream:
            self._announce_stream_game(game)

    def _default_stream_game(self, game: str) -> None:
        from .stream_category import announce_stream_game

        announce_stream_game(self.g, game)

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
        if sys.platform == "linux" and os.environ.get("INVOCATION_ID"):
            # setsid/start_new_session は systemd の cgroup を抜けない。
            # retro-corner.service は KillMode=control-group の oneshot なので、
            # 子を同じ cgroup で起動すると tick 終了時に改善も殺される。
            # 改善とそのAI子プロセスを独立した bounded user service に投入し、
            # 投入失敗時は親cgroupへ安全にフォールバックしない。
            # systemd-run --user は user manager の bus を XDG_RUNTIME_DIR から
            # 解決するため、timer 起動の unit 環境に依存せず spawn 側で既定化する
            # (#947: Failed to connect to bus: No medium found)。
            import uuid

            repo_root = getattr(self.g, "repo_root", _repo_root())
            command = [
                "systemd-run", "--user", "--quiet", "--collect",
                f"--unit=docich-retro-improve-{uuid.uuid4().hex}",
                "--property=Type=exec",
                # Bounded wait on the cross-corner improve lane (1800s) plus
                # the job's own work budget.
                "--property=RuntimeMaxSec=3600",
                "--property=TimeoutStopSec=30",
                "--property=UMask=0077",
                f"--working-directory={repo_root}",
                f"--property=StandardOutput=append:{Path(log_path).resolve()}",
                f"--property=StandardError=append:{Path(log_path).resolve()}",
                "--setenv=DOCICH_ALLOW_REAL_AI=1",
                f"--setenv=PYTHONPATH={repo_root / 'src'}",
            ]
            if env.get("PATH"):
                command.append(f"--setenv=PATH={env['PATH']}")
            try:
                submitted = subprocess.run(
                    [*command, "--", *argv],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    stdin=subprocess.DEVNULL,
                    env=user_bus_env(),
                )
            except subprocess.TimeoutExpired as exc:
                raw = exc.stderr
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", "replace")
                detail = _safe_detail((raw or "").strip())
                raise RetroCornerError(
                    "改善ジョブの起動がタイムアウトしました (systemd-run 30s)"
                    + (f": {detail}" if detail else "")
                ) from exc
            if submitted.returncode != 0:
                # The operator needs the exit status and systemd's own message;
                # the full argv only pushed them past the 240-char durable limit.
                detail = _safe_detail((submitted.stderr or "").strip())
                raise RetroCornerError(
                    f"改善ジョブの起動に失敗しました (rc={submitted.returncode})"
                    + (f": {detail}" if detail else "")
                )
            return
        with open(log_path, "ab") as log_fh:
            subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                cwd=str(getattr(self.g, "repo_root", _repo_root())),
                env=env,
            )

    @staticmethod
    def _spawn_window_args(state: dict[str, object]) -> list[str]:
        """確定したコーナー期間のepoch秒引数 (不正なら空でstate読みへ戻す)。"""
        values = []
        for key in ("started_at", "ends_at"):
            raw = state.get(key)
            if not isinstance(raw, str) or not raw:
                return []
            try:
                values.append(dt.datetime.fromisoformat(raw).timestamp())
            except (ValueError, TypeError, OverflowError, OSError):
                return []
        return ["--started-at", f"{values[0]:.6f}", "--ends-at", f"{values[1]:.6f}"]

    def _spawn_improve_once(self, state: dict[str, object]) -> None:
        """終了時改善ジョブを切り離して起動する。失敗しても finish を壊さない。"""
        if state.get('game') == 'hanjuku-hero':
            state['improve_job'] = {'spawned': False, 'reason': 'hanjuku-improvement-deferred'}
            return
        if state.get("game") == "nethack":
            state["improve_job"] = {
                "spawned": False,
                "reason": "nethack-improvement-not-supported",
            }
            return
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
        # 日次複数ゲームでは select_game(日付) が実走ゲームと一致しないため、
        # 終了したゲームを argv で明示する (state は次コーナーで上書きされる)。
        game = state.get("game")
        if isinstance(game, str) and game:
            argv += ["--game", game]
        # queue dispatchでは次コーナーが共有stateを上書きし得るため、確定済みの
        # コーナー期間も明示して競合させる (job側のstate読みを不要にする)。
        argv += self._spawn_window_args(state)
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
        game: str | None = None,
        window: tuple[float, float] | None = None,
    ) -> dict:
        from .corner_improve import run_corner_improve

        if game is None:
            try:
                game = select_game(self.config.games, dt.date.fromisoformat(date_str))
            except ValueError as exc:
                raise RetroCornerError(f"日付が不正です: {date_str}") from exc
        else:
            try:
                game = validate_game_name(game)
            except NameValidationError as exc:
                raise RetroCornerError(f"ゲーム名が不正です: {game}") from exc
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
            window=window,
        )

    def _finish_locked(self, state: dict[str, object], completed_at: dt.datetime) -> CornerResult:
        if state.get("status") in {"active", "restoring"}:
            from .corner_ownership import verify_runtime
            verify_runtime(self.store, state, state.get("game"))
        game = state.get("game")
        previous = state.get("previous_game")
        if not isinstance(game, str):
            raise RetroCornerError("active retro corner stateにgameがありません")
        try:
            current = self._active_game_reader()
        except RetroCornerError:
            # A queued restore may be retried while another boundary drain is
            # still visible in canonical state.  The canonical active runtime
            # is safe to inspect and keeps the corner from being marked as a
            # terminal failure merely because the reader refuses non-ready
            # state.
            canonical, _missing = self.store.canonical.load()
            active = canonical.get("active")
            current = active.get("game") if isinstance(active, dict) else None
        if current != game:
            state.update(
                status="interrupted",
                completed_at=completed_at.isoformat(),
                last_error=None,
                last_error_code=None,
            )
            self._write_state(state)
            return self._state_result(state)

        request_id = state.get("switch_request_id")
        if not isinstance(request_id, str):
            request_id = new_request_id()
            state["switch_request_id"] = request_id
        state["status"] = "restoring"
        self._write_state(state)
        try:
            if previous is None:
                result = self._invoke_coordinator(
                    self.coordinator.stop, request_id=request_id
                )
                action = "retro corner stop"
            elif isinstance(previous, str) and previous != game:
                result = self._invoke_coordinator(
                    self.coordinator.switch, previous, request_id=request_id
                )
                action = f"{game}->{previous} restore"
            else:
                result = None
                action = "retro corner restore"
            if getattr(result, "status", None) in PENDING_SWITCH_STATUSES:
                state["switch_status"] = getattr(result, "status", "queued")
                self._write_state(state)
                return CornerResult(
                    "queued",
                    game=game,
                    previous_game=previous if isinstance(previous, str) else None,
                    detail=getattr(result, "detail", None)
                    or "ゲーム切替キューで順番待ちです",
                )
            if result is not None:
                self._require_success(result, action)
            # The corner restores the old game without going through
            # _transition_to, so announce here as well; otherwise the
            # category stays on the corner's game after it ends.
            if isinstance(previous, str) and previous != game:
                self._announce_after_switch(previous)
            state.update(
                status="completed",
                completed_at=completed_at.isoformat(),
                last_error=None,
                last_error_code=None,
            )
            state.pop("switch_request_id", None)
            state.pop("switch_status", None)
            # Persist the terminal corner state before handing control to the
            # independent improvement service.  systemd-run may start the
            # child immediately; if it reads the old active state first, the
            # improvement command returns wrong-status and silently skips.
            self._write_state(state)
            self._spawn_improve_once(state)
            self._write_state(state)
            return self._state_result(state)
        except Exception as exc:
            state.update(
                status="failed",
                completed_at=completed_at.isoformat(),
                last_error=_safe_detail(exc),
                last_error_code=self._transition_error_code(exc),
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
        if self._scripted_hanjuku(state):
            return
        ends_at = self._parse_ends_at(state)
        if ends_at is None or now >= ends_at:
            self._ensure_runtime()
            self._finish_locked(state, now)

    def _begin_locked(
        self,
        now: dt.datetime,
        *,
        scheduled: bool,
        target_override: str | None = None,
        extra_state: dict[str, object] | None = None,
    ) -> tuple[dict[str, object] | None, CornerResult | None]:
        """corner を開始する。target_override (抽選モード) は日次の時刻・試行台帳の判定を
        すべて飛ばし、指定ゲームだけを検証して始める。"""
        self._reconcile_stale_locked(now)
        existing = self._read_state()
        if existing.get("status") == "active":
            if scheduled:
                return None, CornerResult("noop", detail="already-active")
            raise RetroCornerError("retro cornerは既にactiveです")
        if existing.get("status") == "restoring":
            self._ensure_runtime()
            return existing, self._finish_locked(existing, now)
        if (
            existing.get("status") == "starting"
            and isinstance(existing.get("switch_request_id"), str)
        ):
            resumed, resume_result = self._resume_queued_start_locked(existing, now)
            if resume_result is not None:
                return resumed, resume_result
            return resumed, None
        # Immediate start in a rolling/lottery profile must use the same
        # eligibility gate as its timer, including dormant registrations.
        if (
            target_override is None
            and not scheduled
            and getattr(self.config, "mode", "daily") in {"rotation", "lottery"}
        ):
            candidates = self._playable_games()
            if not candidates:
                return None, CornerResult("noop", detail="no-eligible-game")
            target_override = self._rng.choice(candidates)
        # サブクラス corner (soren91/nethack) の config dataclass には新フィールドが
        # 無いため、既定値は getattr で落とす (後方互換)。
        daily_each_game = getattr(self.config, "daily_each_game", False)
        randomize_start = getattr(self.config, "randomize_start", False)
        attempts: dict = {}
        if target_override is not None:
            target = target_override
        else:
            if (
                scheduled
                and not daily_each_game
                and existing.get("date") == now.date().isoformat()
                and existing.get("status") in TERMINAL_STATUSES
            ):
                return None, CornerResult("noop", detail="already-ran-today")

            target = select_game(self.config.games, now.date())
            attempts = dict(existing.get("daily_attempts") or {})
            if daily_each_game:
                due = self._due_game(now, existing)
                if due is None:
                    if set(attempts.get(now.date().isoformat(), [])) >= set(self.config.games):
                        return None, CornerResult("noop", detail="already-ran-today")
                    return None, CornerResult("noop", detail="start-window-not-due")
                target = due
                if scheduled_start(self.config, target, now.date()) > now:
                    return None, CornerResult("noop", detail="start-window-not-due")
                attempts.setdefault(now.date().isoformat(), []).append(target)
            elif randomize_start:
                if scheduled_start(self.config, target, now.date()) > now:
                    return None, CornerResult("noop", detail="start-window-not-due")

        if target_override is not None:
            self._validate_games([target])
        else:
            # サブクラス (soren91/nethack) は引数なしで override しているため、通常経路は従来どおり。
            self._validate_games()
        self._ensure_runtime()
        try:
            previous = self._active_game_reader()
        except RetroCornerError:
            # A concurrent coordinator drain is still allowed to own the
            # canonical transition.  Preserve the currently active runtime
            # as the return target and let GameSwitchCoordinator queue this
            # corner's request instead of cancelling it.
            canonical, _missing = self.store.canonical.load()
            active = canonical.get("active")
            previous = active.get("game") if isinstance(active, dict) else None
        ends_at = now + dt.timedelta(minutes=self.config.duration_minutes)
        if extra_state and extra_state.get("rotation_request_id") and previous == target:
            raise RetroCornerError("rotation target already owned by another execution")
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
            "last_error_code": None,
            "switch_request_id": new_request_id(),
        }
        if target_override is not None:
            state["target_matches"] = getattr(self.config, "target_matches", 3)
        elif daily_each_game:
            state["daily_attempts"] = attempts
            state["target_matches"] = getattr(self.config, "target_matches", 3)
        if extra_state:
            state.update(extra_state)
        self._write_state(state)
        try:
            transition = self._transition_to(
                previous,
                target,
                request_id=state["switch_request_id"],
            )
            if getattr(transition, "status", None) in PENDING_SWITCH_STATUSES:
                state["switch_status"] = getattr(transition, "status", "queued")
                self._write_state(state)
                return state, CornerResult(
                    "queued",
                    game=target,
                    previous_game=previous,
                    detail=getattr(transition, "detail", None)
                    or "ゲーム切替キューで順番待ちです",
                )
            started = self._local_now()
            state.pop("switch_request_id", None)
            state.pop("switch_status", None)
            state.update(status="active", started_at=started.isoformat(),
                         ends_at=(started + dt.timedelta(minutes=self.config.duration_minutes)).isoformat())
            self._announce_start_locked(state)
            self._write_state(state)
        except Exception as exc:
            state.update(
                status="failed",
                completed_at=self._local_now().isoformat(),
                last_error=_safe_detail(exc),
                last_error_code=self._transition_error_code(exc),
            )
            self._write_state(state)
            if isinstance(exc, RetroCornerError):
                raise
            raise RetroCornerError(_safe_detail(exc)) from exc
        return state, None

    def _resume_queued_start_locked(
        self, state: dict[str, object], now: dt.datetime
    ) -> tuple[dict[str, object] | None, CornerResult | None]:
        """Retry a corner start whose game-switch request was queued."""

        request_id = state.get("switch_request_id")
        target = state.get("game")
        previous = state.get("previous_game")
        if not isinstance(request_id, str) or not isinstance(target, str):
            return None, None
        if previous is not None and not isinstance(previous, str):
            previous = None
        try:
            transition = self._transition_to(
                previous,
                target,
                request_id=request_id,
            )
            if getattr(transition, "status", None) in PENDING_SWITCH_STATUSES:
                state["switch_status"] = getattr(transition, "status", "queued")
                self._write_state(state)
                return state, CornerResult(
                    "queued",
                    game=target,
                    previous_game=previous,
                    detail=getattr(transition, "detail", None)
                    or "ゲーム切替キューで順番待ちです",
                )
        except Exception as exc:
            state.update(
                status="failed",
                completed_at=now.isoformat(),
                last_error=_safe_detail(exc),
                last_error_code=self._transition_error_code(exc),
            )
            self._write_state(state)
            if isinstance(exc, RetroCornerError):
                raise
            raise RetroCornerError(_safe_detail(exc)) from exc

        started = self._local_now()
        state.pop("switch_request_id", None)
        state.pop("switch_status", None)
        state.update(
            status="active",
            started_at=started.isoformat(),
            ends_at=(started + dt.timedelta(minutes=self.config.duration_minutes)).isoformat(),
            last_error=None,
            last_error_code=None,
        )
        self._announce_start_locked(state)
        self._write_state(state)
        return state, None

    def _target_reached(self, state: dict) -> bool:
        """設定試合数の検知: scorelogの当該コーナー開始以降の件数で判定する。"""
        target = state.get("target_matches")
        if type(target) is not int or not 1 <= target <= 100:
            return False
        game = state.get("game")
        if not isinstance(game, str) or not game:
            return False
        try:
            start_ts = dt.datetime.fromisoformat(str(state.get("started_at"))).timestamp()
        except (ValueError, TypeError, OverflowError, OSError):
            return False
        log_path = Path(self.g.state_dir) / "scores" / f"{game}.jsonl"
        try:
            lines = log_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return False
        count = 0
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict) or entry.get("game") != game:
                continue
            try:
                ts = float(entry.get("ts", 0) or 0)
            except (TypeError, ValueError):
                continue
            if ts >= start_ts:
                count += 1
        return count >= target

    def _rotation_stop_result(self):
        from .corner_rotation import clear_rotation_stop_request, rotation_stop_requested

        if not rotation_stop_requested(self.g, self.state_path):
            return None
        with self._locked():
            latest = self._read_state()
            if latest.get("status") in {"active", "restoring"}:
                result = self._finish_locked(latest, self._local_now())
            else:
                result = self._state_result(latest)
        if getattr(result, "status", None) in {"completed", "interrupted"}:
            clear_rotation_stop_request(self.g, self.state_path)
        return result

    def _wait_and_finish(self, state: dict[str, object]) -> CornerResult:
        if self._scripted_hanjuku(state):
            return self._wait_hanjuku(state)
        ends_at = self._parse_ends_at(state)
        if ends_at is None:
            raise RetroCornerError("retro corner ends_atが不正です")
        if not state.get("target_matches"):
            stopped = self._rotation_stop_result()
            if stopped is not None:
                return stopped
            remaining = max(0.0, (ends_at - self._local_now()).total_seconds())
            self._sleep(remaining)
            stopped = self._rotation_stop_result()
            if stopped is not None:
                return stopped
            with self._locked():
                latest = self._read_state()
                if latest.get("status") != "active":
                    return self._state_result(latest)
                return self._finish_locked(latest, self._local_now())
        else:
            # 設定試合数で早期終了: 試合境界はwrapperの保存後に訪れる。時間上限
            # ends_at は必ず残し、来なければ従来どおり ends_at で終了する。
            next_agent_repair_at = 0.0
            agent_repair_failed = False
            while True:
                stopped = self._rotation_stop_result()
                if stopped is not None:
                    return stopped
                remaining = max(0.0, (ends_at - self._local_now()).total_seconds())
                self._sleep(min(5.0, remaining))
                with self._locked():
                    latest = self._read_state()
                    if latest.get("status") != "active":
                        return self._state_result(latest)
                    if remaining <= 0.0:
                        return self._finish_locked(latest, self._local_now())
                    now_monotonic = time.monotonic()
                    if now_monotonic >= next_agent_repair_at:
                        repaired = self._repair_active_agent(latest)
                        next_agent_repair_at = now_monotonic + AGENT_REPAIR_POLL_SECONDS
                        if repaired is False and not agent_repair_failed:
                            print(
                                f"[retro-corner] agent repair did not recover game={latest.get('game')}",
                                file=sys.stderr,
                            )
                            agent_repair_failed = True
                        elif repaired is True and agent_repair_failed:
                            print(
                                f"[retro-corner] agent recovered game={latest.get('game')}",
                                file=sys.stderr,
                            )
                            agent_repair_failed = False
                    if self._target_reached(latest):
                        return self._finish_locked(latest, self._local_now())

    def _scripted_hanjuku(self, state):
        if state.get('game') != 'hanjuku-hero':
            return False
        from .hanjuku_run import enabled
        return enabled(load_game(self.g, 'hanjuku-hero'))

    def _wait_hanjuku(self, state):
        """No fixed session deadline: observe until game-over or 300s stasis."""
        from .adapters import make_adapter
        from .agent.fence import AgentFence, shared_section
        from .game_switch import DeadlineExceededError, GameSwitchBusyError
        from .hanjuku_run import event
        from .naming import runtime_directory
        next_repair = 0.
        owned_runtime = state.get('bot_runtime_id')
        while True:
            stopped = self._rotation_stop_result()
            if stopped is not None:
                return stopped
            canonical, _missing = self.store.canonical.load()
            active = canonical.get('active') or {}
            if active.get('game') != 'hanjuku-hero':
                with self._locked():
                    return self._finish_locked(self._read_state(), self._local_now())
            if owned_runtime is not None and active.get('runtime_id') != owned_runtime:
                raise RetroCornerError('Hanjuku runtime changed during the corner')
            owned_runtime = active.get('runtime_id')
            fence = AgentFence(game=active['game'], runtime_id=active['runtime_id'],
                               generation=active['generation'], lease_id=active['lease_id'])
            adapter = make_adapter(self.g, load_game(self.g, 'hanjuku-hero'), fence=fence)
            try:
                observation = shared_section(self.g.state_dir, adapter.observe)
            except (DeadlineExceededError, GameSwitchBusyError):
                # Audio/menu maintenance can temporarily own the input gate.
                # A missing observation is not an ending or unchanged frame.
                # Recheck stop requests and canonical ownership on every retry.
                event(runtime_directory(self.g.state_dir, owned_runtime), {
                    'event': 'observation_retry', 'at': time.time(),
                    'reason': 'input_or_switch_busy',
                })
                self._sleep(2.)
                continue
            run = observation.meta.get('hanjuku') or {}
            with self._locked():
                latest = self._read_state()
                if latest.get('status') != 'active':
                    return self._state_result(latest)
                latest['ends_at'] = None
                latest['end_reason'] = run.get('terminal_reason')
                latest['bot_phase'] = run.get('phase')
                latest['bot_actions_sent'] = run.get('actions_sent', 0)
                latest['battles_started'] = run.get('battles_started', 0)
                latest['battles_finished'] = run.get('battles_finished', 0)
                latest['screen_unchanged_seconds'] = run.get('unchanged_seconds', 0)
                latest['bot_runtime_id'] = active['runtime_id']
                self._write_state(latest)
                if run.get('terminal_reason') in {'game_over', 'screen_stalled'}:
                    return self._finish_locked(latest, self._local_now())
                if time.monotonic() >= next_repair:
                    self._repair_active_agent(latest)
                    next_repair = time.monotonic() + AGENT_REPAIR_POLL_SECONDS
            self._sleep(2.)

    def _retry_restoring_tick(self, now: dt.datetime) -> CornerResult | None:
        """Retry a queued corner restore before considering a new slot."""

        with self._locked():
            state = self._read_state()
            if state.get("status") != "restoring":
                return None
            self._ensure_runtime()
            return self._finish_locked(state, now)

    def _retry_starting_tick(self, now: dt.datetime) -> CornerResult | None:
        """Retry a queued corner start independently of its schedule window."""

        with self._locked():
            state = self._read_state()
            if (
                state.get("status") != "starting"
                or not isinstance(state.get("switch_request_id"), str)
            ):
                return None
            resumed, result = self._begin_locked(now, scheduled=True)
        if result is not None:
            return result
        if resumed is None:
            return None
        return self._wait_and_finish(resumed)

    @staticmethod
    def _failed_state_is_recoverable(state: dict[str, object]) -> bool:
        """Recognize only failures caused by the canonical recovery gate.

        Older deployed states predate ``last_error_code``.  The legacy detail
        is accepted only as the exact fixed coordinator message, never as a
        general-purpose error string or an operator-supplied command.
        """

        if state.get("status") != "failed":
            return False
        if state.get("last_error_code") == ERROR_RECOVERY_REQUIRED:
            return True
        detail = state.get("last_error")
        return isinstance(detail, str) and "canonical stateの復旧が必要です" in detail

    def _retry_failed_tick(self, now: dt.datetime) -> CornerResult | None:
        """Retry a recoverable failed slot before evaluating a new schedule."""

        try:
            state = self._read_state()
        except RetroCornerError:
            raise
        if not self._failed_state_is_recoverable(state):
            return None
        return self.recover_failed()

    def recover_failed(self) -> CornerResult:
        """Recover and retry one failed retro slot without selecting a new game.

        This is the fixed owner-only recovery entry point.  It never resets a
        live ``draining`` boundary, never touches an explicit
        ``recovery_required`` phase, and preserves the rotation history so a
        recovered slot cannot cause a duplicate selection inside 24 hours.
        """

        now = self._local_now()
        with self._locked():
            state = self._read_state()
            if not self._failed_state_is_recoverable(state):
                return CornerResult("noop", detail="failed-slot-not-recoverable")
            target = state.get("game")
            if not isinstance(target, str) or not target:
                return CornerResult("failed", detail="failed-slot-has-no-game")

            canonical, _missing = self.store.canonical.load()
            phase = canonical.get("phase")
            if phase == "draining":
                return CornerResult(
                    "queued",
                    game=target,
                    detail="試合終了境界の待機中です。期限前のdrainingには触れません",
                )
            if phase == "recovery_required":
                return CornerResult(
                    "failed",
                    game=target,
                    detail="canonical stateがrecovery_requiredのため自動復旧を停止しました",
                )
            if phase == "failed":
                recovered = self._recover_canonical_failure()
                if getattr(recovered, "status", None) != "succeeded":
                    detail = getattr(recovered, "detail", None) or "canonical failed状態を復旧できません"
                    return CornerResult("failed", game=target, detail=_safe_detail(detail))
                canonical, _missing = self.store.canonical.load()
                phase = canonical.get("phase")
            if phase not in {"idle", "ready"}:
                return CornerResult(
                    "queued",
                    game=target,
                    detail=f"canonical phase={phase!r} の完了を待っています",
                )

            extra_state: dict[str, object] = {}
            for key in ("rotation", "lottery", "daily_attempts"):
                value = state.get(key)
                if value is not None:
                    extra_state[key] = value
            rotation = extra_state.get("rotation")
            if isinstance(rotation, dict):
                rotation = dict(rotation)
                rotation["last_result"] = {
                    "result": "recovery-retry",
                    "game": target,
                    "at": now.isoformat(),
                }
                extra_state["rotation"] = rotation
            resumed, result = self._begin_locked(
                now,
                scheduled=True,
                target_override=target,
                extra_state=extra_state,
            )
        if result is not None:
            return result
        if resumed is None:
            return CornerResult("failed", game=target, detail="failed-slotの再開状態を作成できません")
        return self._wait_and_finish(resumed)

    def start(self) -> CornerResult:
        from .corner_catalog import rotation_enabled
        if rotation_enabled(self.g):
            from .corner_rotation import run_manual
            return run_manual(self.g, self, self.config.games)
        return self._start_direct()

    def _start_direct(self) -> CornerResult:
        with self._locked():
            state, result = self._begin_locked(self._local_now(), scheduled=False)
        if result is not None:
            return result
        assert state is not None
        return self._wait_and_finish(state)

    def run_rotation(self, request_id: str, target: str | None = None) -> CornerResult:
        """Execute/replay one common rotation request, bypassing legacy calendars."""
        with self._tick_guard() as single:
            if not single:
                return CornerResult("queued", detail="already-running")
            with self._locked():
                state = self._read_state()
                if state.get("rotation_request_id") == request_id:
                    status = state.get("status")
                    if status == "completed":
                        return self._state_result(state)
                    if status == "starting":
                        state, result = self._resume_queued_start_locked(state, self._local_now())
                        if result is not None:
                            return result
                    elif status == "restoring":
                        return self._finish_locked(state, self._local_now())
                    elif status != "active":
                        raise RetroCornerError("rotation execution requires recovery")
                    elif status == "active":
                        from .corner_ownership import verify_runtime
                        verify_runtime(self.store, state, state.get("game"))
                else:
                    if state.get("status") not in {"idle", "completed", "interrupted"}:
                        raise RetroCornerError("rotation execution owner mismatch")
                    state, result = self._begin_locked(
                        self._local_now(), scheduled=False, target_override=target,
                        extra_state={"rotation_request_id": request_id, "switch_request_id": request_id},
                    )
                    if result is not None:
                        return result
            if state is None:
                raise RetroCornerError("rotation execution state missing")
            return self._wait_and_finish(state)

    def stop(self) -> CornerResult:
        from .corner_catalog import rotation_enabled
        if rotation_enabled(self.g):
            from .corner_rotation import stop_manual
            return stop_manual(self.g, self, self._stop_direct, busy_callback=self._stop_direct)
        return self._stop_direct()

    def _stop_direct(self) -> CornerResult:
        with self._locked():
            state = self._read_state()
            if state.get("status") not in {"active", "restoring"}:
                return CornerResult("noop", detail="not-active")
            self._ensure_runtime()
            return self._finish_locked(state, self._local_now())

    def tick(self) -> CornerResult:
        from .corner_catalog import rotation_enabled
        if rotation_enabled(self.g):
            from .corner_rotation import CornerRotationManager
            result = CornerRotationManager(self.g).tick()
            return CornerResult(result["status"], detail=result.get("reason"))
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
            failed = self._retry_failed_tick(now)
            if failed is not None:
                return failed
            if getattr(self.config, "mode", "daily") == "rotation":
                return self._rotation_tick()
            if getattr(self.config, "mode", "daily") == "lottery":
                return self._lottery_tick()
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
                        daily = getattr(self.config, "daily_each_game", False)
                        attempted = set(
                            (state.get("daily_attempts") or {}).get(now.date().isoformat(), [])
                        )
                        if not daily or attempted >= set(self.config.games):
                            return CornerResult("noop", detail="already-ran-today")
                        # 日次モード: 同日の次のゲームへ。試行履歴を保持したまま
                        # waiting に戻し、_due_game が残りゲームを選べるようにする。
                        waiting = self._default_state()
                        waiting.update(
                            status="waiting",
                            date=state.get("date"),
                            requested_at=now.timestamp(),
                            daily_attempts=state.get("daily_attempts") or {},
                        )
                        self._write_state(waiting)
                        state = waiting
                    else:
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
                request_id = state.get("switch_request_id")
                if not isinstance(request_id, str):
                    request_id = new_request_id()
                    state["switch_request_id"] = request_id
                transition = self._transition_to(
                    current, state["game"], request_id=request_id
                )
                if getattr(transition, "status", None) in PENDING_SWITCH_STATUSES:
                    state["switch_status"] = getattr(transition, "status", "queued")
                    self._write_state(state)
                    return CornerResult(
                        "queued",
                        game=state.get("game") if isinstance(state.get("game"), str) else None,
                        previous_game=(
                            state.get("previous_game")
                            if isinstance(state.get("previous_game"), str)
                            else None
                        ),
                        detail=getattr(transition, "detail", None)
                        or "ゲーム切替キューで順番待ちです",
                    )
                started = self._local_now()
                state.pop("switch_request_id", None)
                state.pop("switch_status", None)
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

    # ---- 毎時抽選モード -------------------------------------------------------

    @staticmethod
    def _required_executables(game) -> list[str]:
        raw = game.raw.get("retro_corner", {}) if isinstance(game.raw, dict) else {}
        required = raw.get("requires", []) if isinstance(raw, dict) else []
        return [r for r in required if isinstance(r, str) and r] if isinstance(required, list) else []

    @staticmethod
    def _executable_exists(path: str) -> bool:
        if "/" in path:
            return os.path.isfile(path) and os.access(path, os.X_OK)
        return shutil.which(path) is not None

    def _retroarch_ready(self, game) -> bool:
        """Check the local runtime prerequisites for a RetroArch corner.

        ROMs are intentionally outside Git, so a checkout can pass config
        validation while remaining ineligible.  Resolve the ROM/core and the
        same helper binaries used by ``RetroArchCoordinatorAdapter.preflight``
        before including the game in the random candidate set.
        """
        from .adapters.retroarch import resolve_core, resolve_rom

        resolve_rom(self.g, game)
        resolve_core(game)
        required = self._required_executables(game)
        for binary in ("dbus-run-session", "retroarch"):
            if binary not in required:
                required.append(binary)
        if self.g.display.viewport_width > 0:
            for binary in ("Xvfb", "ffplay", "xdotool"):
                if binary not in required:
                    required.append(binary)
        return all(self._executable_exists(path) for path in required)

    def _playable_games(self) -> list[str]:
        """設定と実行環境が揃ったゲームだけを抽選候補にする。"""
        playable = []
        for name in self.config.games:
            try:
                self._validate_games([name])
                game = load_game(self.g, name)
                if game.adapter == "retroarch":
                    ready = self._retroarch_ready(game)
                else:
                    ready = all(
                        self._executable_exists(path)
                        for path in self._required_executables(game)
                    )
            except Exception:
                # 1つの壊れたゲーム設定で抽選 tick 全体を落とさず、遊べないものとして除外する。
                continue
            if ready:
                playable.append(name)
        return playable

    def _lottery_pick(self, state: dict[str, object]) -> str | None:
        candidates = self._playable_games()
        if getattr(self.config, "lottery_avoid_repeat", True) and len(candidates) > 1:
            candidates = [name for name in candidates if name != state.get("game")] or candidates
        return self._rng.choice(candidates) if candidates else None

    def _rotation_interval_seconds(self) -> float:
        """Return the rolling slot interval derived from the current game count."""
        return float(self.config.rotation_period_hours) * 3600.0 / len(self.config.games)

    def _rotation_datetime(self, value: object) -> dt.datetime | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            parsed = dt.datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=self.tz)
        return parsed.astimezone(self.tz)

    def _rotation_history(self, rotation: dict[str, object], now: dt.datetime) -> list[dict[str, str]]:
        """Keep only selections that still participate in the rolling cooldown."""
        raw_history = rotation.get("selection_history")
        if not isinstance(raw_history, list):
            return []
        cutoff = now - dt.timedelta(hours=float(self.config.rotation_period_hours))
        history: list[dict[str, str]] = []
        for raw in raw_history:
            if not isinstance(raw, dict):
                continue
            game = raw.get("game")
            selected_at = self._rotation_datetime(raw.get("selected_at"))
            if not isinstance(game, str) or game not in self.config.games or selected_at is None:
                continue
            # Exactly 24 hours old is eligible again; only a strictly newer
            # selection blocks the game.
            if selected_at > cutoff:
                history.append({"game": game, "selected_at": selected_at.isoformat()})
        history.sort(key=lambda item: item["selected_at"])
        return history

    def _rotation_state(self, state: dict[str, object], now: dt.datetime) -> tuple[dict[str, object], bool]:
        """Load/migrate the persisted rolling schedule without losing history."""
        raw = state.get("rotation")
        raw_rotation = raw if isinstance(raw, dict) else {}
        interval_seconds = self._rotation_interval_seconds()
        history = self._rotation_history(raw_rotation, now)
        configured_games = list(self.config.games)
        try:
            old_interval = float(raw_rotation.get("interval_seconds"))
        except (TypeError, ValueError):
            old_interval = 0.0
        games_changed = raw_rotation.get("games") != configured_games
        interval_changed = abs(old_interval - interval_seconds) > 0.5
        reset_schedule = not raw_rotation or games_changed or interval_changed

        rotation = dict(raw_rotation)
        rotation.update(
            {
                "period_hours": float(self.config.rotation_period_hours),
                "interval_seconds": interval_seconds,
                "games": configured_games,
                "selection_history": history,
            }
        )
        anchor = self._rotation_datetime(rotation.get("anchor_at"))
        next_due = self._rotation_datetime(rotation.get("next_due_at"))
        if reset_schedule or anchor is None or next_due is None:
            rotation["anchor_at"] = now.isoformat()
            rotation["next_due_at"] = now.isoformat()
            rotation.pop("pending", None)
        else:
            rotation["anchor_at"] = anchor.isoformat()
            rotation["next_due_at"] = next_due.isoformat()

        pending = rotation.get("pending")
        if isinstance(pending, dict):
            pending_game = pending.get("game")
            pending_at = self._rotation_datetime(pending.get("selected_at"))
            pending_deadline = self._rotation_datetime(pending.get("deadline_at"))
            queued = pending.get("queued") is True or pending_deadline is None
            if (
                not isinstance(pending_game, str)
                or pending_game not in configured_games
                or pending_at is None
            ):
                rotation.pop("pending", None)
            elif queued:
                rotation["pending"] = {
                    "game": pending_game,
                    "selected_at": pending_at.isoformat(),
                    "queued": True,
                }
            elif now >= pending_deadline:
                # A selected slot is never discarded merely because its
                # boundary/program wait window elapsed.  It remains the FIFO
                # item for the next tick, which receives a fresh wait window.
                rotation["pending"] = {
                    "game": pending_game,
                    "selected_at": pending_at.isoformat(),
                    "queued": True,
                }
            else:
                rotation["pending"] = {
                    "game": pending_game,
                    "selected_at": pending_at.isoformat(),
                    "deadline_at": pending_deadline.isoformat(),
                }
        changed = rotation != raw_rotation
        return rotation, changed

    def _rotation_candidates(self, rotation: dict[str, object], now: dt.datetime) -> list[str]:
        recent = {
            item["game"]
            for item in self._rotation_history(rotation, now)
            if isinstance(item.get("game"), str)
        }
        return [game for game in self._playable_games() if game not in recent]

    def _rotation_pick(self, rotation: dict[str, object], now: dt.datetime) -> str | None:
        candidates = self._rotation_candidates(rotation, now)
        return self._rng.choice(candidates) if candidates else None

    def _rotation_fixed_slot_windows(
        self, now: dt.datetime
    ) -> list[tuple[dt.datetime, dt.datetime, str]]:
        """Return enabled fixed-corner windows around ``now``.

        The fixed corners are configured outside ``[retro_corner]`` and do not
        share the retro manager's dataclass. Read their scheduling contract
        from the selected profile so a rolling slot does not assume the live
        hours in source code. A malformed enabled fixed corner fails closed.
        """
        raw = _raw_config(self.g)
        windows: list[tuple[dt.datetime, dt.datetime, str]] = []
        for section_name in FIXED_CORNER_SECTIONS:
            section = raw.get(section_name)
            if not isinstance(section, dict) or section.get("enabled") is not True:
                continue

            start_hour = section.get("start_hour")
            duration_minutes = section.get("duration_minutes")
            if type(start_hour) is not int or not 0 <= start_hour <= 23:
                raise RetroCornerError(f"{section_name}.start_hour が不正です")
            if duration_minutes is None:
                # Duration-less fixed corners (the content-driven PAPER corner)
                # have no statically reservable window. They are protected by
                # the runtime program-slot exclusivity instead, so a due
                # rotation may start and the duration-less corner waits.
                continue
            if type(duration_minutes) is not int or not 1 <= duration_minutes <= 720:
                raise RetroCornerError(f"{section_name}.duration_minutes が不正です")

            timezone_name = section.get("timezone", self.config.timezone)
            if not isinstance(timezone_name, str) or not timezone_name.strip():
                raise RetroCornerError(f"{section_name}.timezone が不正です")
            try:
                fixed_tz = ZoneInfo(timezone_name)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise RetroCornerError(f"{section_name}.timezone が不正です") from exc

            weekdays_raw = section.get("weekdays")
            if weekdays_raw is None or weekdays_raw == []:
                weekdays: set[int] | None = None
            elif (
                isinstance(weekdays_raw, list)
                and all(type(day) is int and 0 <= day <= 6 for day in weekdays_raw)
            ):
                weekdays = set(weekdays_raw)
            else:
                raise RetroCornerError(f"{section_name}.weekdays が不正です")

            fixed_now = now.astimezone(fixed_tz)
            # A weekday-restricted fixed corner can be seven days away. The
            # extra day also covers an active window that crossed midnight.
            for day_offset in range(-1, 9):
                day = fixed_now.date() + dt.timedelta(days=day_offset)
                if weekdays is not None and day.weekday() not in weekdays:
                    continue
                start_local = dt.datetime.combine(
                    day, dt.time(hour=start_hour), tzinfo=fixed_tz
                )
                end_local = start_local + dt.timedelta(minutes=duration_minutes)
                start = start_local.astimezone(self.tz)
                end = end_local.astimezone(self.tz)
                if end > now:
                    windows.append((start, end, section_name))
        return sorted(windows, key=lambda item: item[0])

    def _rotation_fixed_slot_guard(self, now: dt.datetime) -> str | None:
        """Defer a due rotation that could overlap an imminent fixed slot.

        The rotation may wait for ``rotation_wait_minutes`` before acquiring
        the program slot, then holds it through its configured duration. The
        worst-case end therefore gets a five-minute handoff buffer before the
        next fixed corner's start. Once a fixed window is active, defer as
        well; the existing other-corner check handles unrelated busy owners.
        """
        latest_end = now + dt.timedelta(
            minutes=self.config.rotation_wait_minutes + self.config.duration_minutes
        )
        buffer = dt.timedelta(minutes=ROTATION_FIXED_SLOT_BUFFER_MINUTES)
        for start, end, section_name in self._rotation_fixed_slot_windows(now):
            if start <= now < end:
                return f"fixed-slot-active:{section_name}"
            if start > now:
                if latest_end > start - buffer:
                    return f"fixed-slot-imminent:{section_name}"
                # Windows are sorted, so later fixed starts have more room.
                return None
        return None

    def _rotation_cancel_pending(self, game: str, reason: str) -> None:
        with self._locked():
            state = self._read_state()
            rotation, _changed = self._rotation_state(state, self._local_now())
            pending = rotation.get("pending")
            if isinstance(pending, dict) and pending.get("game") == game:
                if reason == "wait-expired":
                    pending = dict(pending)
                    pending.pop("deadline_at", None)
                    pending["queued"] = True
                    rotation["pending"] = pending
                    result = "queued"
                else:
                    rotation.pop("pending", None)
                    result = "cancelled"
                rotation["last_result"] = {
                    "result": result,
                    "reason": reason,
                    "game": game,
                    "at": self._local_now().isoformat(),
                }
                state["rotation"] = rotation
                self._write_state(state)

    def _rotation_run(self, game: str) -> CornerResult:
        now = self._local_now()
        with self._locked():
            state = self._read_state()
            rotation, _changed = self._rotation_state(state, now)
            pending = rotation.get("pending")
            if not isinstance(pending, dict) or pending.get("game") != game:
                return CornerResult("noop", detail="rotation-selection-lost")
            selected_at = self._rotation_datetime(pending.get("selected_at")) or now
            history = self._rotation_history(rotation, now)
            history.append({"game": game, "selected_at": selected_at.isoformat()})
            rotation["selection_history"] = self._rotation_history(
                {"selection_history": history}, now
            )
            rotation.pop("pending", None)
            rotation["next_due_at"] = (
                selected_at + dt.timedelta(seconds=self._rotation_interval_seconds())
            ).isoformat()
            rotation["last_selected"] = {"game": game, "at": selected_at.isoformat()}
            rotation["last_result"] = {
                "result": "fire",
                "game": game,
                "at": selected_at.isoformat(),
            }
            state["rotation"] = rotation
            # Reserve the selection before the coordinator transition. A
            # failed transition must not immediately retry the same game.
            self._write_state(state)
            state, result = self._begin_locked(
                now,
                scheduled=True,
                target_override=game,
                extra_state={"rotation": rotation},
            )
        if result is not None:
            return result
        assert state is not None
        return self._wait_and_finish(state)

    def _rotation_tick(self) -> CornerResult:
        """Run one rolling slot every 24/N hours with a 24-hour per-game cooldown."""
        from .corner_boundary import CornerWaitExpired, program_slot

        cfg = self.config
        now = self._local_now()
        if not cfg.enabled:
            return CornerResult("noop", detail="disabled")

        game: str | None = None
        deadline_ts: float | None = None
        resume_state: dict[str, object] | None = None
        with self._locked():
            self._reconcile_stale_locked(now)
            state = self._read_state()
            status = state.get("status")
            if status == "active":
                repaired = self._repair_active_agent(state)
                if repaired is False:
                    return CornerResult("noop", game=state.get("game"), detail="agent-repair-failed")
                return CornerResult("noop", detail="already-active")
            if status == "starting":
                if isinstance(state.get("switch_request_id"), str):
                    resumed, resume_result = self._resume_queued_start_locked(state, now)
                    if resume_result is not None:
                        return resume_result
                    resume_state = resumed
                elif not self._lottery_starting_is_stale(state, now):
                    return CornerResult("noop", detail="already-starting")
                elif resume_state is None:
                    state.update(status="interrupted", completed_at=now.isoformat())

            if resume_state is not None:
                state = resume_state

            rotation, changed = self._rotation_state(state, now)
            pending = rotation.get("pending")
            if isinstance(pending, dict):
                pending_game = pending.get("game")
                pending_deadline = self._rotation_datetime(pending.get("deadline_at"))
                if isinstance(pending_game, str) and (
                    pending.get("queued") is True
                    or (pending_deadline is not None and now < pending_deadline)
                ):
                    game = pending_game
                    if pending.get("queued") is True or pending_deadline is None:
                        deadline = now + dt.timedelta(minutes=cfg.rotation_wait_minutes)
                        rotation["pending"] = {
                            "game": pending_game,
                            "selected_at": pending.get("selected_at", now.isoformat()),
                            "deadline_at": deadline.isoformat(),
                        }
                        deadline_ts = deadline.timestamp()
                    else:
                        deadline_ts = pending_deadline.timestamp()

            if game is None and resume_state is None:
                next_due = self._rotation_datetime(rotation.get("next_due_at"))
                if next_due is None or now < next_due:
                    state["rotation"] = rotation
                    if changed:
                        self._write_state(state)
                    return CornerResult("noop", detail="rotation-not-due")
                fixed_guard = self._rotation_fixed_slot_guard(now)
                if fixed_guard is not None:
                    state["rotation"] = rotation
                    if changed:
                        self._write_state(state)
                    return CornerResult("noop", detail=f"rotation-deferred:{fixed_guard}")
                game = self._rotation_pick(rotation, now)
                if game is None:
                    rotation["last_result"] = {
                        "result": "cancelled",
                        "reason": "no-game-past-cooldown",
                        "at": now.isoformat(),
                    }
                    state["rotation"] = rotation
                    self._write_state(state)
                    return CornerResult("noop", detail="rotation-no-eligible-game")
                busy = self._other_corner_busy()
                if busy is not None:
                    rotation["pending"] = {
                        "game": game,
                        "selected_at": now.isoformat(),
                        "queued": True,
                    }
                    rotation["last_result"] = {
                        "result": "queued",
                        "game": game,
                        "reason": busy,
                        "at": now.isoformat(),
                    }
                    state["rotation"] = rotation
                    self._write_state(state)
                    return CornerResult("queued", game=game, detail=f"rotation-queued:{busy}")
                deadline = now + dt.timedelta(minutes=cfg.rotation_wait_minutes)
                rotation["pending"] = {
                    "game": game,
                    "selected_at": now.isoformat(),
                    "deadline_at": deadline.isoformat(),
                }
                rotation["last_result"] = {
                    "result": "selected",
                    "game": game,
                    "at": now.isoformat(),
                }
                deadline_ts = deadline.timestamp()

            state["rotation"] = rotation
            self._write_state(state)

        if resume_state is not None:
            return self._wait_and_finish(resume_state)

        assert game is not None
        requested_at = now.timestamp()
        try:
            if cfg.require_program_boundary:
                with program_slot(
                    self.g,
                    self.state_path,
                    requested_at=requested_at,
                    wait_deadline_ts=deadline_ts if deadline_ts is not None else requested_at,
                    wait_boundary=True,
                    sleep=self._sleep,
                    now=lambda: self._local_now().timestamp(),
                ):
                    return self._rotation_run(game)
            return self._rotation_run(game)
        except CornerWaitExpired:
            self._rotation_cancel_pending(game, "wait-expired")
            return CornerResult("queued", game=game, detail="rotation-queued:wait-expired")

    def _other_corner_busy(self, *, include_game_switch: bool = False) -> str | None:
        """Return only another corner's program-slot ownership.

        A game-switch drain is handled by GameSwitchCoordinator's durable
        request queue; treating it as a lottery cancellation here would lose
        the selected slot before the queue can consume it.
        """
        from .corner_boundary import other_corner_busy

        if include_game_switch:
            try:
                self._active_game_reader()
            except RetroCornerError:
                return "game-switch-in-progress"
        return other_corner_busy(self.g, self.state_path, now=self._local_now().timestamp())

    def _lottery_starting_is_stale(self, state: dict[str, object], now: dt.datetime) -> bool:
        try:
            started = dt.datetime.fromisoformat(str(state.get("started_at")))
        except (TypeError, ValueError):
            return True
        if started.tzinfo is None:
            started = started.replace(tzinfo=self.tz)
        return now - started > dt.timedelta(minutes=STARTING_STALE_MINUTES)

    def _record_lottery(self, record: dict[str, object]) -> None:
        with self._locked():
            state = self._read_state()
            state["lottery"] = record
            self._write_state(state)

    def _lottery_tick(self) -> CornerResult:
        """毎時1回、確率で発火を抽選する。

        当たれば遊べるゲームを無作為に選び、target_matches 試合 (上限 duration_minutes) 遊んで
        元のゲームへ戻る。他コーナーが進行中/待機中なら抽選そのものを取りやめる (待たない)。
        抽選結果は発火前に state へ記録するので、tick の再起動・重複で同じ時に引き直さない。
        抽選は毎正時に起動する固定枠のコーナーが先に枠を取れるよう lottery_minute 分に行う。
        """
        from .corner_boundary import CornerWaitExpired, program_slot

        cfg = self.config
        now = self._local_now()
        if not cfg.enabled:
            return CornerResult("noop", detail="disabled")
        slot = now.strftime("%Y-%m-%dT%H")
        with self._locked():
            self._reconcile_stale_locked(now)
            state = self._read_state()
            status = state.get("status")
            if status == "active":
                return CornerResult("noop", detail="already-active")
            if status == "starting":
                if not self._lottery_starting_is_stale(state, now):
                    return CornerResult("noop", detail="already-starting")
                state.update(status="interrupted", completed_at=now.isoformat())
            drawn = state.get("lottery")
            if isinstance(drawn, dict) and drawn.get("slot") == slot:
                return CornerResult("noop", detail="already-drawn")
            if not cfg.lottery_minute <= now.minute < cfg.lottery_minute + LOTTERY_DRAW_WINDOW_MINUTES:
                return CornerResult("noop", detail="outside-window")

            record: dict[str, object] = {
                "slot": slot,
                "at": now.isoformat(),
                "probability": cfg.lottery_probability,
            }
            game: str | None = None
            busy = self._other_corner_busy(include_game_switch=True)
            if busy is not None:
                record.update(result="cancelled", reason=busy)
            else:
                roll = self._rng.random()
                record["roll"] = round(roll, 6)
                if roll >= cfg.lottery_probability:
                    record.update(result="miss")
                else:
                    game = self._lottery_pick(state)
                    if game is None:
                        record.update(result="cancelled", reason="no-playable-game")
                    else:
                        record.update(result="fire", game=game)
            state["lottery"] = record
            self._write_state(state)
        if game is None:
            reason = record.get("reason")
            return CornerResult(
                "noop", detail=f"lottery-{record['result']}" + (f":{reason}" if reason else "")
            )

        deadline = now.timestamp() + cfg.lottery_wait_minutes * 60
        try:
            if cfg.require_program_boundary:
                with program_slot(
                    self.g,
                    self.state_path,
                    requested_at=now.timestamp(),
                    wait_deadline_ts=deadline,
                    wait_boundary=True,
                    sleep=self._sleep,
                    now=lambda: self._local_now().timestamp(),
                ):
                    return self._lottery_run(game, record)
            return self._lottery_run(game, record)
        except CornerWaitExpired:
            # 境界/枠の待ちが上限を超えた = 他が塞いでいる。実行せず取りやめる。
            self._record_lottery({**record, "result": "cancelled", "reason": "wait-expired"})
            return CornerResult("noop", detail="lottery-cancelled:wait-expired")

    def _lottery_run(self, game: str, record: dict[str, object]) -> CornerResult:
        with self._locked():
            state, result = self._begin_locked(
                self._local_now(), scheduled=True, target_override=game, extra_state={"lottery": record}
            )
        if result is not None:
            return result
        assert state is not None
        return self._wait_and_finish(state)

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
    sub.add_parser(
        "recover-failed",
        help="canonical failed後のレトロ枠を安全に復旧し、同じゲームを再試行する",
    )
    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")
    once = sub.add_parser("improve-once")
    once.add_argument("--date", required=True, help="対象コーナー日 (YYYY-MM-DD)")
    once.add_argument("--game", default=None, help="終了したコーナーのゲーム (既定は日付から選択)")
    once.add_argument("--started-at", type=float, default=None,
                      help="確定済みコーナー開始 epoch秒 (queue dispatch用)")
    once.add_argument("--ends-at", type=float, default=None,
                      help="確定済みコーナー終了予定 epoch秒")
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
            started_at = getattr(args, "started_at", None)
            ends_at = getattr(args, "ends_at", None)
            if (started_at is None) != (ends_at is None):
                raise RetroCornerError("--started-at と --ends-at は同時に指定してください")
            window = (started_at, ends_at) if started_at is not None else None
            try:
                summary = manager.improve_once(
                    args.date, agents=args.agents, matches=args.matches,
                    margin_pct=args.margin_pct, dry_run=args.dry_run,
                    game=getattr(args, "game", None),
                    window=window,
                )
            except CornerImproveError as exc:
                print(f"docich: エラー: {exc}", file=sys.stderr)
                return 2
            print(json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
            return 0
        method_name = {
            "recover-failed": "recover_failed",
        }.get(args.command, args.command)
        result = getattr(manager, method_name)()
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

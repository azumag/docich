"""RetroArch adapters.

Legacy :class:`RetroArchAdapter` keeps the fixed ``run/retroarch/retroarch.cfg``
and the fixed network command port for the current CLI lifecycle.
:class:`RetroArchCoordinatorAdapter` is the P2 runtime-aware adapter: it writes
a generation-specific cfg inside the runtime directory, binds a
generation-derived network command port, runs the emulator in the runtime's
game window and confirms readiness on that runtime's own port (design v2 §4.2).
"""
from __future__ import annotations

import glob
import sys
import time
from pathlib import Path

from .. import procs
from ..actions import Action
from ..game_switch import DeadlineExceededError, ReadinessTimeoutError, RuntimeSpec
from ..netcmd import send_ra_cmd
from ..tmux import SESSION, Tmux, TmuxOwnership
from .base import Adapter, AdapterError, Observation

WINDOW_PATTERN = "RetroArch"
NETWORK_CMD_PORT = 55355
NETWORK_PORT_RANGE = 1000
RA_READY_POLL_S = 0.5

# core = "auto" の探索順 (architecture.md §4.1)。
CORE_CANDIDATES = ("snes9x", "bsnes_mercury_performance", "bsnes_mercury_balanced")

# 意味ボタン -> (RetroArch cfg 値, xdotool キー名)。既定マップ (architecture.md §3.3)。
DEFAULT_BUTTONS: dict[str, tuple[str, str]] = {
    "a": ("x", "x"),
    "b": ("z", "z"),
    "x": ("s", "s"),
    "y": ("a", "a"),
    "l": ("q", "q"),
    "r": ("w", "w"),
    "start": ("enter", "Return"),
    "select": ("rshift", "Shift_R"),
    "up": ("up", "Up"),
    "down": ("down", "Down"),
    "left": ("left", "Left"),
    "right": ("right", "Right"),
}

# pad_map で上書きされた RetroArch cfg トークンを xdotool キー名へ変換する表。
_TOKEN_TO_XDOTOOL = {
    "enter": "Return",
    "rshift": "Shift_R",
    "lshift": "Shift_L",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "space": "space",
}


def _token_to_xdotool(token: str) -> str:
    if token in _TOKEN_TO_XDOTOOL:
        return _TOKEN_TO_XDOTOOL[token]
    if len(token) == 1 and token.isalnum():
        return token
    raise AdapterError(
        f"pad_map の値 '{token}' を xdotool キー名に変換できません "
        f"(対応: {sorted(_TOKEN_TO_XDOTOOL)} または英数字 1 文字)"
    )


# --- shared [retroarch] helpers -------------------------------------------


def retroarch_raw(game) -> dict:
    raw = game.raw.get("retroarch", {})
    if not isinstance(raw, dict):
        raise AdapterError("[retroarch] はテーブルである必要があります")
    return raw


def resolved_buttons(game) -> dict[str, tuple[str, str]]:
    pad_map = retroarch_raw(game).get("pad_map", {}) or {}
    if not isinstance(pad_map, dict):
        raise AdapterError("[retroarch.pad_map] はテーブルである必要があります")
    unknown = [k for k in pad_map if k not in DEFAULT_BUTTONS]
    if unknown:
        raise AdapterError(
            f"pad_map に不正なボタン名があります: {unknown} (使用可能: {sorted(DEFAULT_BUTTONS)})"
        )
    result: dict[str, tuple[str, str]] = {}
    for button, (cfg_val, xdotool_key) in DEFAULT_BUTTONS.items():
        if button in pad_map:
            token = pad_map[button]
            if not isinstance(token, str) or not token:
                raise AdapterError(f"pad_map.{button} は空でない文字列である必要があります")
            cfg_val = token
            xdotool_key = _token_to_xdotool(token)
        result[button] = (cfg_val, xdotool_key)
    return result


def resolve_rom(g, game) -> Path:
    rom = retroarch_raw(game).get("rom")
    if not rom:
        raise AdapterError(f"[retroarch] に rom が設定されていません ({game.path})")
    p = Path(rom)
    if not p.is_absolute():
        p = g.repo_root / p
    if not p.is_file():
        raise AdapterError(
            f"自己吸い出しした ROM を {p} に配置してください (games/roms/README.md 参照)"
        )
    return p


def resolve_core(game) -> str:
    core = retroarch_raw(game).get("core", "auto") or "auto"
    if core != "auto":
        p = Path(core)
        if not p.is_file():
            raise AdapterError(f"指定された core が見つかりません: {p}")
        return str(p)
    for candidate in CORE_CANDIDATES:
        matches = sorted(glob.glob(f"/usr/lib/*/libretro/{candidate}_libretro.so"))
        if matches:
            return matches[0]
    raise AdapterError(
        "RetroArch のコアが見つかりません。`sudo apt install libretro-snes9x` 等でコアを"
        "インストールするか、core に .so の絶対パスを指定してください"
    )


def retroarch_cfg_lines(g, game, cfg_path: Path, network_port: int) -> list[str]:
    buttons = resolved_buttons(game)
    d = g.display
    audio_enable = "true" if g.audio.enabled else "false"
    rdir = cfg_path.parent

    lines = [
        'input_driver = "sdl2"',
        'video_driver = "sdl2"',
        'audio_driver = "pulse"',
        f'audio_enable = "{audio_enable}"',
        'video_fullscreen = "true"',
        f'video_fullscreen_x = "{d.width}"',
        f'video_fullscreen_y = "{d.height}"',
        'pause_nonactive = "false"',
        'config_save_on_exit = "false"',
        'menu_driver = "rgui"',
        'gamemode_enable = "false"',
        'network_cmd_enable = "true"',
        f'network_cmd_port = "{network_port}"',
        f'savestate_directory = "{rdir / "states"}"',
        f'savefile_directory = "{rdir / "saves"}"',
        f'system_directory = "{rdir / "system"}"',
        # 誤爆でエミュレータが終了するのを防ぐ (停止は SIGTERM 経由。architecture.md §4.1)
        'input_exit_emulator = "nul"',
    ]
    for button, (cfg_val, _xdotool_key) in buttons.items():
        lines.append(f'input_player1_{button} = "{cfg_val}"')
    return lines


def retroarch_command(g, game, cfg_path: Path) -> list[str]:
    rom = resolve_rom(g, game)
    core = resolve_core(game)
    # dbus-run-session が無いと GameMode 統合の dbus 呼び出しで abort する (architecture.md §9-1b)。
    return [
        "dbus-run-session", "--", "retroarch",
        "--config", str(cfg_path),
        "-L", str(core),
        str(rom),
    ]


def retroarch_network_port(generation: int) -> int:
    """Generation-derived network command port (replace mode: no concurrent
    runtimes, but the deterministic offset avoids stale-runtime collisions)."""
    return NETWORK_CMD_PORT + (generation % NETWORK_PORT_RANGE)


def _docich_bin() -> str:
    return str(Path(__file__).resolve().parents[3] / "bin" / "docich")


class RetroArchAdapter(Adapter):
    name = "retroarch"

    def __init__(self, ctx):
        super().__init__(ctx)
        self._window_id: str | None = None
        self._rom_path: Path | None = None
        self._core_path: str | None = None

    # --- rom / core resolution ----------------------------------------

    def _resolve_rom(self) -> Path:
        return resolve_rom(self.ctx.g, self.ctx.game)

    def _resolve_core(self) -> str:
        return resolve_core(self.ctx.game)

    def _resolved_buttons(self) -> dict[str, tuple[str, str]]:
        return resolved_buttons(self.ctx.game)

    def _cfg_path(self) -> Path:
        return self.ctx.state.retroarch_dir / "retroarch.cfg"

    # --- Adapter contract ------------------------------------------------

    def prepare(self) -> None:
        self._rom_path = self._resolve_rom()
        self._core_path = self._resolve_core()

        cfg_path = self._cfg_path()
        rdir = cfg_path.parent
        states_dir = rdir / "states"
        saves_dir = rdir / "saves"
        system_dir = rdir / "system"
        for d in (rdir, states_dir, saves_dir, system_dir):
            d.mkdir(parents=True, exist_ok=True)

        lines = retroarch_cfg_lines(self.ctx.g, self.ctx.game, cfg_path, NETWORK_CMD_PORT)
        cfg_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def command(self) -> list[str]:
        rom = self._rom_path or self._resolve_rom()
        core = self._core_path or self._resolve_core()
        # dbus-run-session が無いと GameMode 統合の dbus 呼び出しで abort する (architecture.md §9-1b)。
        return [
            "dbus-run-session", "--", "retroarch",
            "--config", str(self._cfg_path()),
            "-L", str(core),
            str(rom),
        ]

    def observe(self) -> Observation:
        d = self.ctx.g.display
        out_path = self.ctx.state.screenshots_dir / "latest.png"
        result = self.ctx.xkit.screenshot(out_path, d.width, d.height)
        return Observation(
            game=self.ctx.game.name,
            title=self.ctx.game.title,
            adapter=self.name,
            ts=time.time(),
            kind="screenshot",
            screenshot=str(result),
        )

    def _ensure_focus(self) -> None:
        if self._window_id is None:
            self._window_id = self.ctx.xkit.find_window(WINDOW_PATTERN)
        if self._window_id is None:
            print(
                f"docich: 警告: {WINDOW_PATTERN} ウィンドウが見つかりません (フォーカスをスキップします)",
                file=sys.stderr,
            )
            return
        self.ctx.xkit.focus(self._window_id)

    def act(self, action: Action) -> None:
        if action.type == "pad":
            buttons = self._resolved_buttons()
            keys = []
            for button_name in action.buttons:
                if button_name not in buttons:
                    raise AdapterError(f"未知の pad ボタンです: {button_name}")
                keys.append(buttons[button_name][1])
            self._ensure_focus()
            self.ctx.xkit.tap(keys, action.hold_ms)
            return
        if action.type == "key":
            self._ensure_focus()
            self.ctx.xkit.tap(action.keys, action.hold_ms)
            return
        if action.type == "wait":
            return
        raise AdapterError(f"retroarch アダプタは action type '{action.type}' に対応していません")


class RetroArchCoordinatorAdapter:
    """P2 runtime-aware RetroArch adapter (design v2 §4.2).

    One instance is bound to exactly one runtime.  It writes the cfg inside
    the runtime directory, binds a generation-derived network command port
    (no fixed-port collisions), runs the emulator in the runtime's game
    window with ownership tags, and confirms readiness on the runtime's own
    port instead of a generic window/screenshot check.
    """

    name = "retroarch"

    def __init__(self, g, game, spec: RuntimeSpec):
        self.g = g
        self.game = game
        self.spec = spec
        self.tmux = Tmux()
        self.agent_enabled = game.agent.enabled

    def _ownership(self, role: str) -> TmuxOwnership:
        return TmuxOwnership(
            runtime_id=self.spec.runtime_id,
            generation=self.spec.generation,
            role=role,
        )

    def _check_active(self, deadline: float, cancel) -> None:
        if cancel is not None and cancel.is_set():
            raise DeadlineExceededError("adapter call はcancelされました")
        if time.monotonic() >= deadline:
            raise DeadlineExceededError("adapter call のdeadlineを超過しました")

    def _game_window_target(self) -> str:
        return f"{SESSION}:{self.spec.game_window}"

    def _agent_window_target(self) -> str:
        return f"{SESSION}:{self.spec.agent_window}"

    def _cfg_path(self) -> Path:
        return self.spec.runtime_dir / "retroarch.cfg"

    def _network_port(self) -> int:
        return retroarch_network_port(self.spec.generation)

    def _verify_window_ownership(self, target: str, role: str) -> None:
        expected = self._ownership(role)
        actual = self.tmux.read_window_ownership(target)
        if actual != expected:
            raise AdapterError(
                f"window ownershipが一致しません (expected={expected}, actual={actual})"
            )

    def _agent_command(self) -> list[str]:
        return [
            _docich_bin(), "--config", str(self.g.config_path),
            "run", "agent", self.spec.game,
        ]

    # --- CoordinatorAdapter contract -------------------------------------

    def preflight(self, deadline: float, cancel) -> None:
        self._check_active(deadline, cancel)
        resolve_rom(self.g, self.game)
        resolve_core(self.game)
        for binary in ("dbus-run-session", "retroarch"):
            if procs.which(binary) is None:
                raise AdapterError(f"コマンドが見つかりません: {binary}")
        if self.agent_enabled and not Path(_docich_bin()).is_file():
            raise AdapterError(f"docich executable が見つかりません: {_docich_bin()}")
        self._check_active(deadline, cancel)

    def materialize_runtime(self, deadline: float, cancel) -> None:
        self._check_active(deadline, cancel)
        self.spec.runtime_dir.mkdir(parents=True, exist_ok=True)
        for d in ("states", "saves", "system"):
            (self.spec.runtime_dir / d).mkdir(parents=True, exist_ok=True)
        cfg_path = self._cfg_path()
        lines = retroarch_cfg_lines(self.g, self.game, cfg_path, self._network_port())
        cfg_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self._check_active(deadline, cancel)

        target = self._game_window_target()
        if self.tmux.window_target_exists(target):
            self._verify_window_ownership(target, "game")
            return
        self._check_active(deadline, cancel)
        self.tmux.create_window_owned(
            self.spec.game_window,
            retroarch_command(self.g, self.game, cfg_path),
            self._ownership("game"),
        )
        self._check_active(deadline, cancel)

    def readiness(self, deadline: float, cancel) -> None:
        self._check_active(deadline, cancel)
        target = self._game_window_target()
        if not self.tmux.window_target_exists(target):
            raise ReadinessTimeoutError("game windowがありません")
        self._verify_window_ownership(target, "game")
        states = self.tmux.pane_states_checked(target)
        if any(pane.dead for pane in states):
            raise ReadinessTimeoutError("game paneがdeadです")
        # runtime 固有 network command port で status を確認する
        # (generic RetroArch window や screenshot の存在だけでは ready にしない)。
        self._probe_network_status(deadline, cancel)

    def _probe_network_status(self, deadline: float, cancel) -> None:
        while True:
            if cancel is not None and cancel.is_set():
                raise ReadinessTimeoutError("adapter call はcancelされました")
            if time.monotonic() >= deadline:
                raise ReadinessTimeoutError("network command port からの応答がありません")
            reply = send_ra_cmd("GET_STATUS", port=self._network_port())
            if reply is not None:
                return
            time.sleep(RA_READY_POLL_S)

    def alive(self, deadline: float, cancel) -> bool:
        self._check_active(deadline, cancel)
        target = self._game_window_target()
        if not self.tmux.window_target_exists(target):
            return False
        self._verify_window_ownership(target, "game")
        states = self.tmux.pane_states_checked(target)
        return not any(pane.dead for pane in states)

    def cleanup_runtime(self, deadline: float, cancel) -> None:
        for name, role in (
            (self.spec.agent_window, "agent"),
            (self.spec.game_window, "game"),
        ):
            self._check_active(deadline, cancel)
            target = f"{SESSION}:{name}"
            if self.tmux.window_target_exists(target):
                self._check_active(deadline, cancel)
                self.tmux.kill_window_owned(target, self._ownership(role))

    def start_agent(self, deadline: float, cancel) -> None:
        self._check_active(deadline, cancel)
        target = self._agent_window_target()
        if self.tmux.window_target_exists(target):
            self._verify_window_ownership(target, "agent")
            return
        self._check_active(deadline, cancel)
        self.tmux.create_window_owned(
            self.spec.agent_window,
            self._agent_command(),
            self._ownership("agent"),
        )
        self._check_active(deadline, cancel)

    def stop_agent(self, deadline: float, cancel) -> None:
        self._check_active(deadline, cancel)
        target = self._agent_window_target()
        if self.tmux.window_target_exists(target):
            self._check_active(deadline, cancel)
            self.tmux.kill_window_owned(target, self._ownership("agent"))

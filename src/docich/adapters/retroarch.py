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
import os
import re
import sys
import time
from contextlib import nullcontext
from pathlib import Path

from .. import procs
from ..actions import Action
from ..game_switch import (DeadlineExceededError, ReadinessTimeoutError, RuntimeSpec,
                           RoundBoundaryUnsupportedError, atomic_write_json)
from ..netcmd import send_ra_cmd
from ..retroarch_boundary import (BOUNDARY_FILE, checkpoint_digest, identity,
                                 input_gate, matches, read_record, require_input_open)
from ..naming import runtime_directory
from ..xkit import XKit
from ..tmux import SESSION, Tmux, TmuxOwnership
from .base import Adapter, AdapterError, Observation

WINDOW_PATTERN = "RetroArch"
NETWORK_CMD_PORT = 55355
NETWORK_PORT_RANGE = 1000
RA_READY_POLL_S = 0.5
RA_READY_IO_TIMEOUT_S = 0.1

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


def retroarch_audio(g, game) -> tuple[bool, str]:
    """A game may use the existing broadcast bus without owning that bus."""
    raw = retroarch_raw(game)
    enabled = raw.get("audio_enabled", g.audio.enabled)
    sink = raw.get("audio_sink", g.audio.sink_name)
    if type(enabled) is not bool:
        raise AdapterError("retroarch.audio_enabled must be a boolean")
    if not isinstance(sink, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", sink):
        raise AdapterError("retroarch.audio_sink must be a PulseAudio sink name")
    return enabled, sink


def retroarch_cfg_lines(g, game, cfg_path: Path, network_port: int) -> list[str]:
    buttons = resolved_buttons(game)
    d = g.display
    audio_enabled, audio_sink = retroarch_audio(g, game)
    audio_enable = "true" if audio_enabled else "false"
    latency = retroarch_raw(game).get("audio_latency_ms")
    if latency is not None and (type(latency) is not int or not 8 <= latency <= 512):
        raise AdapterError("retroarch.audio_latency_ms must be an integer from 8 to 512")
    rdir = cfg_path.parent

    lines = [
        'input_driver = "sdl2"',
        'video_driver = "sdl2"',
        'audio_driver = "pulse"',
        f'audio_enable = "{audio_enable}"',
        *([f'audio_device = "{audio_sink}"'] if audio_enabled else []),
        *([f'audio_latency = "{latency}"'] if audio_enabled and latency is not None else []),
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
    if d.viewport_width > 0:
        # Keep the core's ordinary window scale/aspect, independently of the
        # broadcast rectangle. presentation.py scales only its captured image.
        lines = [line for line in lines if not line.startswith('video_fullscreen')]
        lines += ['video_fullscreen = "false"', 'video_scale = "3.0"',
                  'video_force_aspect = "true"', 'video_crop_overscan = "false"',
                  'savestate_auto_index = "false"', 'state_slot = "0"']
    from ..hanjuku_run import enabled as scripted_hanjuku
    if scripted_hanjuku(game):
        # Stable native pixels for the deterministic screen signatures.
        lines.append('video_smooth = "false"')
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

    def _runtime_dir(self) -> Path | None:
        fence = self.ctx.fence
        if fence is not None:
            return runtime_directory(self.ctx.g.state_dir, fence.runtime_id)
        if self.ctx.g.display.viewport_width > 0:
            raise AdapterError("contained RetroArch I/O requires a runtime fence")
        return None

    def _source(self):
        runtime_dir = self._runtime_dir()
        if self.ctx.g.display.viewport_width <= 0:
            return self.ctx.xkit, None
        state = read_record(runtime_dir / "presentation.json")
        if (state.get("status") not in {"ready", "presentation_failed"} or not isinstance(state.get("display"), str)
                or not state["display"].startswith(":")
                or not state["display"][1:].isdigit()
                or state["display"] == self.ctx.g.display.name
                or not str(state.get("window", "")).isdigit()
                or any(type(state.get(k)) is not int or state[k] <= 0
                       for k in ("width", "height"))):
            raise AdapterError("RetroArch native presentation is not ready")
        return XKit(state["display"]), state

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
        self._check_fence()
        d = self.ctx.g.display
        out_path = self.ctx.state.screenshots_dir / "latest.png"
        from .. import hanjuku_run
        runtime_dir = self._runtime_dir()
        scripted = hanjuku_run.enabled(self.ctx.game) and runtime_dir is not None
        if scripted:
            out_path = runtime_dir / 'screenshots' / 'latest.png'
        meta = {}
        # Both the agent and the corner monitor observe. Serialize capture,
        # decode and terminal evidence with actual pad input for this runtime.
        with (input_gate(runtime_dir, time.monotonic() + 15) if scripted else nullcontext()):
            source, native = self._source()
            if native:
                result = source.screenshot(out_path, native["width"], native["height"],
                                           window_id=native["window"])
            else:
                result = source.screenshot(out_path, d.width, d.height)
            if scripted:
                from ..hanjuku_pixels import read_png
                frame = read_png(Path(result)).resized()
                reply = send_ra_cmd('GET_STATUS', port=retroarch_network_port(self.ctx.fence.generation), wait_reply_s=.1)
                paused = bool(reply and reply.startswith('GET_STATUS PAUSED '))
                state = hanjuku_run.observe(runtime_dir, hanjuku_run.runtime_identity(self.ctx.fence),
                                           frame, playing=not paused)
                meta = {'runtime_dir': str(runtime_dir),
                        'terminal_reason': state.get('terminal_reason'),
                        'terminal_candidate': state.get('terminal_candidate', False),
                        'hanjuku': state}
        return Observation(
            game=self.ctx.game.name,
            title=self.ctx.game.title,
            adapter=self.name,
            ts=time.time(),
            kind="screenshot",
            screenshot=str(result),
            meta=meta,
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
        self._check_fence()
        runtime_dir = self._runtime_dir()
        with (input_gate(runtime_dir, time.monotonic() + 5) if runtime_dir else nullcontext()):
            if runtime_dir:
                require_input_open(runtime_dir)
                from .. import hanjuku_run
                if hanjuku_run.enabled(self.ctx.game):
                    run = hanjuku_run.load(runtime_dir, hanjuku_run.runtime_identity(self.ctx.fence))
                    if run.get('terminal_reason') or run.get('terminal_candidate'):
                        raise AdapterError('Hanjuku terminal evidence holds input')
            source, native = self._source()
            if native:
                # Never focus or inject into the broadcast presenter.
                self.ctx.xkit = source
                self._window_id = native["window"]
            self._act(action)
            if runtime_dir and hanjuku_run.enabled(self.ctx.game):
                hanjuku_run.action_sent(runtime_dir, hanjuku_run.runtime_identity(self.ctx.fence), action)

    def _act(self, action: Action) -> None:
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
        self.requires_round_boundary = game.lifecycle.require_round_boundary
        # stop historically bypasses draining; opt in without changing the
        # lifecycle of unrelated adapters in this RetroArch implementation.
        self.requires_stop_boundary = game.lifecycle.require_round_boundary
        self.round_boundary_timeout_s = game.lifecycle.boundary_timeout_s
        if not self.requires_round_boundary:
            # game_switch treats the callable boundary method itself as a
            # capability. Hide it for legacy RetroArch games so the lifecycle
            # policy remains a real opt-in, matching the CLI adapter.
            self.request_round_boundary = None
            self.cancel_round_boundary = None

    def _contained(self) -> bool:
        return self.g.display.viewport_width > 0

    def _presentation_path(self) -> Path:
        return self.spec.runtime_dir / "presentation.json"

    def _game_command(self) -> list[str]:
        command = retroarch_command(self.g, self.game, self._cfg_path())
        if not self._contained():
            return command
        d = self.g.display
        audio_enabled, audio_sink = retroarch_audio(self.g, self.game)
        return [sys.executable, str(Path(__file__).resolve().parents[1] / "presentation.py"),
                '--display', d.name, '--title', f'docich-present-{self.spec.runtime_id}',
                '--x', str(d.viewport_x), '--y', str(d.viewport_y),
                '--width', str(d.viewport_width), '--height', str(d.viewport_height),
                '--window-pattern', '^RetroArch',
                '--runtime-state', str(self._presentation_path()),
                *(['--audio-sink', audio_sink] if audio_enabled else []),
                '--', *command]

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
        # Agent は世代別 window 内で run ループとして起動し、runtime identity
        # を束縛する (P3 lease fence)。lease 未発行の rare なspec では束縛なし。
        cmd = [
            _docich_bin(), "--config", str(self.g.config_path),
            "run", "agent", self.spec.game,
        ]
        if (
            self.spec.runtime_id is not None
            and self.spec.generation is not None
            and self.spec.lease_id is not None
        ):
            cmd += [
                "--runtime-id", str(self.spec.runtime_id),
                "--generation", str(self.spec.generation),
                "--lease-id", str(self.spec.lease_id),
            ]
        return cmd

    # --- CoordinatorAdapter contract -------------------------------------

    def preflight(self, deadline: float, cancel) -> None:
        self._check_active(deadline, cancel)
        if self.game.lifecycle.require_round_boundary and not self._contained():
            raise AdapterError("safe RetroArch requires a private contained presentation")
        resolve_rom(self.g, self.game)
        resolve_core(self.game)
        for binary in ("dbus-run-session", "retroarch", *(("Xvfb", "ffplay", "xdotool")
                                                        if self._contained() else ())):
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
        if self._contained():
            if read_record(self.spec.runtime_dir / BOUNDARY_FILE).get("status") == "reached":
                raise AdapterError("saved RetroArch runtime needs explicit checkpoint restoration")
            prior = read_record(self._presentation_path())
            if prior and prior.get("status") != "stopped":
                raise AdapterError("previous RetroArch child cleanup is unconfirmed")
            atomic_write_json(self._presentation_path(), {"status": "starting"})
        self.tmux.create_window_owned(
            self.spec.game_window,
            self._game_command(),
            self._ownership("game"),
            env={"DISPLAY": self.g.display.name},
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
        if self._contained():
            presenter = XKit(self.g.display.name)
            while True:
                self._check_active(deadline, cancel)
                if not self.alive(deadline, cancel):
                    raise ReadinessTimeoutError("RetroArch presenter exited")
                state = read_record(self._presentation_path())
                if state.get("status") == "ready" and presenter.find_window(
                    f"^docich-present-{self.spec.runtime_id}$", timeout=0.1
                ):
                    return
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))

    def _probe_network_status(self, deadline: float, cancel) -> None:
        while True:
            if cancel is not None and cancel.is_set():
                raise ReadinessTimeoutError("adapter call はcancelされました")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReadinessTimeoutError("network command port からの応答がありません")
            reply = send_ra_cmd(
                "GET_STATUS",
                port=self._network_port(),
                wait_reply_s=min(remaining, RA_READY_IO_TIMEOUT_S),
            )
            if reply is not None:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReadinessTimeoutError("network command port からの応答がありません")
            wait_s = min(remaining, RA_READY_POLL_S)
            if cancel is not None:
                if cancel.wait(wait_s):
                    raise ReadinessTimeoutError("adapter call はcancelされました")
            else:
                time.sleep(wait_s)

    def alive(self, deadline: float, cancel) -> bool:
        self._check_active(deadline, cancel)
        target = self._game_window_target()
        if not self.tmux.window_target_exists(target):
            if self._contained():
                record = read_record(self._presentation_path())
                if (record or self._cfg_path().exists()) and record.get("status") != "stopped":
                    raise AdapterError("RetroArch child shutdown is not confirmed")
            return False
        self._verify_window_ownership(target, "game")
        states = self.tmux.pane_states_checked(target)
        if self._contained() and any(pane.dead for pane in states):
            if read_record(self._presentation_path()).get("status") != "stopped":
                raise AdapterError("RetroArch child shutdown is not confirmed")
        return not any(pane.dead for pane in states)

    def _require_safe_cleanup(self, deadline: float, cancel) -> None:
        if self._contained():
            from ..agent.fence import read_canonical
            state = read_canonical(self.g.state_dir)
            committed = any(isinstance(state.get(slot), dict) and
                            state[slot].get("runtime_id") == self.spec.runtime_id
                            for slot in ("active", "previous"))
            if committed:
                record = read_record(self.spec.runtime_dir / BOUNDARY_FILE)
                if not matches(record, self.spec, record.get("request_id")) or record.get("status") != "reached":
                    raise AdapterError("RetroArch committed runtime has no safe boundary")
                from .. import hanjuku_run
                if hanjuku_run.enabled(self.game):
                    terminal = hanjuku_run.terminal(self.spec.runtime_dir, hanjuku_run.runtime_identity(self.spec))
                    if (terminal and record.get('outcome') == terminal['terminal_reason']
                            and record.get('frame_sha256') == terminal['frame_sha256']):
                        return
                    raise AdapterError('Hanjuku terminal evidence is missing')
                if self.tmux.window_target_exists(self._game_window_target()):
                    states = self.tmux.pane_states_checked(self._game_window_target())
                    if not any(pane.dead for pane in states):
                        self._require_paused(deadline, cancel)
                        if checkpoint_digest(self._checkpoint(record["checkpoint"]), deadline, cancel) != record.get("sha256"):
                            raise AdapterError("RetroArch checkpoint changed before cleanup")

    def cleanup_runtime(self, deadline: float, cancel) -> None:
        self._require_safe_cleanup(deadline, cancel)
        for name, role in (
            (self.spec.agent_window, "agent"),
            (self.spec.game_window, "game"),
        ):
            self._check_active(deadline, cancel)
            target = f"{SESSION}:{name}"
            if self.tmux.window_target_exists(target):
                self._check_active(deadline, cancel)
                self.tmux.kill_window_owned(target, self._ownership(role))

    def request_round_boundary(self, request_id: str, deadline: float, cancel) -> None:
        if not self._contained():
            raise RoundBoundaryUnsupportedError("RetroArch safe boundary requires private presentation")
        from .. import hanjuku_run
        if hanjuku_run.enabled(self.game):
            return self._request_script_boundary(request_id, deadline, cancel)
        path = self.spec.runtime_dir / BOUNDARY_FILE
        with input_gate(self.spec.runtime_dir, deadline, cancel):
            self._check_active(deadline, cancel)
            record = read_record(path)
            if record and record.get("status") != "cancelled":
                if not matches(record, self.spec, request_id):
                    raise AdapterError("RetroArch boundary identity mismatch")
            else:
                atomic_write_json(path, dict(identity(self.spec, request_id),
                                             status="waiting", requested_ns=time.time_ns()))
        while True:
            self._check_active(deadline, cancel)
            if not self.alive(deadline, cancel):
                raise AdapterError("RetroArch exited without a safe boundary")
            with input_gate(self.spec.runtime_dir, deadline, cancel):
                record = read_record(path)
                if not matches(record, self.spec, request_id):
                    raise AdapterError("RetroArch boundary identity changed")
                if record.get("status") == "reached":
                    checkpoint = self._checkpoint(record["checkpoint"])
                    if checkpoint_digest(checkpoint, deadline, cancel) != record.get("sha256"):
                        raise AdapterError("RetroArch checkpoint changed")
                    self._require_paused(deadline, cancel)
                    return
                if record.get("status") != "waiting":
                    raise AdapterError("RetroArch boundary needs explicit recovery")
            if cancel is not None:
                cancel.wait(min(0.05, max(0, deadline - time.monotonic())))
            else:
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))

    def _request_script_boundary(self, request_id, deadline, cancel):
        """Wait for game-over/stasis evidence while the bot continues playing."""
        from .. import hanjuku_run
        path = self.spec.runtime_dir / BOUNDARY_FILE
        while True:
            self._check_active(deadline, cancel)
            if not self.alive(deadline, cancel):
                raise AdapterError('Hanjuku exited before terminal evidence')
            with input_gate(self.spec.runtime_dir, deadline, cancel):
                record = read_record(path)
                if record and record.get('status') != 'cancelled' and not matches(record, self.spec, request_id):
                    raise AdapterError('Hanjuku boundary identity mismatch')
                terminal = hanjuku_run.terminal(self.spec.runtime_dir, hanjuku_run.runtime_identity(self.spec))
                if terminal:
                    atomic_write_json(path, dict(identity(self.spec, request_id), status='reached',
                        outcome=terminal['terminal_reason'], frame_sha256=terminal['frame_sha256']))
                    return
                if not record or record.get('status') == 'cancelled':
                    atomic_write_json(path, dict(identity(self.spec, request_id), status='waiting',
                                                requested_ns=time.time_ns()))
            if cancel is not None:
                cancel.wait(.1)
            else:
                time.sleep(.1)

    def _checkpoint(self, name: str) -> Path:
        if name != resolve_rom(self.g, self.game).stem + '.state':
            raise AdapterError("checkpoint must be a slot-zero .state basename")
        path = self.spec.runtime_dir / 'states' / name
        if path.parent.is_symlink():
            raise AdapterError("checkpoint directory may not be a symlink")
        return path

    def _require_paused(self, deadline: float, cancel) -> None:
        self._check_active(deadline, cancel)
        if not self._contained() or not self.alive(deadline, cancel):
            raise AdapterError("RetroArch native runtime is not alive")
        self._verify_window_ownership(self._game_window_target(), "game")
        reply = send_ra_cmd("GET_STATUS", port=self._network_port(),
                            wait_reply_s=min(0.1, max(0.001, deadline - time.monotonic())))
        self._check_active(deadline, cancel)
        # v1.18 GET_STATUS: PAUSED <core>,<content basename>[,<crc>].
        # A menu, missing response, PLAYING or generic OK is not a boundary.
        prefix = "GET_STATUS PAUSED "
        fields = reply[len(prefix):].strip().split(',') if reply and reply.startswith(prefix) else []
        if len(fields) < 2 or fields[1] != resolve_rom(self.g, self.game).stem:
            raise AdapterError("RetroArch paused content identity is unconfirmed")

    def confirm_safe_boundary(self, request_id: str, checkpoint: str, deadline: float, cancel) -> None:
        """Explicit operator acknowledgement of a completed paused checkpoint.

        Caller holds the canonical shared lock and verifies draining identity.
        No save, pause, quit, or automatic game-over inference is issued here.
        """
        path = self.spec.runtime_dir / BOUNDARY_FILE
        with input_gate(self.spec.runtime_dir, deadline, cancel):
            record = read_record(path)
            if not matches(record, self.spec, request_id) or record.get("status") != "waiting":
                raise AdapterError("RetroArch has no matching pending boundary")
            self._require_paused(deadline, cancel)
            saved = self._checkpoint(checkpoint)
            digest = checkpoint_digest(saved, deadline, cancel)
            if saved.stat().st_mtime_ns < record["requested_ns"]:
                raise AdapterError("checkpoint predates the boundary request")
            # The operator attests save completion. fsync plus a second digest
            # rejects a changing checkpoint; do not infer save success from UDP.
            with saved.open('rb') as stream:
                os.fsync(stream.fileno())
            self._require_paused(deadline, cancel)
            if checkpoint_digest(saved, deadline, cancel) != digest:
                raise AdapterError("checkpoint is still changing")
            self._check_active(deadline, cancel)
            atomic_write_json(path, dict(record, status="reached", checkpoint=checkpoint,
                                         sha256=digest, outcome="suspended"))

    def cancel_round_boundary(self, request_id: str, deadline: float, cancel) -> bool:
        # Only our waiting marker is reversible. Never unpause a user's game
        # or release a confirmed hold as a side effect of timeout/recovery.
        with input_gate(self.spec.runtime_dir, deadline, cancel):
            path = self.spec.runtime_dir / BOUNDARY_FILE
            record = read_record(path)
            if not record:
                return True
            if not matches(record, self.spec, request_id):
                return False
            if record.get("status") not in {"waiting", "cancelled"}:
                return False
            atomic_write_json(path, dict(record, status="cancelled"))
            return True

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
        self._require_safe_cleanup(deadline, cancel)
        target = self._agent_window_target()
        if self.tmux.window_target_exists(target):
            self._check_active(deadline, cancel)
            self.tmux.kill_window_owned(target, self._ownership("agent"))

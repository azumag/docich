"""CLI/TUI game adapters.

Legacy :class:`CliGameAdapter` keeps the fixed ``docich-game`` session for the
current CLI lifecycle.  :class:`CliCoordinatorAdapter` is the P2 runtime-aware
adapter: one generation-specific session/window set per runtime, tagged with
tmux ownership options (design v2 §3, §4).
"""
from __future__ import annotations

import shlex
import time
from pathlib import Path

from .. import procs
from ..actions import Action
from ..game_switch import ReadinessTimeoutError, RuntimeSpec
from ..tmux import SESSION, Tmux, TmuxOwnership
from .base import Adapter, AdapterError, Observation

GAME_SESSION = "docich-game"


# --- shared [cli] table helpers --------------------------------------------


def cli_raw(game) -> dict:
    raw = game.raw.get("cli", {})
    if not isinstance(raw, dict):
        raise AdapterError("[cli] はテーブルである必要があります")
    return raw


def cli_command_list(game) -> list[str]:
    command = cli_raw(game).get("command")
    if isinstance(command, str) and command.strip():
        return shlex.split(command)
    if isinstance(command, list) and command:
        return [str(c) for c in command]
    raise AdapterError("[cli] に command が設定されていません")


def cli_cols(game) -> int:
    return int(cli_raw(game).get("cols", 80))


def cli_rows(game) -> int:
    return int(cli_raw(game).get("rows", 24))


def cli_font(game) -> str:
    return str(cli_raw(game).get("font", "monospace"))


def cli_font_size(game) -> int:
    return int(cli_raw(game).get("font_size", 18))


def _docich_bin() -> str:
    # cli.py の _docich_bin() と同じ導出。ここで cli を import すると
    # (cli -> adapters -> cli の) 循環importになるため独立に計算する。
    return str(Path(__file__).resolve().parents[2] / "bin" / "docich")


class CliGameAdapter(Adapter):
    name = "cli"

    def _command_list(self) -> list[str]:
        return cli_command_list(self.ctx.game)

    def _cols(self) -> int:
        return cli_cols(self.ctx.game)

    def _rows(self) -> int:
        return cli_rows(self.ctx.game)

    def _font(self) -> str:
        return cli_font(self.ctx.game)

    def _font_size(self) -> int:
        return cli_font_size(self.ctx.game)

    # --- Adapter contract ------------------------------------------------

    def prepare(self) -> None:
        cmd = self._command_list()
        # tmux サーバーの PATH に /usr/games が無い場合に備えて絶対パス化する。
        resolved = procs.which(cmd[0])
        if not resolved:
            raise AdapterError(
                f"コマンドが見つかりません: {cmd[0]} (PATH を確認してください。/usr/games も探索します)"
            )
        resolved_cmd = [resolved, *cmd[1:]]

        if not self.ctx.tmux.has_session_named(GAME_SESSION):
            self.ctx.tmux.new_game_session(GAME_SESSION, resolved_cmd, self._cols(), self._rows())
            self.ctx.tmux.set_manual_size(GAME_SESSION, self._cols(), self._rows())

    def command(self) -> list[str]:
        # 映像化用の xterm。ゲーム本体は GAME_SESSION 内で走り続ける (read-only attach)。
        return [
            "xterm", "-fa", self._font(), "-fs", str(self._font_size()),
            "-bg", "black", "-fg", "grey90",
            "-geometry", f"{self._cols()}x{self._rows()}+0+0",
            "-T", f"docich-{self.ctx.game.name}",
            "-e", "tmux", "attach-session", "-r", "-t", GAME_SESSION,
        ]

    def observe(self) -> Observation:
        text = self.ctx.tmux.capture_pane(GAME_SESSION)
        meta = {}
        if text == "":
            meta["warning"] = "capture が空です (セッション停止の可能性)"
        return Observation(
            game=self.ctx.game.name,
            title=self.ctx.game.title,
            adapter=self.name,
            ts=time.time(),
            kind="text",
            text=text,
            meta=meta,
        )

    def act(self, action: Action) -> None:
        if action.type == "text":
            self.ctx.tmux.send_keys(GAME_SESSION, [action.text], literal=True)
            return
        if action.type == "special":
            self.ctx.tmux.send_keys(GAME_SESSION, [action.key], literal=False)
            return
        if action.type == "key":
            self.ctx.tmux.send_keys(GAME_SESSION, action.keys, literal=False)
            return
        if action.type == "wait":
            return
        raise AdapterError(f"cli アダプタは action type '{action.type}' に対応していません")

    def cleanup(self) -> None:
        self.ctx.tmux.kill_session_named(GAME_SESSION)


class CliCoordinatorAdapter:
    """P2 runtime-aware CLI adapter (design v2 §4.2).

    One instance is bound to exactly one runtime (``RuntimeSpec``).  It
    creates the generation-specific tmux session (``docich-game-gN``) and the
    game/agent windows (``game-gN`` / ``agent-gN``) with ownership tags, and
    only ever tears down those tagged objects.
    """

    name = "cli"

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

    def _game_window_target(self) -> str:
        return f"{SESSION}:{self.spec.game_window}"

    def _agent_window_target(self) -> str:
        return f"{SESSION}:{self.spec.agent_window}"

    def _game_command(self) -> list[str]:
        cmd = cli_command_list(self.game)
        resolved = procs.which(cmd[0])
        if not resolved:
            raise AdapterError(
                f"コマンドが見つかりません: {cmd[0]} (PATH を確認してください。/usr/games も探索します)"
            )
        return [resolved, *cmd[1:]]

    def _xterm_command(self) -> list[str]:
        return [
            "xterm", "-fa", cli_font(self.game), "-fs", str(cli_font_size(self.game)),
            "-bg", "black", "-fg", "grey90",
            "-geometry", f"{cli_cols(self.game)}x{cli_rows(self.game)}+0+0",
            "-T", f"docich-{self.game.name}",
            "-e", "tmux", "attach-session", "-r", "-t", self.spec.adapter_session,
        ]

    def _agent_command(self) -> list[str]:
        # Agent は世代別 window 内で run ループとして起動する。runtime identity
        # の束縛 (lease fence) は P3 で追加する。
        return [
            _docich_bin(), "--config", str(self.g.config_path),
            "run", "agent", self.spec.game,
        ]

    # --- CoordinatorAdapter contract -------------------------------------

    def preflight(self, deadline: float, cancel) -> None:
        self._game_command()  # コマンド解決のみ (副作用なし)。未解決は AdapterError

    def materialize_runtime(self, deadline: float, cancel) -> None:
        self.spec.runtime_dir.mkdir(parents=True, exist_ok=True)
        if not self.tmux.session_target_exists(self.spec.adapter_session):
            self.tmux.create_game_session_owned(
                self.spec.adapter_session,
                self._game_command(),
                cli_cols(self.game),
                cli_rows(self.game),
                self._ownership("adapter"),
            )
        if not self.tmux.window_target_exists(self._game_window_target()):
            self.tmux.create_window_owned(
                self.spec.game_window, self._xterm_command(), self._ownership("game")
            )

    def readiness(self, deadline: float, cancel) -> None:
        if cancel is not None and cancel.is_set():
            raise ReadinessTimeoutError("adapter call はcancelされました")
        if not self.tmux.session_target_exists(self.spec.adapter_session):
            raise ReadinessTimeoutError("adapter sessionがありません")
        if not self.tmux.window_target_exists(self._game_window_target()):
            raise ReadinessTimeoutError("game windowがありません")
        states = self.tmux.pane_states_checked(self.spec.adapter_session)
        if any(pane.dead for pane in states):
            raise ReadinessTimeoutError("paneがdeadです")
        # capture-pane 自体が成功すること (内容が空でも即失敗にしない)
        self.tmux.capture_pane_checked(self.spec.adapter_session)

    def alive(self, deadline: float, cancel) -> bool:
        return self.tmux.session_target_exists(self.spec.adapter_session)

    def cleanup_runtime(self, deadline: float, cancel) -> None:
        for name, role in (
            (self.spec.agent_window, "agent"),
            (self.spec.game_window, "game"),
        ):
            target = f"{SESSION}:{name}"
            if self.tmux.window_target_exists(target):
                self.tmux.kill_window_owned(target, self._ownership(role))
        if self.tmux.session_target_exists(self.spec.adapter_session):
            self.tmux.kill_session_owned(self.spec.adapter_session, self._ownership("adapter"))

    def start_agent(self, deadline: float, cancel) -> None:
        if not self.tmux.window_target_exists(self._agent_window_target()):
            self.tmux.create_window_owned(
                self.spec.agent_window, self._agent_command(), self._ownership("agent")
            )

    def stop_agent(self, deadline: float, cancel) -> None:
        if self.tmux.window_target_exists(self._agent_window_target()):
            self.tmux.kill_window_owned(self._agent_window_target(), self._ownership("agent"))
"""CLI/TUI game adapter: the game runs in a dedicated tmux session and is
projected onto the shared X11 display via a read-only xterm attach
(architecture.md §4.2)."""
from __future__ import annotations

import shlex
import time

from .. import procs
from ..actions import Action
from .base import Adapter, AdapterError, Observation

GAME_SESSION = "docich-game"


class CliGameAdapter(Adapter):
    name = "cli"

    def _cli_raw(self) -> dict:
        raw = self.ctx.game.raw.get("cli", {})
        if not isinstance(raw, dict):
            raise AdapterError("[cli] はテーブルである必要があります")
        return raw

    def _command_list(self) -> list[str]:
        command = self._cli_raw().get("command")
        if isinstance(command, str) and command.strip():
            return shlex.split(command)
        if isinstance(command, list) and command:
            return [str(c) for c in command]
        raise AdapterError("[cli] に command が設定されていません")

    def _cols(self) -> int:
        return int(self._cli_raw().get("cols", 80))

    def _rows(self) -> int:
        return int(self._cli_raw().get("rows", 24))

    def _font(self) -> str:
        return str(self._cli_raw().get("font", "monospace"))

    def _font_size(self) -> int:
        return int(self._cli_raw().get("font_size", 18))

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

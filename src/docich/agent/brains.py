"""Brain implementations: the observe/act boundary (architecture.md §6).

A brain only needs to implement `decide(obs) -> list[Action]`. `CommandBrain`
delegates to an external process (stateless, spawned fresh every cycle);
`RandomBrain` is a wiring-test/demo generator with no external dependency.
"""
from __future__ import annotations

import random
import shlex
import subprocess
import sys

from .. import procs
from ..actions import Action, ActionError, parse_actions
from ..adapters import AdapterError, Observation
from ..config import GameConfig, GlobalConfig


class CommandBrain:
    """Runs `game.agent.command` fresh every cycle: stdin=Observation JSON,
    stdout=Action JSON (architecture.md §6). No memory across cycles is kept
    by docich; that is the brain script's own responsibility."""

    def __init__(self, g: GlobalConfig, game: GameConfig):
        self.g = g
        self.game = game
        self.cmd = self._resolve_command(game.agent.command)

    @staticmethod
    def _resolve_command(command) -> list[str]:
        if isinstance(command, str) and command.strip():
            return shlex.split(command)
        if isinstance(command, list) and command:
            return [str(c) for c in command]
        raise AdapterError("[agent] brain='command' には command の設定が必要です")

    def decide(self, obs: Observation) -> list[Action]:
        try:
            result = procs.run(
                self.cmd,
                timeout=self.g.agent.brain_timeout_s,
                input=obs.to_json(),
            )
        except subprocess.TimeoutExpired:
            print(f"docich: 警告: brain がタイムアウトしました ({self.game.name})", file=sys.stderr)
            return []
        except OSError as exc:
            print(f"docich: 警告: brain の起動に失敗しました ({self.game.name}): {exc}", file=sys.stderr)
            return []

        if result.returncode != 0:
            print(
                f"docich: 警告: brain が異常終了しました ({self.game.name}, "
                f"code={result.returncode}): {result.stderr.strip()}",
                file=sys.stderr,
            )
            return []

        try:
            return parse_actions(result.stdout)
        except ActionError as exc:
            print(
                f"docich: 警告: brain の出力を解析できませんでした ({self.game.name}): {exc}",
                file=sys.stderr,
            )
            return []


class RandomBrain:
    """Random action generator for wiring tests / demos (architecture.md §6).
    The action space depends on the adapter kind reported in the Observation."""

    def __init__(self, g: GlobalConfig, game: GameConfig):
        self.g = g
        self.game = game

    def decide(self, obs: Observation) -> list[Action]:
        if obs.adapter == "retroarch":
            button = random.choice(["up", "down", "left", "right", "a", "b"])
            return [Action(type="pad", buttons=[button], hold_ms=120)]
        if obs.adapter == "cli":
            text = random.choice(["h", "j", "k", "l"])
            return [Action(type="text", text=text)]
        # browser (および未知の adapter) は待機のみ
        return [Action(type="wait", ms=500)]


def build_brain(g: GlobalConfig, game: GameConfig):
    kind = game.agent.brain
    if kind == "command":
        return CommandBrain(g, game)
    if kind == "random":
        return RandomBrain(g, game)
    raise AdapterError(f"未知の brain です: {kind!r} (使用可能: command, random)")

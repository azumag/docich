"""Brain implementations: the observe/act boundary (architecture.md §6).

A brain only needs to implement `decide(obs) -> list[Action]`. `CommandBrain`
delegates to an external process (stateless, spawned fresh every cycle);
`RandomBrain` is a wiring-test/demo generator with no external dependency.
"""
from __future__ import annotations

import json
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
                # tmux セッションの cwd に依存せず、brain の相対パス参照
                # (例: "brains/hanjuku/brain.py") を安定させる (hanjuku_brain.md §1)。
                cwd=str(self.g.repo_root),
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


class ResolverBrain:
    """Deterministic in-process resolver (token-free).

    Parses the observation text and computes the next keys locally
    (docich.resolver); no LLM call and no subprocess per move.  Strategy
    weights hot-reload from ``<state_dir>/resolver/<game>_strategy.json`` on
    mtime change, so docich.resolver.improve can promote new parameters
    without restarting the agent loop.
    """

    def __init__(self, g: GlobalConfig, game: GameConfig):
        # Imported lazily: docich.resolver imports docich.adapters, and the
        # agent package must stay importable from the adapter layer without
        # an import cycle.
        from ..resolver import resolver_policy, strategy_path

        self.g = g
        self.game = game
        self.policy = resolver_policy(game.name)
        self.strategy_file = strategy_path(g.state_dir, game.name)
        self._strategy: dict = {}
        self._strategy_mtime: int | None = -1

    def _load_strategy(self) -> dict:
        try:
            mtime = self.strategy_file.stat().st_mtime_ns
        except OSError:
            mtime = None
        if mtime != self._strategy_mtime:
            data: dict = {}
            if mtime is not None:
                try:
                    loaded = json.loads(self.strategy_file.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        data = loaded
                except (OSError, ValueError):
                    data = {}
            self._strategy = data
            self._strategy_mtime = mtime
        return self._strategy

    def decide(self, obs: Observation) -> list[Action]:
        text = obs.text or ""
        keys = self.policy(text, self._load_strategy())
        # In BSD robots, ``y`` is both the normal up-left movement key and the
        # affirmative answer at the end-of-match prompt.  Bind the draining
        # hold to the prompt itself; key value alone would freeze a live match
        # whenever the resolver's safest movement happened to be up-left.
        from ..resolver import robots

        is_restart = self.game.name == "robots" and robots.game_over(text)
        if keys and keys[0] == "y" and is_restart and self._draining():
            # The game-over prompt belongs to the round-boundary waiter while
            # the canonical phase is draining: answering it here would consume
            # the prompt before the waiter can ack (and record the score),
            # and the match would restart outside the guarded handover.
            # Mid-match play continues normally; only the restart is held.
            return []
        if keys and keys[0] == "y" and is_restart:
            # The restart key is the one moment the final match score is
            # visible in the pane; record it for the score-history panel.
            self._record_match_score(text)
        return [Action(type="text", text=key) for key in keys]

    def _draining(self) -> bool:
        """True while a game switch is waiting for this match to end.

        Read straight from the canonical JSON (never through the coordinator
        lock: the brain must stay lock-free).  Missing file = no coordinator
        activity = safe to restart as usual.
        """
        try:
            from pathlib import Path

            data = json.loads(
                (Path(self.g.state_dir) / "game_switch.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return False
        return isinstance(data, dict) and data.get("phase") == "draining"


    def _record_match_score(self, text: str) -> None:
        """Append the live match score to the per-game history (best effort)."""
        try:
            if self.game.name != "robots":
                return
            from .resolver import scorelog
            from .resolver.robots import score_from_text

            score = score_from_text(text)
            if isinstance(score, int):
                scorelog.record(self.g.state_dir, self.game.name, score, source="agent")
        except Exception:
            pass



def build_brain(g: GlobalConfig, game: GameConfig):
    kind = game.agent.brain
    if kind == "command":
        return CommandBrain(g, game)
    if kind == "random":
        return RandomBrain(g, game)
    if kind == "resolver":
        return ResolverBrain(g, game)
    raise AdapterError(f"未知の brain です: {kind!r} (使用可能: command, random, resolver)")

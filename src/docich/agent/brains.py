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
from copy import deepcopy

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


class NethackPolicyBrain:
    """Layered CLI NetHack brain with observational sidecars.

    Gameplay actions still come exclusively from the reviewed deterministic
    policy and progress resolver (one observed-context-checked key).
    P3e advisory, P4a observation shadow, and P5e candidate shadow are
    observational only and are never translated into gameplay Actions here.
    """

    def __init__(self, g: GlobalConfig, game: GameConfig):
        if game.name != "nethack" or game.adapter != "cli":
            raise AdapterError("brain='nethack' はCLI NetHack専用です")
        from ..nethack_advisory import NethackAdvisoryController
        from ..nethack_candidate_shadow import (
            NethackCandidateShadowController,
            NethackCandidateShadowError,
        )
        from ..nethack_policy import NethackLayeredPolicy
        from ..nethack_shadow import NethackShadowController

        self.g = g
        self.game = game
        raw = game.raw.get("cli", {}) if isinstance(game.raw, dict) else {}
        self.cols = int(raw.get("cols", 80)) if isinstance(raw, dict) else 80
        self.rows = int(raw.get("rows", 24)) if isinstance(raw, dict) else 24
        self.policy = NethackLayeredPolicy()
        from ..nethack_progress import NethackProgressResolver
        self.progress = NethackProgressResolver()
        self.last_progress_decision = None
        self._action_plan = None
        self._action_validated = False
        self._startup_checkpoint = None
        self.last_decision = None
        self.last_shadow = None
        self.last_advisory = None
        self.last_candidate_shadow = None
        from ..nethack_narration import NethackNarrator, enabled_flag, runtime_table
        from ..nethack_startup import NethackStartup

        try:
            self.startup = NethackStartup(enabled=enabled_flag(runtime_table(game, "startup")))
            self.narrator = NethackNarrator(g, game)
            self.shadow = NethackShadowController(g, game)
            self.advisory = NethackAdvisoryController(g, game)
            self.candidate_shadow = NethackCandidateShadowController(g, game)
        except (ValueError, NethackCandidateShadowError) as exc:
            raise AdapterError(f"NetHack sidecar設定が不正です: {exc}") from exc

    def decide(self, obs: Observation) -> list[Action]:
        self.discard_action_plan()
        if obs.adapter != "cli" or obs.text is None:
            return []
        from ..nethack_observation import normalize_tty
        from ..nethack_policy import assert_p3b_safe

        normalized = normalize_tty(obs.text, cols=self.cols, rows=self.rows)
        startup_before = dict(self.startup.__dict__)
        startup_actions = self.startup.consider(normalized)
        if startup_actions is not None:
            if startup_actions:
                self._action_plan = (normalized, deepcopy(startup_actions), None)
                self._startup_checkpoint = startup_before
            return startup_actions
        self.progress.observe(normalized, self.policy.explorer)
        decision = self.policy.decide(normalized)
        assert_p3b_safe(decision)
        self.last_decision = decision
        resolved = self.progress.resolve(decision, normalized, self.policy.explorer)
        if self.last_progress_decision is None or resolved.intent != self.last_progress_decision.intent:
            # Fixed policy enums only; this is a plan, not proof of actuation
            # or turn progress. Never persist raw prompts in diagnostics.
            print(f"[nethack-progress] planned={resolved.intent}", file=sys.stderr)
        self.last_progress_decision = resolved
        production_actions = list(self.last_progress_decision.actions)
        if production_actions:
            self._action_plan = (normalized, deepcopy(production_actions), deepcopy(resolved))
        try:
            self.narrator.consider(normalized, decision)
        except Exception:
            # Narration can neither suppress nor invent gameplay actions.
            print("[nethack-narration] status=consider_failed", file=sys.stderr)

        # Shadow comparison deliberately happens after policy + safety guard.
        # A mismatch is telemetry only and cannot replace the TTY observation.
        try:
            self.last_shadow = self.shadow.compare(normalized)
        except Exception as exc:
            self.last_shadow = None
            print(
                f"docich: 警告: NetHack shadow observation をスキップしました: {str(exc)[:200]}",
                file=sys.stderr,
            )

        # Strategist/narration is deliberately fail-open relative to gameplay:
        # an advisory integration failure must not suppress or invent a P3b
        # action. The controller itself is defensive; this outer boundary is a
        # final containment fence around optional viewer/LLM integration.
        try:
            self.last_advisory = self.advisory.consider(obs.text, normalized, decision)
        except Exception as exc:
            self.last_advisory = None
            print(
                f"docich: 警告: NetHack strategist advisory をスキップしました: {str(exc)[:200]}",
                file=sys.stderr,
            )

        # P5e receives a copy of the already-fixed production action surface.
        # Its candidate command runs on a daemon worker, so a slow/failed
        # candidate cannot delay or replace the action returned by this call.
        try:
            self.last_candidate_shadow = self.candidate_shadow.consider(
                obs.text,
                normalized,
                decision,
                tuple(production_actions),
            )
        except Exception as exc:
            self.last_candidate_shadow = None
            print(
                f"docich: 警告: NetHack candidate shadow をスキップしました: {str(exc)[:200]}",
                file=sys.stderr,
            )
        return production_actions

    def validate_action(self, action: Action, fresh: Observation, *, canonical=None) -> bool:
        """Called by the loop inside its send lock, with a fresh TTY capture.

        Exact normalized frame equality intentionally rejects even a changed
        message/turn, not just a changed target. ts is transport metadata and
        is not compared. No progress memory is mutated on rejection.
        """
        from ..nethack_observation import normalize_tty
        from ..nethack_progress import assert_production_safe

        self._action_validated = False
        if self._action_plan is None or fresh.game != "nethack" or fresh.adapter != "cli" or fresh.text is None:
            return False
        planned_obs, actions, decision = self._action_plan
        if len(actions) != 1 or action != actions[0]:
            return False
        current = normalize_tty(fresh.text, cols=self.cols, rows=self.rows)
        if current != planned_obs:
            return False
        if decision is not None and decision.intent == "decline_save":
            # During draining the coordinator owns S/confirmation/cancel. A
            # fresh save prompt alone is NOT permission for the agent to send
            # n. Missing state (including standalone unit calls) fails closed.
            if (
                not isinstance(canonical, dict)
                or canonical.get("phase") != "ready"
                or canonical.get("operation") is not None
                or canonical.get("request_id") is not None
                or not isinstance(canonical.get("active"), dict)
                or canonical["active"].get("game") != "nethack"
            ):
                return False
        if decision is not None:
            assert_production_safe(decision, current)
        # Startup has its own reviewed answers; exact-frame equality applies
        # there too, without passing gameplay y/n semantics to the startup gate.
        self._action_validated = True
        return True

    def action_sent(self, action: Action) -> None:
        """Transport acknowledgement, never proof that NetHack advanced."""
        if self._action_plan is None or not self._action_validated:
            raise RuntimeError("NetHack action was not freshly validated")
        planned_obs, actions, decision = self._action_plan
        if action != actions[0]:
            raise RuntimeError("NetHack action changed after validation")
        if decision is not None:
            self.progress.sent(decision, planned_obs)
        self._startup_checkpoint = None
        self.discard_action_plan()

    def discard_action_plan(self) -> None:
        if self._startup_checkpoint is not None:
            # A startup answer rejected before transport must remain retryable.
            self.startup.__dict__.clear()
            self.startup.__dict__.update(self._startup_checkpoint)
        self._startup_checkpoint = None
        self._action_plan = None
        self._action_validated = False


class ResolverBrain:
    """Deterministic in-process resolver (token-free).

    Parses the observation text and computes the next keys locally
    (docich.resolver); no LLM call and no subprocess per move. Strategy
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
        # affirmative answer at the end-of-match prompt. Bind the draining
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
        lock: the brain must stay lock-free). Missing file = no coordinator
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
    if kind == "nethack":
        return NethackPolicyBrain(g, game)
    if kind == "resolver":
        return ResolverBrain(g, game)
    raise AdapterError(
        f"未知の brain です: {kind!r} (使用可能: command, random, nethack, resolver)"
    )

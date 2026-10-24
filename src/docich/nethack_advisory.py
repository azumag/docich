"""Strategist integration for the NetHack policy brain (P3e, #2746).

This module may call an external strategist, enqueue sparse narration, and
return a small set of Actions for the brain to send when the production policy
holds. Blocking prompts are always answered: with the strategist's approved
answer when available, otherwise with the deterministic safe default. Every
returned Action is re-validated against the exact frame by the brain before it
is sent, and every decision is recorded in the advisory log.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from .actions import Action
from .config import GameConfig, GlobalConfig
from .nethack_inventory import parse_visible_inventory
from .nethack_observation import NethackObservation
from .nethack_policy import PolicyDecision
from .nethack_strategist import (
    CommandStrategist,
    ProposalEvaluation,
    StrategistDispatchResult,
    default_prompt_answer,
    dispatch_error_kind_for_exception,
    evaluate_proposal,
    execution_plan,
    prompt_answer_action,
    safe_dispatch_error_kind,
)
from .nethack_strategy import build_strategic_request, should_narrate


# Prayer needs the production resolver's timeout bookkeeping (_pray_expected),
# which advisory keys bypass, so a proposed prayer is logged but never sent.
_BRAIN_SENDABLE_KINDS = frozenset({"rest", "answer_prompt"})

AdvisoryStatus = Literal[
    "disabled",
    "not_needed",
    "cooldown",
    "budget_exhausted",
    "error",
    "proposed",
]


@dataclass(frozen=True)
class NethackAdvisoryConfig:
    enabled: bool = False
    command: str | tuple[str, ...] = ""
    timeout_s: float = 20.0
    cooldown_s: float = 30.0
    max_calls: int = 24
    narration_enabled: bool = False
    narration_cooldown_s: float = 20.0
    speaker: str = ""


@dataclass(frozen=True)
class AdvisoryOutcome:
    status: AdvisoryStatus
    call_index: int = 0
    proposal_kind: str | None = None
    evaluation_status: str | None = None
    evaluation_reason: str | None = None
    error: str | None = None
    narrated: bool = False
    actions: tuple[Action, ...] = ()


def _bounded_number(raw: object, *, default: float, minimum: float, maximum: float, name: str) -> float:
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(f"{name} must be numeric")
    value = float(raw)
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def load_advisory_config(game: GameConfig) -> NethackAdvisoryConfig:
    raw = game.raw if isinstance(game.raw, dict) else {}
    nethack = raw.get("nethack", {})
    section = nethack.get("strategist", {}) if isinstance(nethack, dict) else {}
    if section is None:
        section = {}
    if not isinstance(section, dict):
        raise ValueError("[nethack.strategist] must be a table")

    enabled = section.get("enabled", False)
    narration_enabled = section.get("narration_enabled", False)
    if type(enabled) is not bool or type(narration_enabled) is not bool:
        raise ValueError("strategist enabled flags must be boolean")

    command_raw = section.get("command", "")
    command: str | tuple[str, ...]
    if isinstance(command_raw, str):
        command = command_raw.strip()
    elif isinstance(command_raw, list) and all(isinstance(part, str) for part in command_raw):
        command = tuple(part for part in command_raw if part)
    else:
        raise ValueError("strategist command must be a string or string array")
    if enabled and not command:
        raise ValueError("enabled NetHack strategist requires command")

    max_calls = section.get("max_calls", 24)
    if type(max_calls) is not int or not 1 <= max_calls <= 1000:
        raise ValueError("strategist max_calls must be an integer between 1 and 1000")

    speaker = section.get("speaker", "")
    if not isinstance(speaker, str) or len(speaker) > 80:
        raise ValueError("strategist speaker must be a short string")

    return NethackAdvisoryConfig(
        enabled=enabled,
        command=command,
        timeout_s=_bounded_number(
            section.get("timeout_s"), default=20.0, minimum=0.1, maximum=120.0, name="timeout_s"
        ),
        cooldown_s=_bounded_number(
            section.get("cooldown_s"), default=30.0, minimum=0.0, maximum=3600.0, name="cooldown_s"
        ),
        max_calls=max_calls,
        narration_enabled=narration_enabled,
        narration_cooldown_s=_bounded_number(
            section.get("narration_cooldown_s"),
            default=20.0,
            minimum=0.0,
            maximum=3600.0,
            name="narration_cooldown_s",
        ),
        speaker=speaker,
    )


def _fallback_narration(decision: PolicyDecision) -> str:
    fixed = {
        "survival_emergency": "体力が危険域です。安全な画面では . で1ターン待機します。",
        "status_emergency": "危険な状態異常が見えています。安全な画面では . で1ターン進めます。",
        "status_recovery": "危険な状態異常に対して祈りによる回復を試みます。",
        "food_emergency": "空腹が危険域です。安全な画面では . で1ターン進めます。",
        "prompt_decision": "選択を求められています。内容を確認してから回答します。",
        "assess_contact": "近くに生物がいます。安全な退避または通常の接触を選びます。",
        "exploration_blocked": "安全に進める道が見つからないため、. で1ターン進めます。",
    }
    return fixed.get(decision.intent, "NetHackの状況が変わりました。判断を見直します。")


class NethackAdvisoryController:
    """Rate-limited advisory strategist and sparse narration sidecar."""

    def __init__(
        self,
        g: GlobalConfig,
        game: GameConfig,
        *,
        config: NethackAdvisoryConfig | None = None,
        strategist: object | None = None,
        narrator: Callable[[str], None] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        wall_time: Callable[[], float] = time.time,
    ) -> None:
        self.g = g
        self.game = game
        self.config = config or load_advisory_config(game)
        self._monotonic = monotonic
        self._wall_time = wall_time
        self._calls = 0
        self._last_dispatch_at: float | None = None
        self._last_dispatch_intent: str | None = None
        self._last_narration_at: float | None = None
        self._last_narration_intent: str | None = None
        self._previous_intent: str | None = None
        self._last_prompt_log_fingerprint: str | None = None

        if strategist is not None:
            self.strategist = strategist
        elif self.config.enabled:
            command = list(self.config.command) if isinstance(self.config.command, tuple) else self.config.command
            self.strategist = CommandStrategist(
                command,
                timeout_s=self.config.timeout_s,
                cwd=Path(getattr(g, "repo_root", ".")),
            )
        else:
            self.strategist = None

        if narrator is not None:
            self._narrator = narrator
        elif self.config.narration_enabled:
            def _send(text: str) -> None:
                from .trading.soren_output import enqueue_audio_text

                enqueue_audio_text(g, text, context="nethack", speaker=self.config.speaker)

            self._narrator = _send
        else:
            self._narrator = None

        self.log_path = Path(getattr(g, "state_dir", ".")) / "nethack" / "strategist" / "advisory.jsonl"

    @property
    def calls_used(self) -> int:
        return self._calls

    def _fingerprint(self, obs: NethackObservation, decision: PolicyDecision) -> str:
        material = {
            "intent": decision.intent,
            "turn": obs.vitals.turn,
            "dlvl": obs.vitals.dungeon_level,
            "prompt": obs.prompt,
            "message": obs.message,
        }
        payload = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def _append_log(self, payload: dict[str, object]) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
            fd = os.open(self.log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
            try:
                encoded = line.encode("utf-8")
                offset = 0
                while offset < len(encoded):
                    offset += os.write(fd, encoded[offset:])
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            # Advisory logging must never interfere with gameplay.
            return

    def _narrate(
        self,
        decision: PolicyDecision,
        *,
        previous_intent: str | None,
        proposal_narration: str = "",
        proposal_rationale: str = "",
    ) -> bool:
        if self._narrator is None:
            return False
        if not should_narrate(decision, previous_intent=previous_intent):
            return False
        now = self._monotonic()
        if (
            self._last_narration_at is not None
            and self._last_narration_intent == decision.intent
            and now - self._last_narration_at < self.config.narration_cooldown_s
        ):
            return False
        text = (proposal_narration or proposal_rationale or _fallback_narration(decision)).strip()[:240]
        if not text:
            return False
        try:
            self._narrator(text)
        except Exception:
            return False
        self._last_narration_at = now
        self._last_narration_intent = decision.intent
        return True

    def _default_prompt_actions(self, obs: NethackObservation) -> tuple[Action, ...]:
        answer = default_prompt_answer(obs)
        if answer is None:
            return ()
        return (prompt_answer_action(answer),)

    def _answer_prompt_locally(
        self,
        obs: NethackObservation,
        decision: PolicyDecision,
        *,
        previous_intent: str | None,
        status: AdvisoryStatus,
        call_index: int = 0,
    ) -> AdvisoryOutcome:
        """Answer a blocking prompt without the strategist (fail-safe default).

        A blocking prompt must never leave the game stuck, so this runs whenever
        the strategist is disabled, over budget, cooling down or unavailable.
        """
        narrated = self._narrate(decision, previous_intent=previous_intent)
        actions = self._default_prompt_actions(obs)
        fingerprint = self._fingerprint(obs, decision)
        if fingerprint != self._last_prompt_log_fingerprint:
            self._last_prompt_log_fingerprint = fingerprint
            self._append_log(
                {
                    "schema_version": 1,
                    "ts": self._wall_time(),
                    "status": status,
                    "call_index": call_index,
                    "intent": decision.intent,
                    "reason": decision.reason,
                    "fingerprint": fingerprint,
                    "prompt": obs.prompt,
                    "narrated": narrated,
                    "execution": "default_prompt_answer" if actions else "none",
                }
            )
        return AdvisoryOutcome(
            status=status,
            call_index=call_index,
            narrated=narrated,
            actions=actions,
        )

    def consider(
        self,
        raw_text: str,
        obs: NethackObservation,
        decision: PolicyDecision,
    ) -> AdvisoryOutcome:
        previous_intent = self._previous_intent
        self._previous_intent = decision.intent
        blocking = decision.intent == "prompt_decision"

        if not self.config.enabled or self.strategist is None:
            if blocking:
                return self._answer_prompt_locally(
                    obs, decision, previous_intent=previous_intent, status="disabled"
                )
            narrated = self._narrate(decision, previous_intent=previous_intent)
            return AdvisoryOutcome(status="disabled", narrated=narrated)

        if not decision.requires_llm:
            narrated = self._narrate(decision, previous_intent=previous_intent)
            return AdvisoryOutcome(status="not_needed", narrated=narrated)

        now = self._monotonic()
        if self._calls >= self.config.max_calls:
            if blocking:
                return self._answer_prompt_locally(
                    obs,
                    decision,
                    previous_intent=previous_intent,
                    status="budget_exhausted",
                    call_index=self._calls,
                )
            narrated = self._narrate(decision, previous_intent=previous_intent)
            outcome = AdvisoryOutcome(status="budget_exhausted", call_index=self._calls, narrated=narrated)
            self._append_log(
                {
                    "schema_version": 1,
                    "ts": self._wall_time(),
                    "status": outcome.status,
                    "intent": decision.intent,
                    "reason": decision.reason,
                    "calls_used": self._calls,
                    "fingerprint": self._fingerprint(obs, decision),
                }
            )
            return outcome

        if (
            self._last_dispatch_at is not None
            and self._last_dispatch_intent == decision.intent
            and now - self._last_dispatch_at < self.config.cooldown_s
        ):
            if blocking:
                return self._answer_prompt_locally(
                    obs,
                    decision,
                    previous_intent=previous_intent,
                    status="cooldown",
                    call_index=self._calls,
                )
            narrated = self._narrate(decision, previous_intent=previous_intent)
            return AdvisoryOutcome(status="cooldown", call_index=self._calls, narrated=narrated)

        inventory = parse_visible_inventory(raw_text)
        request = build_strategic_request(obs, decision, inventory)
        self._calls += 1
        self._last_dispatch_at = now
        self._last_dispatch_intent = decision.intent
        call_index = self._calls

        try:
            result = self.strategist.dispatch(request)
        except Exception as exc:
            result = StrategistDispatchResult(
                status="error",
                error=str(exc).replace("\n", " ")[:240],
                error_kind=dispatch_error_kind_for_exception(exc),
            )

        evaluation: ProposalEvaluation | None = None
        proposal_kind: str | None = None
        proposal_narration = ""
        proposal_rationale = ""
        actions: tuple[Action, ...] = ()
        execution = "advisory_only"
        if result.status == "proposed" and result.proposal is not None:
            proposal = result.proposal
            proposal_kind = proposal.kind
            proposal_narration = proposal.narration
            proposal_rationale = proposal.rationale
            evaluation = evaluate_proposal(
                request,
                proposal,
                current_observation=obs,
                current_inventory=inventory,
            )
            plan = execution_plan(evaluation)
            if plan.allowed and proposal.kind in _BRAIN_SENDABLE_KINDS:
                actions = plan.actions
                execution = "strategist_action"
        if blocking and not actions:
            # Rejected, errored or non-answer proposals fall back to the safe default.
            actions = self._default_prompt_actions(obs)
            execution = "default_prompt_answer"

        narrated = self._narrate(
            decision,
            previous_intent=previous_intent,
            proposal_narration=proposal_narration,
            proposal_rationale=proposal_rationale,
        )
        status: AdvisoryStatus = "proposed" if result.status == "proposed" else "error"
        outcome = AdvisoryOutcome(
            status=status,
            call_index=call_index,
            proposal_kind=proposal_kind,
            evaluation_status=evaluation.status if evaluation is not None else None,
            evaluation_reason=evaluation.reason if evaluation is not None else None,
            error=result.error,
            narrated=narrated,
            actions=actions,
        )
        self._append_log(
            {
                "schema_version": 1,
                "ts": self._wall_time(),
                "status": outcome.status,
                "call_index": call_index,
                "intent": decision.intent,
                "reason": decision.reason,
                "fingerprint": self._fingerprint(obs, decision),
                "request": request.to_dict(),
                "proposal": result.proposal.to_dict() if result.proposal is not None else None,
                "evaluation": (
                    {"status": evaluation.status, "reason": evaluation.reason}
                    if evaluation is not None
                    else None
                ),
                "error_kind": (
                    safe_dispatch_error_kind(result.error_kind)
                    if outcome.status == "error"
                    else None
                ),
                "narrated": narrated,
                "execution": execution,
            }
        )
        return outcome

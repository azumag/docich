"""Bounded external strategist dispatch and proposal evaluation (P3d).

The strategist is intentionally model-provider agnostic: a configured external
command receives a :class:`StrategicRequest` JSON document on stdin and must
return one :class:`StrategicProposal` JSON document on stdout.

Crucially, this module does *not* turn proposals into live NetHack keypresses.
The only wait proposal is explicit ``rest`` and maps to one ``.`` key. Every
other state-changing proposal remains advisory until a later executor is
separately implemented, allowlisted, and tested.
"""
from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from . import procs
from .actions import Action
from .nethack_inventory import VisibleInventoryItem
from .nethack_observation import NethackObservation
from .nethack_policy import NethackLayeredPolicy, _has_any, _visible_creature_contact
from .nethack_strategy import (
    StrategicProposal,
    StrategicRequest,
    parse_strategic_proposal,
)


DispatchStatus = Literal["proposed", "error"]
EvaluationStatus = Literal["approved", "rejected"]


@dataclass(frozen=True)
class StrategistDispatchResult:
    status: DispatchStatus
    proposal: StrategicProposal | None = None
    error: str | None = None


@dataclass(frozen=True)
class ProposalEvaluation:
    status: EvaluationStatus
    reason: str
    proposal: StrategicProposal

    @property
    def approved(self) -> bool:
        return self.status == "approved"


@dataclass(frozen=True)
class ExecutionPlan:
    allowed: bool
    reason: str
    actions: tuple[Action, ...] = ()


class CommandStrategist:
    """Run one bounded external strategist command per strategic request."""

    def __init__(
        self,
        command: str | list[str],
        *,
        timeout_s: float = 20.0,
        max_request_bytes: int = 32_768,
        max_response_bytes: int = 16_384,
        cwd: Path | None = None,
        runner: Callable[..., object] = procs.run,
    ) -> None:
        if isinstance(command, str):
            argv = shlex.split(command)
        elif isinstance(command, list):
            argv = [str(part) for part in command]
        else:
            argv = []
        if not argv:
            raise ValueError("strategist command must not be empty")
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not 0 < float(timeout_s) <= 120:
            raise ValueError("timeout_s must be >0 and <=120")
        if type(max_request_bytes) is not int or not 1024 <= max_request_bytes <= 1_048_576:
            raise ValueError("max_request_bytes is out of range")
        if type(max_response_bytes) is not int or not 1024 <= max_response_bytes <= 1_048_576:
            raise ValueError("max_response_bytes is out of range")
        self.command = argv
        self.timeout_s = float(timeout_s)
        self.max_request_bytes = max_request_bytes
        self.max_response_bytes = max_response_bytes
        self.cwd = cwd
        self.runner = runner

    @staticmethod
    def _detail(exc: BaseException) -> str:
        return str(exc).replace("\n", " ")[:240]

    def dispatch(self, request: StrategicRequest) -> StrategistDispatchResult:
        payload = request.to_json()
        if len(payload.encode("utf-8")) > self.max_request_bytes:
            return StrategistDispatchResult(status="error", error="strategist request exceeds size limit")
        try:
            result = self.runner(
                self.command,
                timeout=self.timeout_s,
                input=payload,
                cwd=str(self.cwd) if self.cwd is not None else None,
            )
        except subprocess.TimeoutExpired:
            return StrategistDispatchResult(status="error", error="strategist timeout")
        except OSError as exc:
            return StrategistDispatchResult(
                status="error", error=f"strategist launch failed: {self._detail(exc)}"
            )

        returncode = getattr(result, "returncode", None)
        stdout = getattr(result, "stdout", "")
        stderr = getattr(result, "stderr", "")
        if returncode != 0:
            detail = str(stderr or "").replace("\n", " ").strip()[:160]
            return StrategistDispatchResult(
                status="error",
                error=f"strategist exited with code {returncode}: {detail}".rstrip(": "),
            )
        if not isinstance(stdout, str):
            return StrategistDispatchResult(status="error", error="strategist stdout is not text")
        if len(stdout.encode("utf-8")) > self.max_response_bytes:
            return StrategistDispatchResult(status="error", error="strategist response exceeds size limit")
        try:
            proposal = parse_strategic_proposal(stdout)
        except ValueError as exc:
            return StrategistDispatchResult(
                status="error", error=f"invalid strategist proposal: {self._detail(exc)}"
            )
        return StrategistDispatchResult(status="proposed", proposal=proposal)


def _inventory_by_letter(items: tuple[VisibleInventoryItem, ...]) -> dict[str, VisibleInventoryItem]:
    return {item.letter: item for item in items}


def _snapshot_by_letter(request: StrategicRequest) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for item in request.inventory:
        letter = item.get("letter")
        if isinstance(letter, str) and len(letter) == 1:
            result[letter] = item
    return result


def _item_snapshot_still_matches(
    request: StrategicRequest,
    letter: str,
    current_inventory: tuple[VisibleInventoryItem, ...],
) -> bool:
    before = _snapshot_by_letter(request).get(letter)
    current = _inventory_by_letter(current_inventory).get(letter)
    if before is None or current is None:
        return False
    # Exact public snapshot equality prevents a stale inventory letter from
    # silently referring to a different item or quantity by execution time.
    return before == current.public_dict()


def _allowed_kinds(intent: str) -> frozenset[str]:
    if intent == "prompt_decision":
        return frozenset({"inspect", "answer_prompt"})
    if intent == "survival_emergency":
        return frozenset({"rest", "inspect", "consume", "equip", "use"})
    if intent in {"status_emergency", "food_emergency"}:
        return frozenset({"inspect", "consume", "equip", "use"})
    if intent == "stairs_decision":
        return frozenset({"rest", "inspect", "ascend", "descend"})
    if intent in {"assess_contact", "exploration_blocked"}:
        return frozenset({"rest", "inspect"})
    return frozenset({"rest", "inspect"})


def _prompt_answer_is_compatible(obs: NethackObservation, answer: str | None) -> bool:
    if answer is None:
        return False
    if obs.prompt == "yes_no":
        return answer.lower() in {"y", "n"}
    if obs.prompt == "direction":
        return answer in {"h", "j", "k", "l", "y", "u", "b", "n", "."}
    if obs.prompt == "selection":
        return len(answer) == 1 and answer.isascii()
    if obs.prompt == "text":
        return 0 < len(answer) <= 32
    return False


def evaluate_proposal(
    request: StrategicRequest,
    proposal: StrategicProposal,
    *,
    current_observation: NethackObservation,
    current_inventory: tuple[VisibleInventoryItem, ...] = (),
) -> ProposalEvaluation:
    """Evaluate an advisory proposal against fresh visible state.

    Approval here still does not authorize a keypress; it only means that the
    proposal is internally consistent enough to hand to an executor gate.
    """
    if proposal.kind not in _allowed_kinds(request.intent):
        return ProposalEvaluation(
            status="rejected",
            reason=f"proposal kind {proposal.kind!r} is not allowed for intent {request.intent!r}",
            proposal=proposal,
        )

    if proposal.kind in {"consume", "equip", "use"}:
        assert proposal.inventory_letter is not None
        if not _item_snapshot_still_matches(request, proposal.inventory_letter, current_inventory):
            return ProposalEvaluation(
                status="rejected",
                reason="inventory letter/public item snapshot changed since strategist request",
                proposal=proposal,
            )

    if proposal.kind == "answer_prompt" and not _prompt_answer_is_compatible(
        current_observation, proposal.prompt_answer
    ):
        return ProposalEvaluation(
            status="rejected",
            reason="prompt type or answer no longer matches fresh visible prompt",
            proposal=proposal,
        )

    if proposal.kind == "rest":
        if (
            request.intent != "survival_emergency"
            or current_observation.prompt != "none"
            or current_observation.player is None
            or current_observation.vitals.dungeon_level is None
            or current_observation.vitals.hp is None
            or current_observation.vitals.hp <= 0
            or current_observation.vitals.hp_max is None
            or current_observation.vitals.hp_max <= 0
            or _visible_creature_contact(current_observation)
            or "Hungry" in current_observation.conditions
            or _has_any(
                current_observation,
                NethackLayeredPolicy._SEVERE_CONDITIONS
                | NethackLayeredPolicy._FOOD_EMERGENCY,
            )
        ):
            return ProposalEvaluation(
                status="rejected",
                reason="rest requires a reviewed safe intent and complete gameplay frame without visible creature contact or emergency hunger/status",
                proposal=proposal,
            )

    if proposal.kind != "answer_prompt" and current_observation.prompt not in {"none", "more"}:
        return ProposalEvaluation(
            status="rejected",
            reason="a fresh blocking prompt appeared before proposal evaluation",
            proposal=proposal,
        )

    return ProposalEvaluation(status="approved", reason="public-state checks passed", proposal=proposal)


def execution_plan(evaluation: ProposalEvaluation) -> ExecutionPlan:
    """Final P3d gate. Only explicit ``rest`` is executable in this phase."""
    if not evaluation.approved:
        return ExecutionPlan(allowed=False, reason=f"proposal rejected: {evaluation.reason}")
    if evaluation.proposal.kind == "rest":
        return ExecutionPlan(
            allowed=True,
            reason="rest is the explicit one-turn wait command",
            actions=(Action(type="text", text="."),),
        )
    return ExecutionPlan(
        allowed=False,
        reason=(
            f"executor for {evaluation.proposal.kind!r} is not implemented/allowlisted in P3d"
        ),
    )

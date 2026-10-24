"""Bounded external strategist dispatch and proposal evaluation (P3d/#2746).

The strategist is intentionally model-provider agnostic: a configured external
command receives a :class:`StrategicRequest` JSON document on stdin and must
return one :class:`StrategicProposal` JSON document on stdout.

A proposal is never executed from its text alone. :func:`evaluate_proposal`
re-checks it against the fresh visible state, and :func:`execution_plan` maps
only the kinds with a reviewed executor to keys: ``rest`` (one ``.``),
``pray`` (the reviewed recovery command) and ``answer_prompt`` (one answer to
the visible blocking prompt, see :func:`prompt_answer_action`). The
brain re-validates the exact frame before sending. Other kinds stay
advisory until their executor is implemented and tested.
"""
from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, cast

from . import procs
from .actions import Action
from .nethack_inventory import VisibleInventoryItem
from .nethack_observation import NethackObservation
from .nethack_policy import MAX_PRAY_DUNGEON_LEVEL, NethackLayeredPolicy, SEVERE_RECOVERY_COMMAND, _has_any, _visible_creature_contact
from .nethack_strategy import (
    StrategicProposal,
    StrategicRequest,
    parse_strategic_proposal,
)


DispatchStatus = Literal["proposed", "error"]
EvaluationStatus = Literal["approved", "rejected"]
# Finite, machine-readable failure vocabulary. Raw error/stderr text must stay
# in-process; callers that persist telemetry may only write these categories.
DispatchErrorKind = Literal[
    "timeout",
    "launch_failed",
    "process_failed",
    "invalid_request",
    "invalid_response",
    "internal_error",
]
DISPATCH_ERROR_KINDS: frozenset[str] = frozenset(
    {
        "timeout",
        "launch_failed",
        "process_failed",
        "invalid_request",
        "invalid_response",
        "internal_error",
    }
)


def dispatch_error_kind_for_exception(exc: BaseException) -> DispatchErrorKind:
    """Classify an exception raised outside ``CommandStrategist`` handling."""
    if isinstance(exc, subprocess.TimeoutExpired):
        return "timeout"
    if isinstance(exc, OSError):
        return "launch_failed"
    return "internal_error"


def safe_dispatch_error_kind(raw: object) -> DispatchErrorKind:
    """Return only the finite error vocabulary allowed in persistent output."""
    if isinstance(raw, str) and raw in DISPATCH_ERROR_KINDS:
        return cast(DispatchErrorKind, raw)
    return "internal_error"


@dataclass(frozen=True)
class StrategistDispatchResult:
    status: DispatchStatus
    proposal: StrategicProposal | None = None
    error: str | None = None
    error_kind: DispatchErrorKind | None = None


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
            return StrategistDispatchResult(
                status="error",
                error="strategist request exceeds size limit",
                error_kind="invalid_request",
            )
        try:
            result = self.runner(
                self.command,
                timeout=self.timeout_s,
                input=payload,
                cwd=str(self.cwd) if self.cwd is not None else None,
            )
        except subprocess.TimeoutExpired:
            return StrategistDispatchResult(status="error", error="strategist timeout", error_kind="timeout")
        except OSError as exc:
            return StrategistDispatchResult(
                status="error",
                error=f"strategist launch failed: {self._detail(exc)}",
                error_kind="launch_failed",
            )

        returncode = getattr(result, "returncode", None)
        stdout = getattr(result, "stdout", "")
        stderr = getattr(result, "stderr", "")
        if returncode != 0:
            detail = str(stderr or "").replace("\n", " ").strip()[:160]
            return StrategistDispatchResult(
                status="error",
                error=f"strategist exited with code {returncode}: {detail}".rstrip(": "),
                error_kind="process_failed",
            )
        if not isinstance(stdout, str):
            return StrategistDispatchResult(
                status="error",
                error="strategist stdout is not text",
                error_kind="invalid_response",
            )
        if len(stdout.encode("utf-8")) > self.max_response_bytes:
            return StrategistDispatchResult(
                status="error",
                error="strategist response exceeds size limit",
                error_kind="invalid_response",
            )
        try:
            proposal = parse_strategic_proposal(stdout)
        except ValueError as exc:
            return StrategistDispatchResult(
                status="error",
                error=f"invalid strategist proposal: {self._detail(exc)}",
                error_kind="invalid_response",
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


# Escape cancels any NetHack question. It is the only answer accepted for
# free-text and unrecognized prompts, so the model can never type into them.
PROMPT_ESCAPE = "ESC"
# Kinds with a reviewed executor in execution_plan. Only these may be approved.
EXECUTABLE_KINDS = frozenset({"rest", "pray", "answer_prompt"})
_DIRECTION_ANSWERS = frozenset({"h", "j", "k", "l", "y", "u", "b", "n", "."})
_CANCEL_ONLY_PROMPTS = frozenset({"text", "unknown"})


def prompt_answer_action(answer: str) -> Action:
    """Map a validated prompt answer to one literal key (Escape is a special key)."""
    if answer == PROMPT_ESCAPE:
        return Action(type="special", key="Escape")
    return Action(type="text", text=answer)


def default_prompt_answer(obs: NethackObservation) -> str | None:
    """Deterministic safe answer for a visible blocking prompt, or None.

    Used when the strategist is disabled, unavailable, over budget, or its
    proposal is rejected, so a blocking prompt never leaves the game stuck.
    """
    if obs.prompt == "yes_no":
        return "n"
    if obs.prompt in _CANCEL_ONLY_PROMPTS or obs.prompt in {"direction", "selection"}:
        return PROMPT_ESCAPE
    return None


def _prompt_answer_is_compatible(obs: NethackObservation, answer: str | None) -> bool:
    if answer is None or obs.prompt == "none":
        return False
    if answer == PROMPT_ESCAPE:
        return True
    if obs.prompt == "yes_no":
        return answer.lower() in {"y", "n"}
    if obs.prompt == "direction":
        return answer in _DIRECTION_ANSWERS
    if obs.prompt == "selection":
        return len(answer) == 1 and answer.isascii() and answer.isprintable() and not answer.isspace()
    return False


def evaluate_proposal(
    request: StrategicRequest,
    proposal: StrategicProposal,
    *,
    current_observation: NethackObservation,
    current_inventory: tuple[VisibleInventoryItem, ...] = (),
) -> ProposalEvaluation:
    """Re-check a proposal against fresh visible state.

    Approval means the proposal is still consistent with the fresh frame; the
    keys themselves come from :func:`execution_plan`, and the brain re-checks
    the exact frame again before sending.
    """
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

    if proposal.kind == "pray":
        if (
            request.intent not in {"status_emergency", "status_recovery"}
            or current_observation.prompt not in {"none", "more"}
            or current_observation.player is None
            or current_observation.vitals.dungeon_level is None
            or current_observation.vitals.dungeon_level > MAX_PRAY_DUNGEON_LEVEL
            or current_observation.vitals.hp is None
            or current_observation.vitals.hp <= 0
            or current_observation.vitals.hp_max is None
            or current_observation.vitals.hp_max <= 0
            or not _has_any(current_observation, NethackLayeredPolicy._SEVERE_CONDITIONS)
        ):
            return ProposalEvaluation(
                status="rejected",
                reason="prayer recovery requires a severe status on a complete, non-Gehennom frame",
                proposal=proposal,
            )

    if proposal.kind != "answer_prompt" and current_observation.prompt not in {"none", "more"}:
        return ProposalEvaluation(
            status="rejected",
            reason="a fresh blocking prompt appeared before proposal evaluation",
            proposal=proposal,
        )

    if proposal.kind not in EXECUTABLE_KINDS:
        return ProposalEvaluation(
            status="rejected",
            reason=f"proposal kind {proposal.kind!r} has no reviewed executor yet",
            proposal=proposal,
        )

    return ProposalEvaluation(status="approved", reason="public-state checks passed", proposal=proposal)


def execution_plan(evaluation: ProposalEvaluation) -> ExecutionPlan:
    """Final gate: only kinds with a reviewed executor produce keys."""
    if not evaluation.approved:
        return ExecutionPlan(allowed=False, reason=f"proposal rejected: {evaluation.reason}")
    if evaluation.proposal.kind == "rest":
        return ExecutionPlan(
            allowed=True,
            reason="rest is the explicit one-turn wait command",
            actions=(Action(type="text", text="."),),
        )
    if evaluation.proposal.kind == "pray":
        return ExecutionPlan(
            allowed=True,
            reason="prayer is the reviewed recovery command for severe status",
            actions=(Action(type="text", text=SEVERE_RECOVERY_COMMAND),),
        )
    if evaluation.proposal.kind == "answer_prompt":
        assert evaluation.proposal.prompt_answer is not None
        return ExecutionPlan(
            allowed=True,
            reason="answer to the visible blocking prompt, revalidated against fresh state",
            actions=(prompt_answer_action(evaluation.proposal.prompt_answer),),
        )
    return ExecutionPlan(
        allowed=False,
        reason=(
            f"executor for {evaluation.proposal.kind!r} has no reviewed executor yet"
        ),
    )

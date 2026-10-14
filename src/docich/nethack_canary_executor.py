"""Canary-only executor for approved NetHack strategic proposals (P5h).

This module is deliberately NOT imported by the production NetHack brain.
Production P3d keeps its explicit-rest-only execution gate. The mappings below
exist only inside the isolated canary container where state-changing proposals
may be exercised without touching the long-running production adventure.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .nethack_inventory import VisibleInventoryItem
from .nethack_observation import NethackObservation
from .nethack_policy import _visible_creature_contact
from .nethack_strategist import ProposalEvaluation
from .nethack_strategy import StrategicRequest

KeyKind = Literal["literal", "special"]


@dataclass(frozen=True)
class CanaryKey:
    kind: KeyKind
    value: str


@dataclass(frozen=True)
class CanaryExecutionPlan:
    allowed: bool
    reason: str
    keys: tuple[CanaryKey, ...] = ()


def _literal(value: str) -> CanaryKey:
    return CanaryKey(kind="literal", value=value)


def _special(value: str) -> CanaryKey:
    return CanaryKey(kind="special", value=value)


def _item_by_letter(
    items: tuple[VisibleInventoryItem, ...], letter: str | None
) -> VisibleInventoryItem | None:
    if letter is None:
        return None
    return next((item for item in items if item.letter == letter), None)


def canary_execution_plan(
    request: StrategicRequest,
    evaluation: ProposalEvaluation,
    *,
    current_observation: NethackObservation,
    current_inventory: tuple[VisibleInventoryItem, ...] = (),
) -> CanaryExecutionPlan:
    """Map an already-approved proposal to a tiny canary-only key surface.

    ``evaluate_proposal`` remains the first gate.  This second gate is more
    restrictive than the proposal schema and intentionally rejects commands
    whose fresh visible state is insufficient to execute unambiguously.
    """
    if not evaluation.approved:
        return CanaryExecutionPlan(
            allowed=False,
            reason=f"proposal rejected before canary executor: {evaluation.reason}",
        )

    proposal = evaluation.proposal
    if proposal.kind == "rest":
        if (
            current_observation.prompt != "none"
            or current_observation.player is None
            or _visible_creature_contact(current_observation)
        ):
            return CanaryExecutionPlan(
                allowed=False,
                reason="fresh gameplay frame no longer permits an explicit rest",
            )
        return CanaryExecutionPlan(
            allowed=True,
            reason="rest is one explicit wait command",
            keys=(_literal("."),),
        )

    if proposal.kind == "inspect":
        # Inspection is advisory in P5h.  Inventory probing is performed by the
        # worker before strategist dispatch and never delegated to the model.
        return CanaryExecutionPlan(
            allowed=True,
            reason="inspect is a canary no-op",
        )

    if proposal.kind == "answer_prompt":
        answer = proposal.prompt_answer
        if not isinstance(answer, str) or not answer:
            return CanaryExecutionPlan(False, "prompt answer is missing")
        if current_observation.prompt == "text":
            return CanaryExecutionPlan(
                True,
                "fresh text prompt answer",
                (_literal(answer), _special("Enter")),
            )
        return CanaryExecutionPlan(
            True,
            f"fresh {current_observation.prompt} prompt answer",
            (_literal(answer),),
        )

    item = _item_by_letter(current_inventory, proposal.inventory_letter)
    if proposal.kind in {"consume", "equip", "use"} and item is None:
        return CanaryExecutionPlan(False, "fresh inventory item is unavailable")
    if item is not None and item.unpaid:
        return CanaryExecutionPlan(False, "unpaid item is outside canary executor surface")

    if proposal.kind == "consume" and item is not None:
        if item.category_hint == "food":
            return CanaryExecutionPlan(
                True,
                "consume visibly classified food",
                (_literal("e"), _literal(item.letter)),
            )
        if item.category_hint == "potion":
            return CanaryExecutionPlan(
                True,
                "quaff visibly classified potion",
                (_literal("q"), _literal(item.letter)),
            )
        return CanaryExecutionPlan(
            False,
            f"consume category {item.category_hint!r} is not allowlisted",
        )

    if proposal.kind == "equip" and item is not None:
        if item.equipped:
            return CanaryExecutionPlan(True, "item is already equipped")
        if item.category_hint == "weapon":
            return CanaryExecutionPlan(
                True,
                "wield visibly classified weapon",
                (_literal("w"), _literal(item.letter)),
            )
        if item.category_hint == "armor":
            return CanaryExecutionPlan(
                True,
                "wear visibly classified armor",
                (_literal("W"), _literal(item.letter)),
            )
        return CanaryExecutionPlan(
            False,
            f"equip category {item.category_hint!r} is not allowlisted",
        )

    if proposal.kind == "use" and item is not None:
        if item.category_hint == "tool":
            return CanaryExecutionPlan(
                True,
                "apply visibly classified tool",
                (_literal("a"), _literal(item.letter)),
            )
        # In particular, a wand commonly needs a direction which StrategicProposal
        # does not currently carry.  Do not guess one.
        return CanaryExecutionPlan(
            False,
            f"use category {item.category_hint!r} needs a richer reviewed executor",
        )

    # Stairs and travel need reliable knowledge of the terrain beneath '@'.
    # The TTY public observation does not expose that hidden-under-player glyph,
    # so P5h does not infer it.
    if proposal.kind in {"ascend", "descend", "move_to_stairs"}:
        return CanaryExecutionPlan(
            False,
            f"{proposal.kind} requires terrain evidence not present in the TTY contract",
        )

    return CanaryExecutionPlan(False, f"unsupported canary proposal kind {proposal.kind!r}")

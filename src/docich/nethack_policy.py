"""Layered NetHack gameplay policy.

P3a established a fail-closed action surface where only ``--More--`` could be
automatically advanced. P3b adds one conservative movement action at a time on
currently visible, known-safe terrain chosen by :mod:`nethack_exploration`.

The policy still never automatically attacks, uses items, answers prompts,
opens doors, steps onto a visible trap/item/creature, or descends stairs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .actions import Action
from .nethack_exploration import NethackExplorer
from .nethack_observation import NethackObservation


DecisionLayer = Literal["tactical", "midlevel", "strategic"]


@dataclass(frozen=True)
class PolicyDecision:
    layer: DecisionLayer
    intent: str
    reason: str
    actions: tuple[Action, ...] = ()
    requires_llm: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "layer": self.layer,
            "intent": self.intent,
            "reason": self.reason,
            "requires_llm": self.requires_llm,
            "actions": [
                {
                    "type": action.type,
                    "text": action.text,
                    "key": action.key,
                    "keys": list(action.keys),
                    "ms": action.ms,
                }
                for action in self.actions
            ],
        }


def _visible_creature_contact(obs: NethackObservation) -> bool:
    # Do not infer peaceful/hostile or species identity from a TTY letter.
    # This is only a reason to stop the low-level explorer and ask a higher
    # layer what to do with a visible adjacent contact.
    for glyph in obs.visible_neighbors():
        if glyph.isalpha() or glyph in "&;:'":
            return True
    return False


def _has_any(obs: NethackObservation, names: set[str]) -> bool:
    return any(condition in names for condition in obs.conditions)


class NethackLayeredPolicy:
    """Fail-closed layered policy over normalized public observation."""

    _SEVERE_CONDITIONS = {"Sick", "FoodPois", "Ill", "Slime", "Strngl"}
    _FOOD_EMERGENCY = {"Weak", "Fainting", "Fainted", "Starved"}
    _MOVEMENT_IMPAIRING = {"Blind", "Conf", "Stun", "Hallu"}

    def __init__(self, explorer: NethackExplorer | None = None) -> None:
        self.explorer = explorer or NethackExplorer()

    def decide(self, obs: NethackObservation) -> PolicyDecision:
        if obs.prompt == "more":
            return PolicyDecision(
                layer="tactical",
                intent="advance_message",
                reason="visible --More-- prompt",
                actions=(Action(type="text", text=" "),),
            )

        if obs.prompt in {"yes_no", "direction", "selection", "text"}:
            return PolicyDecision(
                layer="strategic",
                intent="prompt_decision",
                reason=f"blocking {obs.prompt} prompt requires context",
                requires_llm=True,
            )

        hp_ratio = obs.vitals.hp_ratio
        if hp_ratio is not None and hp_ratio <= 0.25:
            return PolicyDecision(
                layer="strategic",
                intent="survival_emergency",
                reason=f"visible HP is critical ({obs.vitals.hp}/{obs.vitals.hp_max})",
                requires_llm=True,
            )

        if _has_any(obs, self._SEVERE_CONDITIONS):
            return PolicyDecision(
                layer="strategic",
                intent="status_emergency",
                reason="visible severe status condition requires recovery plan",
                requires_llm=True,
            )

        if _has_any(obs, self._FOOD_EMERGENCY):
            return PolicyDecision(
                layer="strategic",
                intent="food_emergency",
                reason="visible hunger state is already dangerous",
                requires_llm=True,
            )

        if "Hungry" in obs.conditions:
            return PolicyDecision(
                layer="midlevel",
                intent="seek_food",
                reason="visible Hungry status should alter exploration priority",
            )

        # P3b does not implement recovery/rest tactics yet. Stop exploration at
        # half health rather than continuing just because the state is not yet
        # critical enough for the strategic emergency threshold.
        if hp_ratio is not None and hp_ratio <= 0.50:
            return PolicyDecision(
                layer="midlevel",
                intent="hold_low_hp",
                reason=f"visible HP is below exploration threshold ({obs.vitals.hp}/{obs.vitals.hp_max})",
            )

        if _has_any(obs, self._MOVEMENT_IMPAIRING):
            return PolicyDecision(
                layer="midlevel",
                intent="hold_impaired",
                reason="visible status can make deterministic movement unsafe",
            )

        if obs.player is None:
            return PolicyDecision(
                layer="midlevel",
                intent="inspect_screen",
                reason="player glyph is not uniquely visible; do not guess a move",
            )

        if _visible_creature_contact(obs):
            return PolicyDecision(
                layer="midlevel",
                intent="assess_contact",
                reason="adjacent creature glyph is visible but hostility is unknown",
            )

        step = self.explorer.plan_step(obs)
        if step is not None:
            return PolicyDecision(
                layer="midlevel",
                intent="explore_step",
                reason=(
                    f"{step.reason}; visible target {step.target} glyph={step.target_glyph!r}"
                ),
                actions=(Action(type="text", text=step.key),),
            )

        return PolicyDecision(
            layer="midlevel",
            intent="exploration_blocked",
            reason="no visible safe cardinal exploration step is available",
        )


def assert_p3a_safe(decision: PolicyDecision) -> None:
    """P3a guard retained for tests/backward compatibility."""
    if not decision.actions:
        return
    if (
        decision.layer == "tactical"
        and decision.intent == "advance_message"
        and len(decision.actions) == 1
        and decision.actions[0].type == "text"
        and decision.actions[0].text == " "
    ):
        return
    raise RuntimeError("P3a policy attempted an action outside the safe tactical surface")


def assert_p3b_safe(decision: PolicyDecision) -> None:
    """Allow only More-space or one reviewed cardinal exploration step."""
    if not decision.actions:
        return
    if (
        decision.layer == "tactical"
        and decision.intent == "advance_message"
        and len(decision.actions) == 1
        and decision.actions[0].type == "text"
        and decision.actions[0].text == " "
    ):
        return
    if (
        decision.layer == "midlevel"
        and decision.intent == "explore_step"
        and len(decision.actions) == 1
        and decision.actions[0].type == "text"
        and decision.actions[0].text in {"h", "j", "k", "l"}
    ):
        return
    raise RuntimeError("P3b policy attempted an action outside the reviewed safe surface")

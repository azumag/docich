"""Layered NetHack gameplay policy foundation (P3a).

P3a deliberately performs only one automatic gameplay action: advancing a
visible ``--More--`` prompt.  Every choice that could alter strategy, consume a
resource, attack a possibly peaceful creature, answer a prompt, or choose a
movement direction is escalated to a higher layer with no keypress.

This gives later tactical/mid-level/LLM work a stable contract without making
an unfinished policy dangerous merely by selecting the brain.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .actions import Action
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
    """Fail-closed P3a policy over normalized public observation."""

    _SEVERE_CONDITIONS = {"Sick", "FoodPois", "Ill", "Slime", "Strngl"}
    _FOOD_EMERGENCY = {"Weak", "Fainting", "Fainted", "Starved"}

    def decide(self, obs: NethackObservation) -> PolicyDecision:
        # --More-- carries no strategic choice: advancing it only reveals the
        # next already-visible message/page. This is the sole P3a auto-action.
        if obs.prompt == "more":
            return PolicyDecision(
                layer="tactical",
                intent="advance_message",
                reason="visible --More-- prompt",
                actions=(Action(type="text", text=" "),),
            )

        # Prompts may consume, attack, name, identify, or otherwise commit a
        # choice. Never answer them from a generic low-level policy.
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

        return PolicyDecision(
            layer="midlevel",
            intent="explore",
            reason="no blocking prompt or visible emergency",
        )


def assert_p3a_safe(decision: PolicyDecision) -> None:
    """Guard the intentionally tiny P3a automatic action surface.

    Later phases can replace this guard when they add tested movement/combat
    rules. Until then, a regression cannot silently turn an unfinished policy
    into an autonomous attacker or item consumer.
    """
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

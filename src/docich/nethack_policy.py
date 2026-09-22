"""Layered NetHack gameplay policy.

P3a established a fail-closed action surface where only ``--More--`` could be
automatically advanced. P3b adds one conservative movement action at a time on
currently visible, known-safe terrain chosen by :mod:`nethack_exploration`.

Production wait resolution is in nethack_progress: a bounded ordinary bump is
now the last resort after visible escape, never a forced attack, and a
turn-based wait is always the literal ``.`` command. Item use, doors, stair
traversal and arbitrary prompt answers remain outside the surface.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .actions import Action
from .nethack_exploration import MOVE_KEYS, NethackExplorer, visible_safe_step
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


def turn_ready(obs: NethackObservation) -> bool:
    """Whether a literal gameplay key can safely consume one turn.

    Severe status and hunger emergencies are still handled by the strategic
    safety boundary; this predicate only establishes that the frame is a
    complete gameplay frame. Prompt, unknown, and player-less frames remain
    outside this contract because ``.`` could answer a question rather than
    wait.
    """
    return (
        obs.prompt == "none"
        and obs.player is not None
        and obs.vitals.dungeon_level is not None
        and obs.vitals.hp is not None
        and obs.vitals.hp > 0
        and obs.vitals.hp_max is not None
        and obs.vitals.hp_max > 0
    )


# Default NetHack symbols: f is a feline, { is a fountain. Colour does not
# turn f into terrain, nor does plain text establish a creature's hostility.
def creature_glyph(glyph: str) -> bool:
    return len(glyph) == 1 and ((glyph.isascii() and glyph.isalpha()) or glyph in "&;:'@")


def _visible_creature_contact(obs: NethackObservation) -> bool:
    return any(creature_glyph(glyph) for glyph in obs.visible_neighbors())


def decline_prompt(obs: NethackObservation) -> str | None:
    """Only a whole, unanswered top-line save/attack confirmation permits n.

    Substrings in message history, answered prompts, wrapped/truncated prompts,
    and attack questions with any other answer grammar remain fail-closed.
    """
    if obs.prompt != "yes_no":
        return None
    raw_lines = obs.raw_text.splitlines()
    if not raw_lines or raw_lines[0].strip() != obs.message.strip():
        return None  # do not discard an answer/suffix beyond the capture width
    for intent, pattern in (
        ("decline_save", r"Really save\?\s*\[yn\](?:\s*\(n\))?"),
        ("decline_attack", r"Really attack(?: [^?\[\]\r\n]+)?\?\s*\[yn\](?:\s*\(n\))?"),
    ):
        if re.fullmatch(pattern, obs.message.strip(), re.IGNORECASE):
            return intent
    return None


def _has_any(obs: NethackObservation, names: set[str]) -> bool:
    return any(condition in names for condition in obs.conditions)


class NethackLayeredPolicy:
    """Fail-closed layered policy over normalized public observation."""

    _SEVERE_CONDITIONS = {"Sick", "FoodPois", "Ill", "Slime", "Strngl", "Stone", "TermIll"}
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

        decline = decline_prompt(obs)
        # Attack refusal belongs to the production resolver, not this base
        # policy shared with candidate/canary evaluation.
        if decline == "decline_save":
            return PolicyDecision(
                layer="tactical", intent=decline,
                reason="decline an explicit unanswered save/attack confirmation",
                actions=(Action(type="text", text="n"),),
            )

        if obs.prompt != "none":
            return PolicyDecision(
                layer="strategic",
                intent="prompt_decision",
                reason=f"blocking {obs.prompt} prompt requires context",
                requires_llm=True,
            )

        # Fatal status/hunger must dominate low HP; otherwise critical HP
        # silently turns their fail-closed holds into generic rest/movement.
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

        hp_ratio = obs.vitals.hp_ratio
        if hp_ratio is not None and hp_ratio <= 0.25:
            return PolicyDecision(
                layer="strategic", intent="survival_emergency",
                reason=f"visible HP is critical ({obs.vitals.hp}/{obs.vitals.hp_max})",
                requires_llm=True,
            )

        if "Hungry" in obs.conditions:
            return PolicyDecision(
                layer="midlevel",
                intent="seek_food",
                reason="visible Hungry status should alter exploration priority",
            )

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

        if (
            obs.player is None or obs.vitals.dungeon_level is None
            or obs.vitals.hp is None or obs.vitals.hp <= 0
            or obs.vitals.hp_max is None or obs.vitals.hp_max <= 0
        ):
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
            reason="no visible safe exploration step is available",
        )


def assert_p3b_safe(decision: PolicyDecision) -> None:
    """Allow only reviewed safe surface: More-space, decline-save, or one step."""
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
        decision.layer == "tactical"
        and decision.intent == "decline_save"
        and len(decision.actions) == 1
        and decision.actions[0].type == "text"
        and decision.actions[0].text == "n"
    ):
        return
    if (
        decision.layer == "midlevel"
        and decision.intent == "explore_step"
        and len(decision.actions) == 1
        and decision.actions[0].type == "text"
        and decision.actions[0].text in MOVE_KEYS
    ):
        return
    raise RuntimeError("P3b policy attempted an action outside the reviewed safe surface")


# NetHack is turn-based: while the agent does nothing, nothing changes, so the
# same frame comes back forever. A pet-like ambiguous glyph blocking the only
# corridor or low HP that only time fixes can otherwise freeze a run for good.
# The production agent lets one turn pass with NetHack's rest command for the
# reviewed safe holds below. Severe status and the hunger tiers above `Weak`
# remain fail-closed until an explicit recovery path is reviewed; a generic
# rest can move them closer to death. The policy's own decision remains
# available to advisory/shadow telemetry, but it is never exposed as a no-op
# gameplay choice for the safe hold surface.
REST_KEY = "."
# Reviewed subset of the food emergency that may spend one explicit wait
# turn. NetHack only advances hunger while the hero acts, so resting does
# worsen nutrition -- but a turn-based game whose hero never acts can never
# recover either, and the hero was observed frozen at `Weak` with 0 actions
# on every iteration (2026-09-23 nethack slot). `Weak` is the entry tier of
# that emergency; Fainting/Fainted/Starved keep the fail-closed boundary
# below, so a Weak that keeps resting still stops resting once it worsens.
RESTABLE_FOOD_CONDITIONS = frozenset({"Weak"})
REST_HOLD_INTENTS = frozenset(
    {
        "exploration_blocked",
        "hold_low_hp",
        "hold_impaired",
    }
)

# A critical-HP emergency may eventually need an active recovery plan. Until
# one is available, returning no action permanently freezes the turn-based
# game, so a single explicit ``.`` is allowed when the frame is otherwise
# complete. Severe status and the non-restable hunger tiers are different:
# passing a turn can worsen them, so they remain fail-closed until recovery
# is reviewed. `food_emergency` joins this surface only for the restable
# `Weak` tier, which the condition gate above enforces.
REST_EMERGENCY_INTENTS = frozenset({"survival_emergency"})
RESTABLE_INTENTS = REST_HOLD_INTENTS | REST_EMERGENCY_INTENTS


def rest_action_for_hold(
    decision: PolicyDecision, obs: NethackObservation
) -> Action | None:
    """Return the explicit ``.`` wait action for a reviewed safe hold.

    Unknown screens, prompts, player-less frames, visible creature contact,
    hunger (`Hungry` and the tiers above `Weak`), and severe status
    emergencies remain fail-closed because ``.`` is not a reviewed recovery
    action for those states.
    """
    if (
        decision.actions
        or decision.intent not in RESTABLE_INTENTS | {"food_emergency"}
        or not turn_ready(obs)
        or _visible_creature_contact(obs)
        or "Hungry" in obs.conditions
        or _has_any(obs, NethackLayeredPolicy._SEVERE_CONDITIONS)
        or _has_any(obs, NethackLayeredPolicy._FOOD_EMERGENCY - RESTABLE_FOOD_CONDITIONS)
    ):
        return None
    if decision.layer == "midlevel" and not decision.requires_llm:
        allowed = decision.intent in REST_HOLD_INTENTS
    elif decision.layer == "strategic":
        allowed = decision.intent in REST_EMERGENCY_INTENTS or (
            decision.intent == "food_emergency"
            # The restable tier must actually be observed: a misleading
            # food_emergency intent beside a clean status line never rests.
            and _has_any(obs, RESTABLE_FOOD_CONDITIONS)
            and not _has_any(obs, NethackLayeredPolicy._FOOD_EMERGENCY - RESTABLE_FOOD_CONDITIONS)
        )
    else:
        allowed = False
    if not allowed:
        return None
    return Action(type="text", text=REST_KEY)


# A no-action decision beside a visible creature is a deadlock, not a pause:
# NetHack advances only when the hero acts. Without input, the creature does
# not take its turn either, and the frame can freeze for good. Prefer the
# explorer's safe step, then one
# ordinary contact attempt. If those reviewed routes are exhausted, the
# resolver remains fail-closed rather than resting beside the creature; the
# caller can then surface the blocked state for recovery planning.
STEP_OUT_INTENTS = REST_HOLD_INTENTS | REST_EMERGENCY_INTENTS | {"assess_contact", "seek_food"}


def step_out_of_hold(decision: PolicyDecision, obs: NethackObservation, explorer) -> Action | None:
    """One reviewed move to break a frozen no-action state, else ``None``.

    Used after rest declined or for Hungry exploration: a decision that produced no
    action, with a uniquely visible player and no prompt, where the explorer
    can still name a safe step.  ``food_emergency`` and
    ``inspect_screen`` are excluded -- moving cannot help hunger, and without a
    unique player glyph there is nothing to plan from.
    """
    if (
        decision.actions
        or obs.prompt != "none"
        or obs.player is None
        or decision.intent not in STEP_OUT_INTENTS
        or _has_any(obs, NethackLayeredPolicy._SEVERE_CONDITIONS | NethackLayeredPolicy._FOOD_EMERGENCY | NethackLayeredPolicy._MOVEMENT_IMPAIRING)
    ):
        return None
    step = explorer.plan_step(obs)
    if step is None or not visible_safe_step(obs, step.key):
        return None
    return Action(type="text", text=step.key)


def assert_step_out_safe(actions: list[Action] | tuple[Action, ...]) -> None:
    """The step-out fallback may only ever be one reviewed movement key."""
    if (
        len(actions) != 1
        or actions[0].type != "text"
        or actions[0].text not in MOVE_KEYS
    ):
        raise RuntimeError("step-out fallback attempted an action outside the reviewed safe surface")


def assert_rest_safe(actions: list[Action] | tuple[Action, ...]) -> None:
    """The rest fallback may only ever be exactly one ``.`` text action."""
    if len(actions) != 1 or actions[0].type != "text" or actions[0].text != REST_KEY:
        raise RuntimeError("rest fallback attempted an action outside the reviewed safe surface")


def assert_p3a_safe(decision: PolicyDecision) -> None:
    """Compatibility entrypoint used by the brain; current branch is P3b.

    The function name remains so the stacked P3a tests and brain wiring do not
    churn.  On P3b it delegates to the expanded reviewed safe surface.
    """
    assert_p3b_safe(decision)

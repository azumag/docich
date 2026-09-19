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


# Visible glyphs that are letters in NetHack but are not treated as creatures.
# Treating them as adjacent monsters made P3b hold instead of exploring
# (observed around a fountain 'f' on Dlvl:1).
#
# Caveat: the observation is plain text, so colour is lost, and ``f`` is
# genuinely ambiguous -- depending on colour it is a fountain or a cat (the
# starting pet, or a hostile feline).  The planner never steps onto it either
# way; telling the cases apart needs a colour-aware capture (not done here).
_TERRAIN_LETTER_GLYPHS = frozenset(
    {"f", "{"}  # fountain, water/lava variants rendered as letters
)


def _visible_creature_contact(obs: NethackObservation) -> bool:
    for glyph in obs.visible_neighbors():
        if glyph in _TERRAIN_LETTER_GLYPHS:
            continue
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

        if obs.prompt == "yes_no":
            # Reviewed, safe default answers.  The corner exists to keep one
            # adventure running, so a "Really save? [yn]" (a normal save would
            # end the run and conflict with the run boundary) is declined and
            # play continues.  Anything else stays a deliberate hold.
            lowered = " ".join(obs.raw_text.lower().split())
            if "really save" in lowered:
                return PolicyDecision(
                    layer="tactical",
                    intent="decline_save",
                    reason="decline the save prompt to keep the run going",
                    actions=(Action(type="text", text="n"),),
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
        and decision.actions[0].text in {"h", "j", "k", "l"}
    ):
        return
    raise RuntimeError("P3b policy attempted an action outside the reviewed safe surface")


# NetHack is turn-based: while the agent does nothing, nothing changes, so the
# same frame -- and the same hold -- comes back forever.  A pet-like ambiguous
# glyph blocking the only corridor or low HP that only time fixes can otherwise
# freeze a run for good.  For exactly these non-LLM mid-level holds the
# production agent lets one turn pass with NetHack's rest command, but only
# while no recognized adjacent creature is visible.  The policy's own decision
# is unchanged, so strategy/advisory/shadow keep seeing the hold.
REST_KEY = "."
REST_HOLD_INTENTS = frozenset(
    {
        "exploration_blocked",
        "hold_low_hp",
        "hold_impaired",
        "seek_food",
    }
)

# Emergencies ask for a recovery plan (quaff, pray, flee) that only a strategic
# layer can choose, so the policy deliberately produces no action.  With no
# strategist configured that is again a permanent freeze: observed on production
# 2026-09-19 at HP 4/16, where the agent reported "0 actions" every iteration
# until the corner's stall guard ended it.  Waiting a turn is not the plan, but
# for these two it beats freezing, because HP and most status effects only
# recover as turns pass.  ``food_emergency`` is deliberately excluded: resting
# burns nutrition, so passing turns makes starvation worse, and eating is not on
# the reviewed action surface.  The shared no-adjacent-creature rule still
# applies, so a weakened hero never rests next to something that can hit it.
REST_EMERGENCY_INTENTS = frozenset({"survival_emergency", "status_emergency"})


def rest_action_for_hold(
    decision: PolicyDecision, obs: NethackObservation
) -> Action | None:
    """The single reviewed rest action for a stalled hold, else ``None``.

    Requires a hold that produced no action, a uniquely visible player, no
    prompt, and no recognized adjacent creature.  Within that, either a
    mid-level non-LLM hold (``REST_HOLD_INTENTS``) or one of the two
    emergencies time alone can improve (``REST_EMERGENCY_INTENTS``).  Anything
    else -- an unknown screen, ``food_emergency``, a hold that already acts --
    stays a hold: a stray ``.`` on a prompt or beside a possibly hostile
    creature is not something this guard may risk.
    """
    if (
        decision.actions
        or obs.prompt != "none"
        or obs.player is None
        or _visible_creature_contact(obs)
    ):
        return None
    if decision.layer == "midlevel" and not decision.requires_llm:
        allowed = decision.intent in REST_HOLD_INTENTS
    elif decision.layer == "strategic":
        allowed = decision.intent in REST_EMERGENCY_INTENTS
    else:
        allowed = False
    if not allowed:
        return None
    return Action(type="text", text=REST_KEY)


# A hold beside a visible creature is a deadlock, not a pause: NetHack only
# advances when the hero acts, so the creature never takes its turn either and
# the frame is frozen for good (observed on production 2026-09-19, generation
# 250: HP 4/16 with a ':' adjacent, byte-identical screen, "0 actions" forever).
# Resting there is not allowed -- standing still next to something that can hit
# a weakened hero is how it dies -- so take the explorer's own safe step
# instead.  That step never moves onto a creature, item, trap or door, and it is
# the same reviewed h/j/k/l surface P3b already uses for exploring.
STEP_OUT_INTENTS = REST_HOLD_INTENTS | REST_EMERGENCY_INTENTS | {"assess_contact"}


def step_out_of_hold(decision: PolicyDecision, obs: NethackObservation, explorer) -> Action | None:
    """One reviewed move to break a frozen hold, else ``None``.

    Used only after ``rest_action_for_hold`` declined: a hold that produced no
    action, with a uniquely visible player and no prompt, where the explorer
    can still name a safe cardinal step.  ``food_emergency`` and
    ``inspect_screen`` are excluded -- moving cannot help hunger, and without a
    unique player glyph there is nothing to plan from.
    """
    if (
        decision.actions
        or obs.prompt != "none"
        or obs.player is None
        or decision.intent not in STEP_OUT_INTENTS
    ):
        return None
    step = explorer.plan_step(obs)
    if step is None or step.key not in {"h", "j", "k", "l"}:
        return None
    return Action(type="text", text=step.key)


def assert_step_out_safe(actions: list[Action] | tuple[Action, ...]) -> None:
    """The step-out fallback may only ever be one reviewed cardinal move."""
    if (
        len(actions) != 1
        or actions[0].type != "text"
        or actions[0].text not in {"h", "j", "k", "l"}
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

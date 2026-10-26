"""Layered NetHack gameplay policy.

P3a established a fail-closed action surface where only ``--More--`` could be
automatically advanced. P3b adds one conservative movement action at a time on
currently visible, known-safe terrain chosen by :mod:`nethack_exploration`.

Production wait resolution is in nethack_progress: a bounded ordinary bump is
now the last resort after visible escape, never a forced attack, and a
turn-based wait is always the literal ``.`` command. Item use, stair traversal
and arbitrary prompt answers remain outside the surface. The production
progress resolver handles visible adjacent doors through its own two-frame
command contract.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Literal

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




SEVERE_RECOVERY_COMMAND = "#"

# NetHack's "go down the stairs" command. Reviewed in #2666: the action is
# only produced when exploration memory already saw a '>' on the hero's own
# tile, and on a down staircase the game descends immediately. Used anywhere
# else the game answers "You can't go down here." without changing the turn,
# so the decision is never guessed from the current frame alone (the hero
# glyph hides the terrain).
DESCEND_COMMAND = ">"
# NetHack's refusal line when the hero is not on a staircase. It proves the
# remembered tile is stale, so the tile is forgotten instead of retried.
DESCEND_REFUSAL = "can't go down here"

# Reviewed prayer recovery (#759).  Verified against the production canary's
# NetHack 5.0.0 TTY (same source and nethackrc as containers/nethack-canary):
#   '#'            -> the extended-command getlin prompt (top line shows "#")
#   'pray' + Enter -> "Are you sure you want to pray? [yn] (n)"
#   'y'            -> "You begin praying ... --More--" then a 3-turn freeze
# The plain 'p' key is #pay, not #pray, so the extended command is required.
# NetHack 5.0.0 defaults paranoid_confirmation to "pray swim trap"
# (options.c), so dopray() always asks for the confirmation (pray.c).
PRAY_COMMAND_KEYS = "pray\n"
PRAY_CONFIRM_KEY = "y"
# Gehennom (deep levels) prayer cannot help and upsets the god (pray.c
# prayer_done() Inhell branch); only known shallow levels are eligible.
MAX_PRAY_DUNGEON_LEVEL = 20


def at_extended_command_prompt(obs: NethackObservation) -> bool:
    """The extended-command getlin: top line is ``#`` plus the word so far."""
    return obs.prompt == "none" and re.match(r"^#[ ]?[a-z]*$", obs.message or "") is not None


def at_pray_confirmation(obs: NethackObservation) -> bool:
    """The paranoid_confirmation prayer prompt (a whole yes/no question)."""
    return obs.prompt == "yes_no" and "are you sure you want to pray" in (obs.message or "").lower()


# Each reviewed prayer key with the frame predicate that must hold before it is
# sent.  A frame that does not match abandons the half-typed command rather
# than inventing keystrokes.
PRAY_RECOVERY_STEPS: tuple[tuple[str, Callable[[NethackObservation], bool]], ...] = (
    ("p", at_extended_command_prompt),
    ("r", at_extended_command_prompt),
    ("a", at_extended_command_prompt),
    ("y", at_extended_command_prompt),
    ("\n", at_extended_command_prompt),
    (PRAY_CONFIRM_KEY, at_pray_confirmation),
)


def severe_status_recovery_action(obs: NethackObservation) -> Action | None:
    """Return the reviewed prayer recovery action for a severe status, if safe.

    Prayer is the only recovery action on the reviewed surface so far.  It is
    deliberately withheld when the frame is not a complete turn or when the
    dungeon level is unknown/Gehennom.  The presence of an adjacent creature
    does NOT change the treatment (issue #759: a severe status kills by time
    alone, so threat avoidance is a separate decision).  Item-specific
    recovery is intentionally not guessed here; it requires a confirmed
    inventory observation and is handled separately.
    """
    if not turn_ready(obs):
        return None
    level = obs.vitals.dungeon_level
    if level is None or level > MAX_PRAY_DUNGEON_LEVEL:
        return None
    if not _has_any(obs, NethackLayeredPolicy._SEVERE_CONDITIONS):
        return None
    return Action(type="text", text=SEVERE_RECOVERY_COMMAND)

# Status conditions that dominate movement and trigger the reviewed prayer (#759).
SEVERE_CONDITIONS = frozenset({"Sick", "FoodPois", "Ill", "Slime", "Strngl", "Stone", "TermIll"})


class NethackLayeredPolicy:
    """Fail-closed layered policy over normalized public observation."""

    _SEVERE_CONDITIONS = SEVERE_CONDITIONS
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
            recovery = severe_status_recovery_action(obs)
            if recovery is not None:
                return PolicyDecision(
                    layer="strategic",
                    intent="status_recovery",
                    reason="visible severe status condition has a reviewed prayer recovery action",
                    actions=(recovery,),
                    requires_llm=True,
                )
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

        # A refused descent proves the remembered tile is stale (for example a
        # previous run's level layout). Forget it before choosing a new action
        # so the hero never re-sends '>' on a tile that is not a staircase.
        if DESCEND_REFUSAL in obs.message.lower():
            self.explorer.memory.forget_down_stairs(obs)

        # Descending is the only way a run leaves Dlvl 1 (#2666). It is
        # deliberately checked before the adjacent-creature hold: standing on
        # the down staircase is already a safe, reviewed progression action,
        # and it neither moves into the creature nor attacks it. The tile is
        # proven from exploration memory because the hero glyph replaces the
        # staircase on the current frame.
        if turn_ready(obs) and self.explorer.memory.standing_on_down_stairs(obs):
            return PolicyDecision(
                layer="midlevel",
                intent="descend_stairs",
                reason="player stands on a remembered down-staircase",
                actions=(Action(type="text", text=DESCEND_COMMAND),),
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


# Production denylist: commands that must never be generated automatically by
# the production policy, each with a human-readable reason.  Everything else
# (standard NetHack commands) is allowed; safety is enforced post-hoc via
# action trace + retrospective (#2743 requirement 3).
PRODUCTION_DENIED_COMMANDS: dict[str, str] = {
    "Q": "quit would terminate the run",
    "S": "save would suspend the run",
    "!": "shell escape is not a game command",
    "F": "force-fight bypasses peaceful/pet protection",
    "X": "explore mode bypasses normal play",
    "#quit": "extended quit would terminate the run",
    "#save": "extended save would suspend the run",
    "#!": "extended shell escape is not a game command",
}


def _production_deny_reason(text: object) -> str | None:
    # Backwards/alternative names that callers or tests may use for the same
    # denylist.
    # (aliases defined below after the dict is complete)
    if not isinstance(text, str):
        return None
    if text in PRODUCTION_DENIED_COMMANDS:
        return PRODUCTION_DENIED_COMMANDS[text]
    lowered = text.lower()
    if lowered.startswith("#wiz"):
        return "wizard-mode command is not a production action"
    return None


def assert_p3b_safe(decision: PolicyDecision) -> None:
    """Denylist gate: reject only commands with a recorded reason.

    The P3b whitelist (More-space / decline-save / one reviewed step) is
    abolished per #2743.  Standard commands are allowed; only entries in
    ``PRODUCTION_DENIED_COMMANDS`` (and wizard-mode extended commands) raise.
    """
    if not decision.actions:
        return
    for action in decision.actions:
        if getattr(action, "type", None) != "text":
            raise RuntimeError(
                "production policy attempted a non-text action outside the reviewed surface"
            )
        text = getattr(action, "text", None)
        reason = _production_deny_reason(text)
        if reason is not None:
            raise RuntimeError(
                f"production policy attempted a denied command {text!r}: {reason}"
            )


def assert_p3a_safe(decision: PolicyDecision) -> None:
    """Compatibility entrypoint (P3a). Now delegates to the denylist gate."""
    assert_p3b_safe(decision)


# Backwards/alternative names that callers or tests may use for the same
# denylist.
DENIED_COMMANDS = PRODUCTION_DENIED_COMMANDS
DENYLIST = PRODUCTION_DENIED_COMMANDS


# NetHack is turn-based: while the agent does nothing, nothing changes, so the
# same frame comes back forever. A pet-like ambiguous glyph blocking the only
# corridor or low HP that only time fixes can otherwise freeze a run for good.
# The production agent lets one turn pass with NetHack's rest command for any
# decision that produced no reviewed action (owner decision 2026-09-23:
# `.` はどんな時でも可). Only frames where a literal key could answer a
# question -- prompts, unknown screens, player-less frames -- stay fail-closed
# via turn_ready(). Severe status, every hunger tier (Hungry .. Starved) and a
# visible creature no longer block the wait itself: movement/contact keep the
# gameplay_ready gate in nethack_progress, and the resolver still tries a
# retreat then one ordinary contact before resting beside a creature. Passing
# turns can worsen hunger; that trade-off is accepted so a turn-based game
# never sits input-free. The policy's own decision remains available to
# advisory/shadow telemetry.
REST_KEY = "."
REST_HOLD_INTENTS = frozenset(
    {
        "exploration_blocked",
        "hold_low_hp",
        "hold_impaired",
    }
)

# Kept as the step-out surface (not the rest surface): movement next to a
# creature or during a food emergency is still only allowed through the
# reviewed retreat/contact paths.
REST_EMERGENCY_INTENTS = frozenset({"survival_emergency"})


def rest_action_for_hold(
    decision: PolicyDecision, obs: NethackObservation
) -> Action | None:
    """Return the explicit ``.`` wait action for any complete gameplay frame.

    The only fail-closed cases are frames where a literal key could answer a
    question instead of waiting (prompt, unknown screen, player-less frame,
    all rejected by ``turn_ready``) and decisions that already carry a
    reviewed action. Severe status, food_emergency, and an adjacent
    creature may all spend one wait turn (owner decision 2026-09-23).
    """
    if decision.actions or not turn_ready(obs):
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
    unique player glyph there is nothing to plan from.  Severe status
    (``status_emergency``/``status_recovery``) is also excluded -- it has
    its own reviewed recovery action.
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
    if step is None or not visible_safe_step(obs, step.key, getattr(explorer, "opened_doors", frozenset())):
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



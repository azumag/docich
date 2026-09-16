"""Canary-only tactical action schema (P5j).

The isolated canary is allowed to do a few things the production P3b policy
deliberately never does: attack a visible adjacent monster, open a closed door,
and answer a direction request toward a visible frontier.  This module is
imported only by :mod:`docich.nethack_canary_worker`; production NetHack
(``src/docich/agent/brains.py``) keeps using :mod:`docich.nethack_policy`
unchanged.

Every emitted action is re-checked by :func:`assert_canary_safe`, which is an
allowlist over the exact reviewed key surface.
"""
from __future__ import annotations

from dataclasses import replace

from .actions import Action
from .nethack_exploration import NethackExplorer
from .nethack_observation import NethackObservation
from .nethack_policy import NethackLayeredPolicy, PolicyDecision

# vi keys for the eight neighbours, keyed by (dx, dy).
_DIRECTION_KEY: dict[tuple[int, int], str] = {
    (0, -1): "k",
    (0, 1): "j",
    (-1, 0): "h",
    (1, 0): "l",
    (-1, -1): "y",
    (1, -1): "u",
    (-1, 1): "b",
    (1, 1): "n",
}

# Human glyphs (shopkeeper / guard / priest / quest NPC).  The isolated canary
# gains nothing from attacking one and it would only start a long guard or
# shop sequence, so they are never targeted.
_NEVER_ATTACK = frozenset({"@"})

_CLOSED_DOOR = "+"

_CARDINAL_MOVES = frozenset({"h", "j", "k", "l"})
_DIAGONAL_MOVES = frozenset({"y", "u", "b", "n"})
_ALL_MOVES = _CARDINAL_MOVES | _DIAGONAL_MOVES


def _glyph_at(obs: NethackObservation, dx: int, dy: int) -> str:
    if obs.player is None:
        return " "
    px, py = obs.player
    x, y = px + dx, py + dy
    if y < 0 or y >= len(obs.map_rows):
        return " "
    row = obs.map_rows[y]
    if x < 0 or x >= len(row):
        return " "
    return row[x]


def _attackable_neighbors(obs: NethackObservation) -> tuple[tuple[int, int, str], ...]:
    found: list[tuple[int, int, str]] = []
    for (dx, dy) in _DIRECTION_KEY:
        glyph = _glyph_at(obs, dx, dy)
        if glyph.isalpha() and glyph not in _NEVER_ATTACK:
            found.append((dx, dy, glyph))
    return tuple(found)


def _openable_neighbors(obs: NethackObservation) -> tuple[tuple[int, int, str], ...]:
    found: list[tuple[int, int, str]] = []
    for (dx, dy) in _DIRECTION_KEY:
        glyph = _glyph_at(obs, dx, dy)
        if glyph == _CLOSED_DOOR:
            found.append((dx, dy, glyph))
    return tuple(found)


class CanaryTacticalPolicy:
    """Baseline canary policy: P3b safety layers plus reviewed tactical actions."""

    def __init__(self, explorer: NethackExplorer | None = None) -> None:
        self._base = NethackLayeredPolicy(explorer)

    def decide(self, obs: NethackObservation) -> PolicyDecision:
        if obs.prompt == "more":
            return PolicyDecision(
                layer="tactical",
                intent="advance_message",
                reason="visible --More-- prompt",
                actions=(Action(type="text", text=" "),),
            )

        # NetHack asks "Really attack? [yn]" only for a peaceful monster.  The
        # canary is isolated, so the reviewed schema accepts the confirmation.
        if obs.prompt == "yes_no":
            if "really attack" in obs.raw_text.lower():
                return PolicyDecision(
                    layer="tactical",
                    intent="confirm_attack",
                    reason="canary accepts a visible Really attack? prompt",
                    actions=(Action(type="text", text="y"),),
                )
            # Any other yes/no confirmation (pick up, descend, ...) is declined;
            # the canary never accepts an unreviewed state-changing confirmation.
            return PolicyDecision(
                layer="tactical",
                intent="decline_prompt",
                reason="decline an unreviewed yes/no prompt",
                actions=(Action(type="text", text="n"),),
            )

        # Directional travel: answer a bare direction request toward the nearest
        # visible safe frontier instead of stalling on it.
        if obs.prompt == "direction":
            step = self._base.explorer.plan_step(replace(obs, prompt="none"))
            if step is not None:
                return PolicyDecision(
                    layer="tactical",
                    intent="directional_travel",
                    reason=f"answer direction prompt toward visible {step.target_glyph!r}",
                    actions=(Action(type="text", text=step.key),),
                )
            return PolicyDecision(
                layer="strategic",
                intent="prompt_decision",
                reason="direction prompt with no visible safe target",
                requires_llm=True,
            )

        decision = self._base.decide(obs)

        if decision.intent == "assess_contact":
            neighbors = _attackable_neighbors(obs)
            if neighbors:
                dx, dy, glyph = neighbors[0]
                key = _DIRECTION_KEY[(dx, dy)]
                return PolicyDecision(
                    layer="tactical",
                    intent="attack_adjacent",
                    reason=f"attack visible adjacent monster glyph={glyph!r}",
                    actions=(Action(type="text", text=key),),
                )
            # Only non-attackable glyphs (for example a human '@') are adjacent;
            # route around them instead of stalling on assess_contact.
            step = self._base.explorer.plan_step(obs)
            if step is not None:
                return PolicyDecision(
                    layer="tactical",
                    intent="explore_step",
                    reason=f"avoid non-attackable visible neighbour; {step.reason}",
                    actions=(Action(type="text", text=step.key),),
                )
            return decision

        if decision.intent in {"explore_step", "exploration_blocked"}:
            doors = _openable_neighbors(obs)
            if doors:
                dx, dy, glyph = doors[0]
                key = _DIRECTION_KEY[(dx, dy)]
                return PolicyDecision(
                    layer="tactical",
                    intent="open_door",
                    reason=f"open visible adjacent closed door {glyph!r}",
                    actions=(
                        Action(type="text", text="o"),
                        Action(type="text", text=key),
                    ),
                )

        return decision


def assert_canary_safe(decision: PolicyDecision) -> None:
    """Allow only the reviewed canary key surface; fail closed on anything else."""
    if not decision.actions:
        return
    if not all(action.type == "text" for action in decision.actions):
        raise RuntimeError("canary policy attempted a non-text action")
    keys = tuple(action.text for action in decision.actions)
    if decision.intent == "advance_message" and keys == (" ",):
        return
    if decision.intent == "confirm_attack" and keys == ("y",):
        return
    if decision.intent == "decline_prompt" and keys == ("n",):
        return
    if (
        decision.intent in {"attack_adjacent", "directional_travel", "explore_step"}
        and len(keys) == 1
        and keys[0] in _ALL_MOVES
    ):
        return
    if (
        decision.intent == "open_door"
        and len(keys) == 2
        and keys[0] == "o"
        and keys[1] in _ALL_MOVES
    ):
        return
    raise RuntimeError("canary policy attempted an action outside the reviewed canary surface")

"""Canary-only tactical action schema (P5j/P5k).

The isolated canary is allowed to do a few things the production P3b policy
deliberately never does: attack a visible adjacent monster, open a closed door,
answer a direction request toward a visible frontier, and eat a visible food
item when Hungry.  This module is imported only by
:mod:`docich.nethack_canary_worker`; production NetHack
(``src/docich/agent/brains.py``) keeps using :mod:`docich.nethack_policy`
unchanged.

Every emitted action is re-checked by :func:`assert_canary_safe`, which is an
allowlist over the exact reviewed key surface.
"""
from __future__ import annotations

import string
from dataclasses import replace

from .actions import Action
from .nethack_exploration import NethackExplorer
from .nethack_inventory import VisibleInventoryItem
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
_INVENTORY_LETTERS = frozenset(string.ascii_letters)
# Base-policy conditions that trigger seek_food / food_emergency.
_HUNGER_CONDITIONS = frozenset({"Hungry", "Weak", "Fainting", "Fainted", "Starved"})


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


def _food_item(
    inventory: tuple[VisibleInventoryItem, ...],
) -> VisibleInventoryItem | None:
    """Return a visible, unpaid-free food item, preferring simple foods.

    A closed tin cannot be eaten directly, so it is only chosen when it is the
    sole food available.
    """
    foods = [item for item in inventory if item.category_hint == "food" and not item.unpaid]
    for item in foods:
        if "tin" not in item.description.lower():
            return item
    return foods[0] if foods else None


class CanaryTacticalPolicy:
    """Baseline canary policy: P3b safety layers plus reviewed tactical actions."""

    def __init__(self, explorer: NethackExplorer | None = None) -> None:
        self._base = NethackLayeredPolicy(explorer)
        # (dungeon_level, x, y) of doors whose open attempt did not change the
        # tile; they are never retried so the canary cannot loop on a lock.
        self._failed_doors: set[tuple[int, int, int]] = set()

    def _door_key(self, obs: NethackObservation, dx: int, dy: int) -> tuple[int, int, int]:
        depth = obs.vitals.dungeon_level
        px, py = obs.player if obs.player is not None else (0, 0)
        return (depth if depth is not None else -1, px + dx, py + dy)

    def decide(
        self,
        obs: NethackObservation,
        *,
        inventory: tuple[VisibleInventoryItem, ...] = (),
    ) -> PolicyDecision:
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

        if decision.intent in {"seek_food", "food_emergency"}:
            food = _food_item(inventory)
            if food is not None:
                return PolicyDecision(
                    layer="tactical",
                    intent="eat_food",
                    reason=f"eat visible food item {food.letter!r}",
                    actions=(
                        Action(type="text", text="e"),
                        Action(type="text", text=food.letter),
                    ),
                )
            # Nothing edible is available (or the worker is on its eat
            # cooldown).  Drop the hunger priority and keep playing instead of
            # stalling on a no-action seek_food decision.
            decision = self._base.decide(
                replace(
                    obs,
                    conditions=tuple(
                        c for c in obs.conditions if c not in _HUNGER_CONDITIONS
                    ),
                )
            )

        if decision.intent in {"hold_low_hp", "survival_emergency"}:
            # P3b deliberately holds at low HP.  The isolated canary keeps
            # playing: fight an adjacent monster, otherwise rest to regenerate.
            neighbors = _attackable_neighbors(obs)
            if neighbors:
                dx, dy, glyph = neighbors[0]
                return PolicyDecision(
                    layer="tactical",
                    intent="attack_adjacent",
                    reason=f"fight visible adjacent {glyph!r} at low HP",
                    actions=(Action(type="text", text=_DIRECTION_KEY[(dx, dy)]),),
                )
            return PolicyDecision(
                layer="tactical",
                intent="rest_low_hp",
                reason="rest to regenerate at low visible HP",
                actions=(Action(type="text", text="."),),
            )

        if decision.intent == "hold_impaired":
            # Blinded/confused/stunned/hallucinating: P3b holds, the canary
            # rests so turns pass and the status can recover.
            return PolicyDecision(
                layer="tactical",
                intent="rest",
                reason="rest while movement is visibly impaired",
                actions=(Action(type="text", text="."),),
            )

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
            doors = [
                (dx, dy, glyph)
                for (dx, dy, glyph) in _openable_neighbors(obs)
                if self._door_key(obs, dx, dy) not in self._failed_doors
            ]
            if doors:
                dx, dy, glyph = doors[0]
                key = _DIRECTION_KEY[(dx, dy)]
                # A locked door stays "+"; remember the attempt so the canary
                # never loops on the same door and instead routes around it.
                self._failed_doors.add(self._door_key(obs, dx, dy))
                return PolicyDecision(
                    layer="tactical",
                    intent="open_door",
                    reason=f"open visible adjacent closed door {glyph!r}",
                    actions=(
                        Action(type="text", text="o"),
                        Action(type="text", text=key),
                    ),
                )
            if decision.intent == "exploration_blocked":
                return PolicyDecision(
                    layer="tactical",
                    intent="rest",
                    reason="rest when no safe step is visible",
                    actions=(Action(type="text", text="."),),
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
    if decision.intent in {"rest_low_hp", "rest"} and keys == (".",):
        return
    if (
        decision.intent == "eat_food"
        and len(keys) == 2
        and keys[0] == "e"
        and len(keys[1]) == 1
        and keys[1] in _INVENTORY_LETTERS
    ):
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

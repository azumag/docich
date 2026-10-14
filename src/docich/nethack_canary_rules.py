"""Shared canary rule primitives (P6).

Kept separate from both the observation normalizer and the policy so the
declarative action catalog (:mod:`docich.nethack_action_spec`) and the policy
(:mod:`docich.nethack_canary_tactics`) evaluate preconditions with exactly the
same code and cannot drift.
"""
from __future__ import annotations

import string

from .nethack_inventory import VisibleInventoryItem
from .nethack_observation import NethackObservation

# vi keys for the eight neighbours, keyed by (dx, dy).
DIRECTION_KEY: dict[tuple[int, int], str] = {
    (0, -1): "k",
    (0, 1): "j",
    (-1, 0): "h",
    (1, 0): "l",
    (-1, -1): "y",
    (1, -1): "u",
    (-1, 1): "b",
    (1, 1): "n",
}

# Human glyphs (shopkeeper / guard / priest / quest NPC).  Never attacked.
NEVER_ATTACK = frozenset({"@"})

CLOSED_DOOR = "+"

CARDINAL_MOVES = frozenset({"h", "j", "k", "l"})
DIAGONAL_MOVES = frozenset({"y", "u", "b", "n"})
ALL_MOVES = CARDINAL_MOVES | DIAGONAL_MOVES
INVENTORY_LETTERS = frozenset(string.ascii_letters)

# Base-policy conditions that trigger seek_food / food_emergency.
HUNGER_CONDITIONS = frozenset({"Hungry", "Weak", "Fainting", "Fainted", "Starved"})

# Conditions under which P3b refuses to move (P3b holds).
IMPAIRING_CONDITIONS = frozenset({"Blind", "Conf", "Stun", "Hallu"})


def glyph_at(obs: NethackObservation, dx: int, dy: int) -> str:
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


def attackable_neighbors(obs: NethackObservation) -> tuple[tuple[int, int, str], ...]:
    found: list[tuple[int, int, str]] = []
    for (dx, dy) in DIRECTION_KEY:
        glyph = glyph_at(obs, dx, dy)
        if glyph.isalpha() and glyph not in NEVER_ATTACK:
            found.append((dx, dy, glyph))
    return tuple(found)


def openable_neighbors(obs: NethackObservation) -> tuple[tuple[int, int, str], ...]:
    found: list[tuple[int, int, str]] = []
    for (dx, dy) in DIRECTION_KEY:
        if glyph_at(obs, dx, dy) == CLOSED_DOOR:
            found.append((dx, dy, CLOSED_DOOR))
    return tuple(found)


def door_key(obs: NethackObservation, dx: int, dy: int) -> tuple[int, int, int]:
    depth = obs.vitals.dungeon_level
    px, py = obs.player if obs.player is not None else (0, 0)
    return (depth if depth is not None else -1, px + dx, py + dy)


def food_item(
    inventory: tuple[VisibleInventoryItem, ...],
) -> VisibleInventoryItem | None:
    """Return a visible, unpaid-free food item, preferring simple foods."""
    foods = [item for item in inventory if item.category_hint == "food" and not item.unpaid]
    for item in foods:
        if "tin" not in item.description.lower():
            return item
    return foods[0] if foods else None

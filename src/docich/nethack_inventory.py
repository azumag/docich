"""Parse inventory-like text that is visibly shown by NetHack.

The parser never resolves an unidentified object to its hidden true identity.
It stores the description exactly as displayed and derives only presentation
hints (quantity, visible B/U/C word, equipped annotation, and a coarse category
hint from visible words).
"""
from __future__ import annotations

import re
from dataclasses import dataclass


_ENTRY_RE = re.compile(r"^\s*([a-zA-Z$])\s*(?:-|\))\s+(.+?)\s*$")
_QUANTITY_RE = re.compile(r"^(\d+)\s+(.+)$")

_EQUIPPED_MARKERS = (
    "weapon in hand",
    "being worn",
    "on left hand",
    "on right hand",
    "in quiver",
    "alternate weapon",
    "being used",
)

_CATEGORY_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("potion", ("potion",)),
    ("scroll", ("scroll",)),
    ("wand", ("wand",)),
    ("ring", ("ring",)),
    ("amulet", ("amulet",)),
    ("armor", ("armor", "mail", "cloak", "helmet", "helm", "boots", "gloves", "shield")),
    ("weapon", ("sword", "dagger", "axe", "spear", "bow", "arrow", "mace", "club", "whip", "blade")),
    ("food", ("ration", "food", "corpse", "tin", "apple", "orange", "melon", "carrot", "egg")),
    ("tool", ("lamp", "key", "pick-axe", "pickaxe", "mirror", "whistle", "bag", "box", "chest", "towel")),
    ("gem", ("gem", "stone")),
)


@dataclass(frozen=True)
class VisibleInventoryItem:
    letter: str
    description: str
    quantity: int | None
    buc: str
    equipped: bool
    unpaid: bool
    category_hint: str

    def public_dict(self) -> dict[str, object]:
        return {
            "letter": self.letter,
            "description": self.description,
            "quantity": self.quantity,
            "buc": self.buc,
            "equipped": self.equipped,
            "unpaid": self.unpaid,
            "category_hint": self.category_hint,
        }


def _visible_buc(description: str) -> str:
    lower = description.lower()
    for value in ("blessed", "uncursed", "cursed"):
        if re.search(rf"\b{value}\b", lower):
            return value
    return "unknown"


def _category_hint(description: str) -> str:
    lower = description.lower()
    for category, words in _CATEGORY_WORDS:
        if any(re.search(rf"\b{re.escape(word)}s?\b", lower) for word in words):
            return category
    return "unknown"


def _quantity(description: str) -> int | None:
    match = _QUANTITY_RE.match(description)
    if match:
        return int(match.group(1))
    # Singular articles are visibly a quantity of one, but avoid treating
    # proper names/descriptions without an article as known quantity.
    if re.match(r"^(?:an?|the)\s+", description, re.IGNORECASE):
        return 1
    return None


def parse_visible_inventory(text: str) -> tuple[VisibleInventoryItem, ...]:
    """Return only entries which visibly carry an inventory letter.

    Headers, prompts, status lines, and prose are ignored. Duplicate letters in
    one captured screen are ambiguous, so the first visible entry wins and the
    parser does not merge potentially different menu pages.
    """
    items: list[VisibleInventoryItem] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        match = _ENTRY_RE.match(raw_line)
        if not match:
            continue
        letter, description = match.group(1), match.group(2).strip()
        if letter in seen or not description:
            continue
        seen.add(letter)
        lower = description.lower()
        items.append(
            VisibleInventoryItem(
                letter=letter,
                description=description,
                quantity=_quantity(description),
                buc=_visible_buc(description),
                equipped=any(marker in lower for marker in _EQUIPPED_MARKERS),
                unpaid="unpaid" in lower,
                category_hint=_category_hint(description),
            )
        )
    return tuple(items)


def inventory_public_summary(items: tuple[VisibleInventoryItem, ...]) -> list[dict[str, object]]:
    return [item.public_dict() for item in items]

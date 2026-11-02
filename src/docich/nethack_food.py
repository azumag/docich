"""Conservative food selection for the production NetHack hunger policy.

Issue #2806 needs a production path from a *visible* inventory snapshot to an
edible item.  Keep that choice separate from inventory probing/action dispatch
so hidden identity is never inferred and the caller can remain fail-closed.
"""
from __future__ import annotations

from .nethack_inventory import VisibleInventoryItem

# Only descriptions whose visible identity is safe without freshness/species
# knowledge.  Corpse/egg/tin deliberately stay out: those need additional
# context before production may consume them.
_SAFE_VISIBLE_FOOD_WORDS = (
    "food ration",
    "cram ration",
    "k-ration",
    "c-ration",
    "lembas wafer",
    "pancake",
    "fortune cookie",
    "cream pie",
    "candy bar",
    "apple",
    "orange",
    "melon",
    "carrot",
    "banana",
    "kelp frond",
    "eucalyptus leaf",
)


def production_food_item(
    inventory: tuple[VisibleInventoryItem, ...],
) -> VisibleInventoryItem | None:
    """Choose only a visibly identified, unpaid, conservative food item.

    Inventory letters and identities come exclusively from the rendered
    inventory.  Unknown food, corpses, eggs, tins, and unpaid goods are not
    guessed through; returning ``None`` is the intended fail-closed result.
    """
    for item in inventory:
        if item.unpaid or item.category_hint != "food":
            continue
        description = item.description.lower()
        if any(word in description for word in _SAFE_VISIBLE_FOOD_WORDS):
            return item
    return None

from docich.nethack_food import production_food_item
from docich.nethack_inventory import parse_visible_inventory


def items(*lines: str):
    return parse_visible_inventory("\n".join(lines) + "\n")


def test_prefers_first_visibly_safe_paid_food():
    inventory = items(
        "a - a lichen corpse",
        "b - an uncursed food ration",
        "c - an apple",
    )
    chosen = production_food_item(inventory)
    assert chosen is not None
    assert chosen.letter == "b"


def test_rejects_unpaid_food():
    inventory = items("f - an uncursed food ration (unpaid, 45 zorkmids)")
    assert production_food_item(inventory) is None


def test_rejects_food_needing_extra_safety_context():
    inventory = items(
        "a - a lichen corpse",
        "b - an egg",
        "c - a tin",
    )
    assert production_food_item(inventory) is None


def test_does_not_guess_unknown_item_identity():
    inventory = items("x - a gray stone")
    assert production_food_item(inventory) is None


def test_accepts_simple_visible_fruit():
    inventory = items("q - a carrot")
    chosen = production_food_item(inventory)
    assert chosen is not None
    assert chosen.letter == "q"

from __future__ import annotations

import unittest

from docich.nethack_inventory import parse_visible_inventory


class TestVisibleInventory(unittest.TestCase):
    def test_parses_visible_letters_without_resolving_hidden_identity(self) -> None:
        text = """
Weapons
 a - a blessed +1 long sword (weapon in hand)
Comestibles
 b - 2 uncursed food rations
Potions
 c - a potion called cloudy
Scrolls
 d - an unpaid scroll labeled ELBIB YLOH
HP:10(10) Dlvl:2
"""
        items = parse_visible_inventory(text)
        self.assertEqual([item.letter for item in items], ["a", "b", "c", "d"])

        sword, food, potion, scroll = items
        self.assertEqual(sword.buc, "blessed")
        self.assertTrue(sword.equipped)
        self.assertEqual(sword.quantity, 1)
        self.assertEqual(sword.category_hint, "weapon")

        self.assertEqual(food.quantity, 2)
        self.assertEqual(food.buc, "uncursed")
        self.assertEqual(food.category_hint, "food")

        self.assertEqual(potion.description, "a potion called cloudy")
        self.assertEqual(potion.buc, "unknown")
        self.assertEqual(potion.category_hint, "potion")
        # No hidden true identity field exists by contract.
        self.assertNotIn("true_identity", potion.public_dict())

        self.assertTrue(scroll.unpaid)
        self.assertEqual(scroll.category_hint, "scroll")

    def test_ignores_headers_prompts_and_duplicate_letters(self) -> None:
        text = """
Inventory:
 a - a dagger
What do you want to use? [a-z ?*]
 a - a second impossible duplicate
 --More--
"""
        items = parse_visible_inventory(text)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].description, "a dagger")

    def test_unknown_visible_description_stays_unknown(self) -> None:
        items = parse_visible_inventory(" z - a mysterious object\n")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].category_hint, "unknown")
        self.assertEqual(items[0].buc, "unknown")


if __name__ == "__main__":
    unittest.main()

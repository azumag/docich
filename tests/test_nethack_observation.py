from __future__ import annotations

import unittest

from docich.nethack_observation import normalize_tty


class TestNethackObservation(unittest.TestCase):
    def test_visible_status_and_local_map_are_normalized(self) -> None:
        text = (
            "You see here a potion.\n"
            "..@d....\n"
            "..#.....\n"
            "Dlvl:3 $:42 HP:5(20) Pw:7(10) AC:2 Exp:4\n"
            "T:123 Hungry Blind\n"
        )
        obs = normalize_tty(text, cols=80, rows=5)
        self.assertEqual(obs.message, "You see here a potion.")
        self.assertEqual(obs.player, (2, 0))
        self.assertEqual(obs.vitals.hp, 5)
        self.assertEqual(obs.vitals.hp_max, 20)
        self.assertEqual(obs.vitals.hp_ratio, 0.25)
        self.assertEqual(obs.vitals.power, 7)
        self.assertEqual(obs.vitals.power_max, 10)
        self.assertEqual(obs.vitals.ac, 2)
        self.assertEqual(obs.vitals.experience_level, 4)
        self.assertEqual(obs.vitals.dungeon_level, 3)
        self.assertEqual(obs.vitals.gold, 42)
        self.assertEqual(obs.vitals.turn, 123)
        self.assertEqual(obs.conditions, ("Hungry", "Blind"))
        self.assertEqual(obs.prompt, "none")
        self.assertIn("d", obs.visible_neighbors())

        summary = obs.public_summary()
        self.assertEqual(summary["message"], "You see here a potion.")
        self.assertEqual(summary["vitals"]["hp"], 5)
        self.assertEqual(summary["conditions"], ["Hungry", "Blind"])
        self.assertEqual(len(summary["local_map"]), 5)
        # The summary contains only visible terminal-derived fields; it has no
        # monster identity, item identity or unseen-map key.
        self.assertNotIn("monster_identity", summary)
        self.assertNotIn("identified_items", summary)
        self.assertNotIn("unseen_map", summary)

    def test_prompt_types_are_visible_only(self) -> None:
        base = "\n.@..\n....\nHP:10(10) Pw:3(3) AC:5 Exp:1\nDlvl:1 T:2\n"
        cases = {
            "You hit it. --More--": "more",
            "In what direction?": "direction",
            "Really attack? [yn]": "yes_no",
            "What do you want to drink?": "selection",
            "Call a potion:": "text",
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                obs = normalize_tty(message + base, cols=80, rows=5)
                self.assertEqual(obs.prompt, expected)

    def test_ambiguous_player_glyph_fails_soft(self) -> None:
        text = (
            "msg\n"
            ".@.@....\n"
            "........\n"
            "HP:10(10) Pw:3(3) AC:5 Exp:1\n"
            "Dlvl:1 T:2\n"
        )
        obs = normalize_tty(text, cols=80, rows=5)
        self.assertIsNone(obs.player)
        self.assertEqual(obs.local_map(), ())
        self.assertEqual(obs.visible_neighbors(), ())

    def test_invalid_local_radius_is_rejected(self) -> None:
        obs = normalize_tty("", cols=8, rows=5)
        with self.assertRaises(ValueError):
            obs.local_map(-1)
        with self.assertRaises(ValueError):
            obs.local_map(9)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import textwrap
import unittest

import pytest

from docich.nethack_observation import normalize_tty
from docich.nethack_policy import decline_prompt


def tty_layout(*message_rows: str, map_fragment: str = "") -> str:
    """80x24 capture with a stale, playable map behind the message window."""
    lines = [" " * 80 for _ in range(24)]
    lines[10] = " " * 20 + map_fragment
    lines[14] = " " * 38 + "|...|"
    lines[15] = " " * 38 + "|.@.|"
    lines[16] = " " * 38 + "|..#|"
    lines[17] = " " * 38 + "-----"
    lines[22] = "Docich the Stripling St:17 Dx:12 Co:18 In:7 Wi:11 Ch:8 Lawful"
    lines[23] = "Dlvl:1 $:0 HP:16(16) Pw:2(2) AC:6 Xp:1 T:12"
    lines[:len(message_rows)] = message_rows
    assert all(len(line) <= 80 for line in lines)
    return "\n".join(line.ljust(80) for line in lines)


@pytest.mark.parametrize("message,expected,decline", [
    ("You hit the goblin. --More--", "more", None),
    ("Really save? [yn] (n)", "yes_no", "decline_save"),
    ("Really attack the kitten? [yn] (n)", "yes_no", "decline_attack"),
    ("In what direction?", "direction", None),
    ("What do you want to drink? [a-z or ?*]", "selection", None),
    ("Call a potion:", "text", None),
    ("Name an individual object:", "text", None),
    ("Unknown question?", "unknown", None),
])
def test_top_message_region_in_full_tty_layout(message, expected, decline):
    # Map glyphs must not override a direction, selection or More prompt.
    obs = normalize_tty(tty_layout(message, map_fragment="|.[yn.|"))
    assert obs.prompt == expected
    assert decline_prompt(obs) == decline
    assert obs.player == (40, 14)


@pytest.mark.parametrize("message", ["", "You see here a potion."])
@pytest.mark.parametrize("fragment", [
    "|.[yn.|", "|.(y/n).|", "|.yes or no.|", "|.--More--.|",
    "|.In what direction.|", "|.pick an object.|", "|.(end).|",
])
def test_map_glyphs_are_not_prompt_text(message, fragment):
    obs = normalize_tty(tty_layout(message, map_fragment=fragment))
    assert obs.prompt == "none"
    assert fragment in obs.map_rows[9]
    assert obs.player == (40, 14)


@pytest.mark.parametrize("length", [60, 73, 78])
@pytest.mark.parametrize("map_row", ["-" * 80, "#" * 80, "dfyn" * 20, "[yn" * 26])
def test_long_normal_message_does_not_use_next_map_row_as_wrap_evidence(length, map_row):
    message = "You see " + "x" * (length - len("You see .")) + "."
    assert len(message) == length
    obs = normalize_tty(tty_layout(message, map_row))
    assert obs.prompt == "none"
    assert obs.map_rows[0].rstrip() == map_row
    assert obs.player == (40, 14)


@pytest.mark.parametrize("message", ["Really", "Really save", "Really attack the kitten", "Would you like to inspect"])
@pytest.mark.parametrize("map_row", ["", "-" * 80, "#" * 80, "dfyn" * 20])
def test_incomplete_question_stem_holds_independent_of_next_row(message, map_row):
    obs = normalize_tty(tty_layout(message, map_row))
    assert obs.prompt == "unknown"
    assert decline_prompt(obs) is None


@pytest.mark.parametrize("message", [
    "Would you like to inspect " + "this unusual object " * 5 + "before continuing?",
    "Really attack the " + "very " * 20 + "peaceful kitten? [yn] (n)",
    " " * 68 + "Really save? [yn] (n)",
])
@pytest.mark.parametrize("wrap", ["hard", "word"])
def test_wrapped_questions_in_full_tty_are_unknown_not_answers(message, wrap):
    message_rows = (
        [message[i:i + 80] for i in range(0, len(message), 80)]
        if wrap == "hard" else textwrap.wrap(message, width=80)
    )
    assert len(message_rows) > 1
    obs = normalize_tty(tty_layout(*message_rows))
    assert obs.prompt == "unknown"
    assert decline_prompt(obs) is None
    assert obs.player == (40, 14)  # wrapped text must not shift map coordinates


def test_hard_wrap_without_recognizable_question_stem_is_unknown():
    # The only question mark is on the second row. The top row's width, not
    # the following glyphs or question vocabulary, is the blocking evidence.
    obs = normalize_tty(tty_layout("An unusual object " + "x" * 62, "continue?"))
    assert len(obs.message) == 80
    assert obs.prompt == "unknown"


def test_short_split_save_is_not_reconstructed_into_an_allowed_answer():
    obs = normalize_tty(tty_layout("Really save?", "[yn] (n)"))
    assert obs.prompt == "unknown"
    assert decline_prompt(obs) is None


def test_truncated_joined_unknown_question_is_not_gameplay():
    message = "Would you like to inspect " + "this unusual object " * 5 + "before continuing?"
    text = message + "\n" + tty_layout().split("\n", 1)[1]
    obs = normalize_tty(text)
    assert "?" not in obs.message  # beyond the captured first-row width
    assert obs.prompt == "unknown"


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

    def test_status_block_is_found_without_the_legacy_two_trailing_rows(self) -> None:
        # Real 3.6.7 frame: the status row is second-to-last because a message
        # and a trailing blank row follow it, and the row above the map is the
        # message the map was drawn with. The player must still be seen and the
        # vitals parsed.
        text = (
            "Really save? [yn] (n)\n"
            "------------\n"
            "|$....f.....|\n"
            "|......@...|\n"
            "|..........|\n"
            "------------\n"
            "[Docich the Stripling ] St:17 Dx:12 Co:18 In:7 Wi:11 Ch:8 Lawful\n"
            "Dlvl:1 $:0 HP:16(16) Pw:2(2) AC:6 Xp:1\n"
        )
        obs = normalize_tty(text, cols=80, rows=24)
        self.assertEqual(obs.player, (7, 2))
        self.assertEqual(obs.vitals.hp, 16)
        self.assertEqual(obs.vitals.hp_max, 16)
        self.assertEqual(obs.vitals.dungeon_level, 1)
        self.assertIn("Dlvl:1", obs.status_lines[0])
        # The map starts below the message row and stops before the status row.
        self.assertTrue(any("|$....f.....|" in row for row in obs.map_rows))

    def test_invalid_local_radius_is_rejected(self) -> None:
        obs = normalize_tty("", cols=8, rows=5)
        with self.assertRaises(ValueError):
            obs.local_map(-1)
        with self.assertRaises(ValueError):
            obs.local_map(9)


if __name__ == "__main__":
    unittest.main()

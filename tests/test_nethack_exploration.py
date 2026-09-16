from __future__ import annotations

import unittest

from docich.actions import Action
from docich.nethack_exploration import NethackExplorer, PASSABLE
from docich.nethack_observation import normalize_tty
from docich.nethack_policy import NethackLayeredPolicy, PolicyDecision, assert_p3b_safe


def obs(map_rows: tuple[str, str], *, hp="10(10)", condition=""):
    text = (
        "msg\n"
        f"{map_rows[0]}\n"
        f"{map_rows[1]}\n"
        f"Dlvl:2 HP:{hp} Pw:4(4) AC:5 Exp:2\n"
        f"T:12 {condition}\n"
    )
    # Real NetHack TTY uses 80 columns. Keep the status line intact here;
    # shrinking cols for a compact map would truncate HP/condition evidence.
    return normalize_tty(text, cols=80, rows=5)


class TestNethackExplorer(unittest.TestCase):
    def test_step_is_only_onto_visible_passable_cardinal_cell(self) -> None:
        observation = obs(("###@.      ", "           "))
        step = NethackExplorer().plan_step(observation)
        self.assertIsNotNone(step)
        assert step is not None
        self.assertIn(step.key, {"h", "j", "k", "l"})
        self.assertIn(step.target_glyph, PASSABLE)
        self.assertEqual(step.source, observation.player)

    def test_creature_item_trap_door_and_unknown_are_never_targets(self) -> None:
        # Player has a single safe left corridor. Right/vertical neighbors are
        # creature/item/unknown; the visible trap/door are also non-passable.
        observation = obs(("#@d!+^     ", "            "))
        step = NethackExplorer().plan_step(observation)
        self.assertIsNotNone(step)
        assert step is not None
        self.assertEqual(step.key, "h")
        self.assertEqual(step.target_glyph, "#")
        self.assertNotIn(step.target_glyph, {"d", "!", "+", "^", " "})

    def test_no_safe_neighbor_returns_none(self) -> None:
        observation = obs(("d@!+^      ", "            "))
        self.assertIsNone(NethackExplorer().plan_step(observation))

    def test_visit_memory_changes_tie_breaking(self) -> None:
        explorer = NethackExplorer()
        first = obs(("..@..      ", "            "))
        step1 = explorer.plan_step(first)
        self.assertIsNotNone(step1)
        assert step1 is not None
        # Simulate that the chosen cell became the next player location while
        # the same visible corridor remains.
        row = list(".....      ")
        row[step1.target[0]] = "@"
        second = obs(("".join(row), "            "))
        explorer.plan_step(second)
        depth = second.vitals.dungeon_level
        assert depth is not None
        memory = explorer.memory.level(depth)
        self.assertGreaterEqual(memory.visits.get(step1.target, 0), 1)


class TestP3bPolicy(unittest.TestCase):
    def test_normal_state_can_emit_one_reviewed_exploration_step(self) -> None:
        decision = NethackLayeredPolicy().decide(obs(("###@.      ", "            ")))
        self.assertEqual(decision.intent, "explore_step")
        self.assertEqual(len(decision.actions), 1)
        self.assertIn(decision.actions[0].text, {"h", "j", "k", "l"})
        assert_p3b_safe(decision)

    def test_low_hp_and_impairment_hold_position(self) -> None:
        low = NethackLayeredPolicy().decide(
            obs(("###@.      ", "            "), hp="4(10)")
        )
        self.assertEqual(low.intent, "hold_low_hp")
        self.assertEqual(low.actions, ())

        blind = NethackLayeredPolicy().decide(
            obs(("###@.      ", "            "), condition="Blind")
        )
        self.assertEqual(blind.intent, "hold_impaired")
        self.assertEqual(blind.actions, ())

    def test_adjacent_creature_still_blocks_explorer(self) -> None:
        decision = NethackLayeredPolicy().decide(obs(("##@d.      ", "            ")))
        self.assertEqual(decision.intent, "assess_contact")
        self.assertEqual(decision.actions, ())

    def test_p3b_guard_rejects_attack_or_item_key(self) -> None:
        for key in ("a", "q", ">", "o"):
            with self.subTest(key=key):
                bad = PolicyDecision(
                    layer="midlevel",
                    intent="explore_step",
                    reason="bad",
                    actions=(Action(type="text", text=key),),
                )
                with self.assertRaises(RuntimeError):
                    assert_p3b_safe(bad)


if __name__ == "__main__":
    unittest.main()

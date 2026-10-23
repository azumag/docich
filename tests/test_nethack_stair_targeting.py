from __future__ import annotations

import unittest

from docich.nethack_exploration import NethackExplorer
from docich.nethack_observation import normalize_tty
from docich.nethack_policy import NethackLayeredPolicy, assert_p3b_safe


def observation(map1: str, map2: str = "           "):
    return normalize_tty(
        "msg\n"
        f"{map1}\n"
        f"{map2}\n"
        "Dlvl:1 HP:10(10) Pw:4(4) AC:5 Exp:1\n"
        "T:12\n",
        cols=80,
        rows=5,
    )


class TestDownStairTargeting(unittest.TestCase):
    def test_visible_reachable_down_stair_beats_generic_frontier(self) -> None:
        obs = observation(".@.>.      ")
        step = NethackExplorer().plan_step(obs)

        self.assertIsNotNone(step)
        assert step is not None
        self.assertEqual(step.key, "l")
        self.assertEqual(step.target, (2, 0))
        self.assertEqual(step.reason, "reachable visible down-staircase")

    def test_policy_walks_to_seen_stair_then_uses_reviewed_descend(self) -> None:
        policy = NethackLayeredPolicy()

        first = policy.decide(observation(".@.>.      "))
        self.assertEqual(first.intent, "explore_step")
        self.assertEqual([action.text for action in first.actions], ["l"])
        self.assertIn("reachable visible down-staircase", first.reason)
        assert_p3b_safe(first)

        second = policy.decide(observation("..@>.      "))
        self.assertEqual(second.intent, "explore_step")
        self.assertEqual([action.text for action in second.actions], ["l"])
        self.assertIn("reachable visible down-staircase", second.reason)
        assert_p3b_safe(second)

        # The hero glyph now hides the staircase. The memory populated by the
        # preceding frames is the reviewed evidence used by #2672.
        on_stairs = policy.decide(observation("...@.      "))
        self.assertEqual(on_stairs.intent, "descend_stairs")
        self.assertEqual([action.text for action in on_stairs.actions], [">"])
        assert_p3b_safe(on_stairs)

    def test_stale_remembered_stair_does_not_attract_exploration(self) -> None:
        explorer = NethackExplorer()
        explorer.plan_step(observation(".@.>.      "))

        # The same coordinate is now visibly ordinary floor. Keep the stale
        # memory for #2672's bounded refusal handling, but do not route toward
        # it unless the current frame still proves a visible staircase.
        step = explorer.plan_step(observation(".@...      "))

        self.assertIsNotNone(step)
        assert step is not None
        self.assertNotEqual(step.reason, "reachable visible down-staircase")


if __name__ == "__main__":
    unittest.main()

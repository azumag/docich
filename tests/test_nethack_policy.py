from __future__ import annotations

import unittest
from types import SimpleNamespace

from docich.actions import Action
from docich.adapters.base import Observation
from docich.agent.brains import NethackPolicyBrain, build_brain
from docich.nethack_observation import normalize_tty
from docich.nethack_policy import (
    NethackLayeredPolicy,
    PolicyDecision,
    REST_EMERGENCY_INTENTS,
    assert_p3b_safe,
    rest_action_for_hold,
    step_out_of_hold,
)


def frame(message: str = "", *, hp: str = "10(10)", condition: str = "", map1: str = ".@....") -> str:
    return (
        f"{message}\n"
        f"{map1}\n"
        "......\n"
        f"Dlvl:2 $:5 HP:{hp} Pw:4(4) AC:5 Exp:2\n"
        f"T:12 {condition}\n"
    )


class TestNethackLayeredPolicy(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = NethackLayeredPolicy()

    def decide(self, text: str):
        return self.policy.decide(normalize_tty(text, cols=80, rows=5))

    def test_more_remains_safe_tactical_auto_action(self) -> None:
        decision = self.decide(frame("You hit the goblin. --More--"))
        self.assertEqual(decision.layer, "tactical")
        self.assertEqual(decision.intent, "advance_message")
        self.assertFalse(decision.requires_llm)
        self.assertEqual(len(decision.actions), 1)
        self.assertEqual(decision.actions[0].type, "text")
        self.assertEqual(decision.actions[0].text, " ")
        assert_p3b_safe(decision)

    def test_critical_hp_escalates_without_keypress(self) -> None:
        decision = self.decide(frame(hp="2(10)"))
        self.assertEqual(decision.layer, "strategic")
        self.assertEqual(decision.intent, "survival_emergency")
        self.assertTrue(decision.requires_llm)
        self.assertEqual(decision.actions, ())

    def test_severe_status_uses_reviewed_prayer_recovery_when_safe(self) -> None:
        self.assertEqual(REST_EMERGENCY_INTENTS, frozenset({"survival_emergency"}))
        for condition in ("Sick", "FoodPois", "Ill", "Slime", "Strngl"):
            with self.subTest(condition=condition):
                observation = normalize_tty(frame(condition=condition), cols=80, rows=5)
                decision = self.policy.decide(observation)
                self.assertEqual(decision.layer, "strategic")
                self.assertEqual(decision.intent, "status_recovery")
                self.assertTrue(decision.requires_llm)
                self.assertEqual([a.text for a in decision.actions], ["#"])
                self.assertIsNone(rest_action_for_hold(decision, observation))

    def test_prompt_escalates_without_guessing_answer(self) -> None:
        decision = self.decide(frame("Really quit? [yn]"))
        self.assertEqual(decision.layer, "strategic")
        self.assertEqual(decision.intent, "prompt_decision")
        self.assertEqual(decision.actions, ())

    def test_save_prompt_is_declined_to_keep_the_run_going(self) -> None:
        decision = self.decide(frame("Really save? [yn] (n)"))
        self.assertEqual(decision.layer, "tactical")
        self.assertEqual(decision.intent, "decline_save")
        self.assertFalse(decision.requires_llm)
        self.assertEqual([a.text for a in decision.actions], ["n"])
        assert_p3b_safe(decision)

    def test_descends_only_from_a_remembered_down_staircase(self) -> None:
        # The '>' is seen one frame before the hero steps on it; the current
        # frame only shows '@' because the hero glyph hides the terrain.
        self.policy.decide(normalize_tty(frame(map1="..@>.."), cols=80, rows=5))
        on_stairs = normalize_tty(frame(map1="...@.."), cols=80, rows=5)
        decision = self.policy.decide(on_stairs)
        self.assertEqual(decision.layer, "midlevel")
        self.assertEqual(decision.intent, "descend_stairs")
        self.assertFalse(decision.requires_llm)
        self.assertEqual([action.text for action in decision.actions], [">"])
        assert_p3b_safe(decision)
        # The production resolver leaves the reviewed descend action intact.
        from docich.nethack_progress import NethackProgressResolver
        resolver = NethackProgressResolver()
        resolved = resolver.resolve(decision, on_stairs, self.policy.explorer)
        self.assertEqual(resolved.intent, "descend_stairs")
        self.assertEqual([action.text for action in resolved.actions], [">"])

    def test_does_not_descend_without_a_remembered_stair_tile(self) -> None:
        on_floor = normalize_tty(frame(map1="...@.."), cols=80, rows=5)
        decision = self.policy.decide(on_floor)
        self.assertNotEqual(decision.intent, "descend_stairs")

    def test_stair_memory_is_per_dungeon_level(self) -> None:
        self.policy.decide(normalize_tty(frame(map1="..@>.."), cols=80, rows=5))
        deeper = frame(map1="...@..").replace("Dlvl:2", "Dlvl:3")
        decision = self.policy.decide(normalize_tty(deeper, cols=80, rows=5))
        self.assertNotEqual(decision.intent, "descend_stairs")

    def test_refused_descent_forgets_the_tile_and_is_not_retried(self) -> None:
        self.policy.decide(normalize_tty(frame(map1="..@>.."), cols=80, rows=5))
        refused = normalize_tty(frame("You can't go down here.", map1="...@.."),
                                cols=80, rows=5)
        self.assertNotEqual(self.policy.decide(refused).intent, "descend_stairs")
        after = normalize_tty(frame(map1="...@.."), cols=80, rows=5)
        self.assertNotEqual(self.policy.decide(after).intent, "descend_stairs")

    def test_production_safety_rejects_descend_off_the_stair_tile(self) -> None:
        from docich.nethack_progress import assert_production_safe
        on_stairs = normalize_tty(frame(map1="...@.."), cols=80, rows=5)
        decision = PolicyDecision(
            "midlevel", "descend_stairs", "remembered staircase",
            (Action(type="text", text=">"),),
        )
        assert_production_safe(decision, on_stairs, down_stairs={on_stairs.player})
        with self.assertRaises(RuntimeError):
            assert_production_safe(decision, on_stairs, down_stairs={(99, 99)})
        prompting = normalize_tty(frame("Really attack the goblin? [yn]", map1="...@.."),
                                  cols=80, rows=5)
        with self.assertRaises(RuntimeError):
            assert_production_safe(decision, prompting, down_stairs={prompting.player})

    def test_real_frame_still_explores_after_the_save_prompt(self) -> None:
        # Full 3.6.7 frame, status not on the last two rows, save prompt gone.
        text = (
            "------------\n"
            "|$....f.....|\n"
            "|......@...|\n"
            "|..........|\n"
            "------------\n"
            "[Docich the Stripling ] St:17 Dx:12 Co:18 In:7 Wi:11 Ch:8 Lawful\n"
            "Dlvl:1 $:0 HP:16(16) Pw:2(2) AC:6 Xp:1\n"
        )
        obs = normalize_tty(text, cols=80, rows=24)
        self.assertIsNotNone(obs.player)
        decision = self.policy.decide(obs)
        # The adjacent f is a feline, not terrain. Preserve the policy hold
        # for observers while the reviewed fallback retreats on visible floor.
        self.assertEqual(decision.intent, "assess_contact")
        self.assertEqual(decision.actions, ())
        step = step_out_of_hold(decision, obs, self.policy.explorer)
        self.assertIsNotNone(step)
        self.assertIn(step.text, {"h", "j", "k", "l"})
        assert_p3b_safe(decision)

    def test_visible_hunger_changes_midlevel_priority(self) -> None:
        decision = self.decide(frame(condition="Hungry"))
        self.assertEqual(decision.layer, "midlevel")
        self.assertEqual(decision.intent, "seek_food")
        self.assertEqual(decision.actions, ())

    def test_adjacent_creature_is_not_attacked_automatically(self) -> None:
        decision = self.decide(frame(map1=".@d..."))
        self.assertEqual(decision.layer, "midlevel")
        self.assertEqual(decision.intent, "assess_contact")
        self.assertEqual(decision.actions, ())

    def test_clear_screen_can_emit_one_reviewed_exploration_step(self) -> None:
        decision = self.decide(frame())
        self.assertEqual(decision.layer, "midlevel")
        self.assertEqual(decision.intent, "explore_step")
        self.assertEqual(len(decision.actions), 1)
        self.assertIn(decision.actions[0].text, {"h", "j", "k", "l"})
        assert_p3b_safe(decision)

    def test_p3b_safety_guard_rejects_unreviewed_action_surface(self) -> None:
        decision = PolicyDecision(
            layer="tactical",
            intent="attack",
            reason="bad regression",
            actions=(Action(type="text", text="Q"),),
        )
        with self.assertRaises(RuntimeError):
            assert_p3b_safe(decision)


class TestNethackPolicyBrain(unittest.TestCase):
    def _game(self, *, brain="nethack", name="nethack", adapter="cli"):
        return SimpleNamespace(
            name=name,
            adapter=adapter,
            raw={"cli": {"cols": 80, "rows": 5}},
            agent=SimpleNamespace(brain=brain, command=""),
        )

    def test_brain_builds_more_and_reviewed_exploration_actions(self) -> None:
        brain = build_brain(SimpleNamespace(), self._game())
        self.assertIsInstance(brain, NethackPolicyBrain)
        obs = Observation(
            game="nethack",
            title="NetHack",
            adapter="cli",
            ts=1.0,
            kind="text",
            text=frame("message --More--"),
        )
        actions = brain.decide(obs)
        self.assertEqual([(a.type, a.text) for a in actions], [("text", " ")])
        self.assertEqual(brain.last_decision.intent, "advance_message")

        obs2 = Observation(
            game="nethack",
            title="NetHack",
            adapter="cli",
            ts=2.0,
            kind="text",
            text=frame(),
        )
        actions2 = brain.decide(obs2)
        self.assertEqual(len(actions2), 1)
        self.assertEqual(actions2[0].type, "text")
        self.assertIn(actions2[0].text, {"h", "j", "k", "l"})
        self.assertEqual(brain.last_decision.intent, "explore_step")

    def test_brain_uses_reviewed_prayer_recovery_on_severe_status(self) -> None:
        brain = build_brain(SimpleNamespace(), self._game())
        obs = Observation(
            game="nethack",
            title="NetHack",
            adapter="cli",
            ts=1.0,
            kind="text",
            text=frame(condition="Sick"),
        )
        self.assertEqual([a.text for a in brain.decide(obs)], ["#"])
        self.assertEqual(brain.last_decision.intent, "status_recovery")

    def test_brain_is_restricted_to_cli_nethack(self) -> None:
        from docich.adapters import AdapterError

        with self.assertRaises(AdapterError):
            NethackPolicyBrain(SimpleNamespace(), self._game(name="robots"))
        with self.assertRaises(AdapterError):
            NethackPolicyBrain(SimpleNamespace(), self._game(adapter="browser"))


if __name__ == "__main__":
    unittest.main()


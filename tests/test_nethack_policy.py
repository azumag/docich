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
    assert_p3a_safe,
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

    def test_more_is_only_p3a_auto_action(self) -> None:
        decision = self.decide(frame("You hit the goblin. --More--"))
        self.assertEqual(decision.layer, "tactical")
        self.assertEqual(decision.intent, "advance_message")
        self.assertFalse(decision.requires_llm)
        self.assertEqual(len(decision.actions), 1)
        self.assertEqual(decision.actions[0].type, "text")
        self.assertEqual(decision.actions[0].text, " ")
        assert_p3a_safe(decision)

    def test_critical_hp_escalates_without_keypress(self) -> None:
        decision = self.decide(frame(hp="2(10)"))
        self.assertEqual(decision.layer, "strategic")
        self.assertEqual(decision.intent, "survival_emergency")
        self.assertTrue(decision.requires_llm)
        self.assertEqual(decision.actions, ())

    def test_prompt_escalates_without_guessing_answer(self) -> None:
        decision = self.decide(frame("Really attack? [yn]"))
        self.assertEqual(decision.layer, "strategic")
        self.assertEqual(decision.intent, "prompt_decision")
        self.assertEqual(decision.actions, ())

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

    def test_clear_screen_requests_exploration_but_does_not_move_yet(self) -> None:
        decision = self.decide(frame())
        self.assertEqual(decision.layer, "midlevel")
        self.assertEqual(decision.intent, "explore")
        self.assertEqual(decision.actions, ())

    def test_p3a_safety_guard_rejects_unreviewed_action_surface(self) -> None:
        decision = PolicyDecision(
            layer="tactical",
            intent="attack",
            reason="bad regression",
            actions=(Action(type="text", text="h"),),
        )
        with self.assertRaises(RuntimeError):
            assert_p3a_safe(decision)


class TestNethackPolicyBrain(unittest.TestCase):
    def _game(self, *, brain="nethack", name="nethack", adapter="cli"):
        return SimpleNamespace(
            name=name,
            adapter=adapter,
            raw={"cli": {"cols": 80, "rows": 5}},
            agent=SimpleNamespace(brain=brain, command=""),
        )

    def test_brain_builds_and_only_advances_more(self) -> None:
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
        self.assertEqual(brain.decide(obs2), [])
        self.assertEqual(brain.last_decision.intent, "explore")

    def test_brain_is_restricted_to_cli_nethack(self) -> None:
        from docich.adapters import AdapterError

        with self.assertRaises(AdapterError):
            NethackPolicyBrain(SimpleNamespace(), self._game(name="robots"))
        with self.assertRaises(AdapterError):
            NethackPolicyBrain(SimpleNamespace(), self._game(adapter="browser"))


if __name__ == "__main__":
    unittest.main()

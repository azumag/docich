from __future__ import annotations

import json
import unittest

from docich.nethack_inventory import parse_visible_inventory
from docich.nethack_observation import normalize_tty
from docich.nethack_policy import PolicyDecision
from docich.nethack_strategy import (
    build_strategic_request,
    parse_strategic_proposal,
    should_narrate,
)


def observation():
    text = (
        "Really attack? [yn]\n"
        "..@d....\n"
        "..#.....\n"
        "Dlvl:3 $:42 HP:5(20) Pw:7(10) AC:2 Exp:4\n"
        "T:123 Hungry\n"
    )
    return normalize_tty(text, cols=80, rows=5)


class TestStrategicRequest(unittest.TestCase):
    def test_request_contains_public_summary_and_visible_inventory_only(self) -> None:
        decision = PolicyDecision(
            layer="strategic",
            intent="prompt_decision",
            reason="visible yes/no prompt requires context",
            requires_llm=True,
        )
        items = parse_visible_inventory(
            "a - a potion called cloudy\n"
            "b - an uncursed food ration\n"
        )
        request = build_strategic_request(observation(), decision, items)
        payload = request.to_dict()
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["intent"], "prompt_decision")
        self.assertEqual(payload["observation"]["vitals"]["hp"], 5)
        self.assertEqual(payload["inventory"][0]["description"], "a potion called cloudy")
        self.assertEqual(payload["inventory"][0]["buc"], "unknown")
        self.assertNotIn("raw_text", payload["observation"])
        serialized = request.to_json()
        self.assertNotIn("true_identity", serialized)
        self.assertIn("do not assume hidden NetHack state", serialized)


class TestStrategicProposal(unittest.TestCase):
    def test_valid_advisory_proposals_parse_without_becoming_actions(self) -> None:
        proposal = parse_strategic_proposal(
            {
                "schema_version": 1,
                "kind": "consume",
                "rationale": "visible hunger is dangerous",
                "inventory_letter": "b",
                "narration": "食料を使う候補です。",
            }
        )
        self.assertEqual(proposal.kind, "consume")
        self.assertEqual(proposal.inventory_letter, "b")
        self.assertFalse(hasattr(proposal, "actions"))

        prompt = parse_strategic_proposal(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": "answer_prompt",
                    "rationale": "visible prompt",
                    "prompt_answer": "n",
                }
            )
        )
        self.assertEqual(prompt.prompt_answer, "n")

    def test_invalid_or_overpowered_shapes_are_rejected(self) -> None:
        invalid = [
            {"schema_version": 2, "kind": "hold", "rationale": "x"},
            {"schema_version": 1, "kind": "hold", "rationale": "x"},
            {"schema_version": 1, "kind": "shell", "rationale": "x"},
            {"schema_version": 1, "kind": "consume", "rationale": "x"},
            {
                "schema_version": 1,
                "kind": "hold",
                "rationale": "x",
                "inventory_letter": "a",
            },
            {
                "schema_version": 1,
                "kind": "answer_prompt",
                "rationale": "x",
            },
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    parse_strategic_proposal(payload)

    def test_narration_is_reserved_for_meaningful_changes(self) -> None:
        strategic = PolicyDecision(
            layer="strategic",
            intent="survival_emergency",
            reason="low hp",
            requires_llm=True,
        )
        explore = PolicyDecision(
            layer="midlevel",
            intent="explore_step",
            reason="safe",
        )
        self.assertTrue(should_narrate(strategic))
        self.assertFalse(should_narrate(explore))
        self.assertTrue(should_narrate(explore, previous_intent="assess_contact"))


if __name__ == "__main__":
    unittest.main()

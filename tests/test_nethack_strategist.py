from __future__ import annotations

import subprocess
import unittest
from types import SimpleNamespace

from docich.nethack_inventory import parse_visible_inventory
from docich.nethack_observation import normalize_tty
from docich.nethack_policy import PolicyDecision
from docich.nethack_strategist import (
    CommandStrategist,
    evaluate_proposal,
    execution_plan,
)
from docich.nethack_strategy import (
    StrategicProposal,
    StrategicRequest,
    build_strategic_request,
)


def obs(message: str = "msg"):
    return normalize_tty(
        f"{message}\n..@.....\n..#.....\nDlvl:3 HP:5(20) Pw:7(10) AC:2 Exp:4\nT:123\n",
        cols=80,
        rows=5,
    )


def emergency_request(items=()):
    decision = PolicyDecision(
        layer="strategic",
        intent="survival_emergency",
        reason="visible HP is critical",
        requires_llm=True,
    )
    return build_strategic_request(obs(), decision, items)


class TestCommandStrategist(unittest.TestCase):
    def test_constructor_rejects_empty_command_and_invalid_timeout(self) -> None:
        with self.assertRaises(ValueError):
            CommandStrategist([])
        with self.assertRaises(ValueError):
            CommandStrategist("   ")
        with self.assertRaises(ValueError):
            CommandStrategist(["fake"], timeout_s=0)
        with self.assertRaises(ValueError):
            CommandStrategist(["fake"], timeout_s=121)

    def test_successful_dispatch_parses_advisory_proposal(self) -> None:
        seen = {}

        def runner(command, **kwargs):
            seen["command"] = command
            seen["input"] = kwargs["input"]
            seen["timeout"] = kwargs["timeout"]
            return SimpleNamespace(
                returncode=0,
                stdout='{"schema_version":1,"kind":"hold","rationale":"wait"}',
                stderr="",
            )

        strategist = CommandStrategist(["fake-strategist"], timeout_s=7, runner=runner)
        result = strategist.dispatch(emergency_request())
        self.assertEqual(result.status, "proposed")
        self.assertIsNotNone(result.proposal)
        assert result.proposal is not None
        self.assertEqual(result.proposal.kind, "hold")
        self.assertEqual(seen["command"], ["fake-strategist"])
        self.assertEqual(seen["timeout"], 7.0)
        self.assertIn('"constraints"', seen["input"])

    def test_timeout_nonzero_and_invalid_json_fail_closed(self) -> None:
        def timeout_runner(command, **kwargs):
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])

        timeout = CommandStrategist(["fake"], runner=timeout_runner).dispatch(emergency_request())
        self.assertEqual(timeout.status, "error")
        self.assertIn("timeout", timeout.error or "")

        def nonzero_runner(command, **kwargs):
            return SimpleNamespace(returncode=9, stdout="", stderr="provider down")

        nonzero = CommandStrategist(["fake"], runner=nonzero_runner).dispatch(emergency_request())
        self.assertEqual(nonzero.status, "error")
        self.assertIn("code 9", nonzero.error or "")

        def invalid_runner(command, **kwargs):
            return SimpleNamespace(returncode=0, stdout="not-json", stderr="")

        invalid = CommandStrategist(["fake"], runner=invalid_runner).dispatch(emergency_request())
        self.assertEqual(invalid.status, "error")
        self.assertIn("invalid strategist proposal", invalid.error or "")

    def test_request_and_response_size_limits_fail_closed(self) -> None:
        huge = StrategicRequest(
            schema_version=1,
            intent="survival_emergency",
            reason="x" * 3000,
            observation={},
            inventory=[],
            constraints=(),
        )
        runner_called = []

        def runner(command, **kwargs):
            runner_called.append(True)
            return SimpleNamespace(returncode=0, stdout="{}", stderr="")

        strategist = CommandStrategist(["fake"], max_request_bytes=1024, runner=runner)
        result = strategist.dispatch(huge)
        self.assertEqual(result.status, "error")
        self.assertEqual(runner_called, [])

        def large_response(command, **kwargs):
            return SimpleNamespace(returncode=0, stdout="x" * 2000, stderr="")

        strategist2 = CommandStrategist(["fake"], max_response_bytes=1024, runner=large_response)
        result2 = strategist2.dispatch(emergency_request())
        self.assertEqual(result2.status, "error")
        self.assertIn("size limit", result2.error or "")


class TestProposalEvaluation(unittest.TestCase):
    def test_inventory_proposal_requires_fresh_exact_public_snapshot(self) -> None:
        before = parse_visible_inventory("a - an uncursed food ration\n")
        request = emergency_request(before)
        proposal = StrategicProposal(
            schema_version=1,
            kind="consume",
            rationale="visible critical HP and food candidate",
            inventory_letter="a",
        )

        approved = evaluate_proposal(
            request,
            proposal,
            current_observation=obs(),
            current_inventory=before,
        )
        self.assertTrue(approved.approved)
        # Approval is not execution authorization in P3d.
        plan = execution_plan(approved)
        self.assertFalse(plan.allowed)
        self.assertEqual(plan.actions, ())

        changed = parse_visible_inventory("a - a potion called cloudy\n")
        rejected = evaluate_proposal(
            request,
            proposal,
            current_observation=obs(),
            current_inventory=changed,
        )
        self.assertFalse(rejected.approved)
        self.assertIn("changed", rejected.reason)

    def test_prompt_answer_is_revalidated_against_fresh_prompt(self) -> None:
        prompt_obs = obs("Really attack? [yn]")
        decision = PolicyDecision(
            layer="strategic",
            intent="prompt_decision",
            reason="visible prompt",
            requires_llm=True,
        )
        request = build_strategic_request(prompt_obs, decision)
        yes = StrategicProposal(
            schema_version=1,
            kind="answer_prompt",
            rationale="answer visible prompt",
            prompt_answer="n",
        )
        approved = evaluate_proposal(
            request,
            yes,
            current_observation=prompt_obs,
        )
        self.assertTrue(approved.approved)
        self.assertFalse(execution_plan(approved).allowed)

        stale = evaluate_proposal(request, yes, current_observation=obs("prompt is gone"))
        self.assertFalse(stale.approved)

    def test_kind_must_match_original_intent(self) -> None:
        request = emergency_request()
        proposal = StrategicProposal(
            schema_version=1,
            kind="descend",
            rationale="progress",
        )
        result = evaluate_proposal(request, proposal, current_observation=obs())
        self.assertFalse(result.approved)
        self.assertIn("not allowed", result.reason)

    def test_only_hold_is_executable_and_it_is_noop(self) -> None:
        request = emergency_request()
        proposal = StrategicProposal(schema_version=1, kind="hold", rationale="wait")
        evaluation = evaluate_proposal(request, proposal, current_observation=obs())
        self.assertTrue(evaluation.approved)
        plan = execution_plan(evaluation)
        self.assertTrue(plan.allowed)
        self.assertEqual(plan.actions, ())


if __name__ == "__main__":
    unittest.main()

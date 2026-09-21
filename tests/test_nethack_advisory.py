from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from docich.nethack_advisory import (
    NethackAdvisoryConfig,
    NethackAdvisoryController,
    load_advisory_config,
)
from docich.nethack_observation import normalize_tty
from docich.nethack_policy import PolicyDecision
from docich.nethack_strategist import StrategistDispatchResult
from docich.nethack_strategy import StrategicProposal


def visible_screen(*, hp: str = "2(10)", message: str = "danger") -> str:
    return (
        f"{message}\n"
        "..@.....\n"
        "..#.....\n"
        f"Dlvl:3 HP:{hp} Pw:7(10) AC:2 Exp:4\n"
        "T:123\n"
    )


def normalized(*, hp: str = "2(10)", message: str = "danger"):
    return normalize_tty(visible_screen(hp=hp, message=message), cols=80, rows=5)


def emergency() -> PolicyDecision:
    return PolicyDecision(
        layer="strategic",
        intent="survival_emergency",
        reason="visible HP is critical",
        requires_llm=True,
    )


class FakeStrategist:
    def __init__(self, result: StrategistDispatchResult) -> None:
        self.result = result
        self.calls = []

    def dispatch(self, request):
        self.calls.append(request)
        return self.result


class TestAdvisoryConfig(unittest.TestCase):
    def test_disabled_standard_shape_does_not_require_command(self) -> None:
        game = SimpleNamespace(
            raw={
                "nethack": {
                    "strategist": {
                        "enabled": False,
                        "command": [],
                        "narration_enabled": False,
                    }
                }
            }
        )
        cfg = load_advisory_config(game)
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.command, ())
        self.assertEqual(cfg.max_calls, 24)

    def test_enabled_requires_command_and_bounds(self) -> None:
        with self.assertRaises(ValueError):
            load_advisory_config(
                SimpleNamespace(raw={"nethack": {"strategist": {"enabled": True, "command": []}}})
            )
        with self.assertRaises(ValueError):
            load_advisory_config(
                SimpleNamespace(
                    raw={
                        "nethack": {
                            "strategist": {
                                "enabled": True,
                                "command": ["x"],
                                "max_calls": 0,
                            }
                        }
                    }
                )
            )


class TestAdvisoryController(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.g = SimpleNamespace(state_dir=self.root / "state", repo_root=self.root)
        self.game = SimpleNamespace(name="nethack", raw={})
        self.clock = [100.0]

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def controller(self, strategist, *, max_calls=24, cooldown=30.0, narrator=None):
        cfg = NethackAdvisoryConfig(
            enabled=True,
            command=("fake",),
            cooldown_s=cooldown,
            max_calls=max_calls,
            narration_enabled=narrator is not None,
            narration_cooldown_s=20.0,
        )
        return NethackAdvisoryController(
            self.g,
            self.game,
            config=cfg,
            strategist=strategist,
            narrator=narrator,
            monotonic=lambda: self.clock[0],
            wall_time=lambda: 1234.5,
        )

    def test_proposal_is_logged_and_narrated_but_never_becomes_action(self) -> None:
        spoken = []
        strategist = FakeStrategist(
            StrategistDispatchResult(
                status="proposed",
                proposal=StrategicProposal(
                    schema_version=1,
                    kind="rest",
                    rationale="wait one turn with dot",
                    narration="ここは . で1ターン待機します。",
                ),
            )
        )
        ctl = self.controller(strategist, narrator=spoken.append)
        text = visible_screen()
        outcome = ctl.consider(text, normalized(), emergency())

        self.assertEqual(outcome.status, "proposed")
        self.assertEqual(outcome.proposal_kind, "rest")
        self.assertEqual(outcome.evaluation_status, "approved")
        self.assertTrue(outcome.narrated)
        self.assertEqual(spoken, ["ここは . で1ターン待機します。"])
        self.assertFalse(hasattr(outcome, "actions"))
        self.assertEqual(len(strategist.calls), 1)

        log_path = self.root / "state" / "nethack" / "strategist" / "advisory.jsonl"
        event = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(event["execution"], "advisory_only")
        self.assertEqual(event["proposal"]["kind"], "rest")
        self.assertEqual(event["evaluation"]["status"], "approved")

    def test_same_intent_is_cooled_down_and_budget_is_bounded(self) -> None:
        strategist = FakeStrategist(
            StrategistDispatchResult(
                status="proposed",
                proposal=StrategicProposal(schema_version=1, kind="rest", rationale="wait one turn with dot"),
            )
        )
        ctl = self.controller(strategist, max_calls=2, cooldown=10.0)
        text = visible_screen()
        observation = normalized()

        self.assertEqual(ctl.consider(text, observation, emergency()).status, "proposed")
        self.assertEqual(ctl.consider(text, observation, emergency()).status, "cooldown")
        self.assertEqual(len(strategist.calls), 1)

        self.clock[0] += 11.0
        self.assertEqual(ctl.consider(text, observation, emergency()).status, "proposed")
        self.clock[0] += 11.0
        self.assertEqual(ctl.consider(text, observation, emergency()).status, "budget_exhausted")
        self.assertEqual(len(strategist.calls), 2)
        self.assertEqual(ctl.calls_used, 2)

    def test_dispatch_error_and_narration_error_are_fail_open(self) -> None:
        strategist = FakeStrategist(StrategistDispatchResult(status="error", error="provider down"))

        def broken_narrator(text: str) -> None:
            raise RuntimeError("audio down")

        ctl = self.controller(strategist, narrator=broken_narrator)
        outcome = ctl.consider(visible_screen(), normalized(), emergency())
        self.assertEqual(outcome.status, "error")
        self.assertEqual(outcome.error, "provider down")
        self.assertFalse(outcome.narrated)

    def test_local_meaningful_decision_can_narrate_without_model_dispatch(self) -> None:
        spoken = []
        cfg = NethackAdvisoryConfig(
            enabled=False,
            command="",
            narration_enabled=True,
            narration_cooldown_s=20.0,
        )
        ctl = NethackAdvisoryController(
            self.g,
            self.game,
            config=cfg,
            narrator=spoken.append,
            monotonic=lambda: self.clock[0],
        )
        contact = PolicyDecision(
            layer="midlevel",
            intent="assess_contact",
            reason="visible contact",
        )
        outcome = ctl.consider(visible_screen(hp="10(10)"), normalized(hp="10(10)"), contact)
        self.assertEqual(outcome.status, "disabled")
        self.assertTrue(outcome.narrated)
        self.assertIn("生物", spoken[0])

        # Same intent inside narration cooldown is silent.
        again = ctl.consider(visible_screen(hp="10(10)"), normalized(hp="10(10)"), contact)
        self.assertFalse(again.narrated)

    def test_non_strategic_exploration_never_calls_model(self) -> None:
        strategist = FakeStrategist(
            StrategistDispatchResult(
                status="proposed",
                proposal=StrategicProposal(schema_version=1, kind="rest", rationale="wait one turn with dot"),
            )
        )
        ctl = self.controller(strategist)
        decision = PolicyDecision(
            layer="midlevel",
            intent="explore_step",
            reason="safe visible step",
        )
        outcome = ctl.consider(visible_screen(hp="10(10)"), normalized(hp="10(10)"), decision)
        self.assertEqual(outcome.status, "not_needed")
        self.assertEqual(strategist.calls, [])


if __name__ == "__main__":
    unittest.main()

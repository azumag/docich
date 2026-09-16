from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from docich.adapters.base import Observation
from docich.agent.brains import NethackPolicyBrain
from docich.nethack_candidate_eval import CandidateManifest
from docich.nethack_candidate_shadow import (
    CandidateShadowConfig,
    NethackCandidateShadowController,
    load_candidate_shadow_config,
)
from docich.nethack_observation import normalize_tty
from docich.nethack_policy import PolicyDecision
from docich.nethack_strategist import StrategistDispatchResult
from docich.nethack_strategy import StrategicProposal


def screen(*, hp: str = "2(10)") -> str:
    return (
        "danger\n"
        "###@.\n"
        "     \n"
        f"Dlvl:3 HP:{hp} Pw:7(10) AC:2 Exp:4\n"
        "T:123\n"
    )


def normalized(*, hp: str = "2(10)"):
    return normalize_tty(screen(hp=hp), cols=80, rows=5)


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


class TestCandidateShadowConfig(unittest.TestCase):
    def test_disabled_config_does_not_require_manifest(self) -> None:
        game = SimpleNamespace(raw={"nethack": {"candidate_shadow": {"enabled": False}}})
        cfg = load_candidate_shadow_config(game)
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.manifest, "")
        self.assertEqual(cfg.max_calls, 24)

    def test_enabled_requires_manifest_and_bounds(self) -> None:
        with self.assertRaises(ValueError):
            load_candidate_shadow_config(
                SimpleNamespace(raw={"nethack": {"candidate_shadow": {"enabled": True}}})
            )
        with self.assertRaises(ValueError):
            load_candidate_shadow_config(
                SimpleNamespace(
                    raw={
                        "nethack": {
                            "candidate_shadow": {
                                "enabled": True,
                                "manifest": "candidate.json",
                                "max_calls": 0,
                            }
                        }
                    }
                )
            )


class TestCandidateShadowController(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.state = self.root / "state"
        self.g = SimpleNamespace(state_dir=self.state, repo_root=self.root)
        self.game = SimpleNamespace(name="nethack", raw={})
        self.clock = [100.0]
        self.suite_id = "a" * 64
        self.manifest = CandidateManifest(
            candidate_id="candidate-a",
            version="v1",
            command=("secret-candidate-command", "--token=do-not-log"),
            timeout_s=1.0,
            expected_suite_id=self.suite_id,
        )
        self._write_safety_report()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _report_path(self) -> Path:
        return (
            self.state
            / "nethack"
            / "regression"
            / "candidates"
            / self.manifest.candidate_id
            / self.manifest.version
            / f"{self.suite_id}.json"
        )

    def _write_safety_report(self, **overrides) -> None:
        payload = {
            "schema_version": 1,
            "candidate_id": self.manifest.candidate_id,
            "candidate_version": self.manifest.version,
            "candidate_fingerprint": self.manifest.fingerprint,
            "command_sha256": self.manifest.command_hash,
            "suite_id": self.suite_id,
            "status": "completed",
            "baseline_contract_passed": True,
            "candidate_safety_contract_passed": True,
            "eligible_for_behavior_review": True,
            "eligible_for_promotion_review": False,
            "performance_improvement_assessed": False,
            "automatic_promotion": False,
            "policy_effect": "none",
        }
        payload.update(overrides)
        path = self._report_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def controller(self, strategist, *, max_calls=24, cooldown=30.0):
        return NethackCandidateShadowController(
            self.g,
            self.game,
            config=CandidateShadowConfig(
                enabled=True,
                manifest="unused.json",
                cooldown_s=cooldown,
                max_calls=max_calls,
            ),
            manifest=self.manifest,
            strategist=strategist,
            monotonic=lambda: self.clock[0],
            wall_time=lambda: 1234.5,
        )

    def test_live_shadow_requires_exact_green_p5d_report(self) -> None:
        strategist = FakeStrategist(StrategistDispatchResult(status="error", error="unused"))
        self._write_safety_report(candidate_safety_contract_passed=False)
        with self.assertRaises(ValueError):
            self.controller(strategist)
        self.assertEqual(strategist.calls, [])

        manifest_without_suite = CandidateManifest(
            candidate_id="candidate-a",
            version="v1",
            command=("fake",),
            expected_suite_id=None,
        )
        with self.assertRaises(ValueError):
            NethackCandidateShadowController(
                self.g,
                self.game,
                config=CandidateShadowConfig(enabled=True, manifest="unused.json"),
                manifest=manifest_without_suite,
                strategist=strategist,
            )

    def test_safe_candidate_is_logged_as_shadow_only_with_run_id(self) -> None:
        current = self.state / "nethack" / "current.json"
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_text(json.dumps({"schema_version": 1, "run_id": "run-123"}), encoding="utf-8")
        strategist = FakeStrategist(
            StrategistDispatchResult(
                status="proposed",
                proposal=StrategicProposal(
                    schema_version=1,
                    kind="hold",
                    rationale="survival first",
                ),
            )
        )
        ctl = self.controller(strategist)
        outcome = ctl.consider(screen(), normalized(), emergency())
        self.assertEqual(outcome.status, "proposed")
        self.assertEqual(outcome.evaluation_status, "approved")
        self.assertEqual(outcome.unexpected_execution_actions, 0)
        self.assertFalse(hasattr(outcome, "actions"))

        log = self.state / "nethack" / "candidate_shadow" / "candidate-a" / "v1.jsonl"
        text = log.read_text(encoding="utf-8")
        event = json.loads(text.splitlines()[-1])
        self.assertEqual(event["run_id"], "run-123")
        self.assertEqual(event["suite_id"], self.suite_id)
        self.assertEqual(event["execution"], "shadow_only")
        self.assertEqual(event["policy_effect"], "none")
        self.assertEqual(event["baseline"]["intent"], "survival_emergency")
        self.assertEqual(event["proposal"]["kind"], "hold")
        self.assertNotIn("secret-candidate-command", text)
        self.assertNotIn("do-not-log", text)

    def test_dangerous_candidate_is_rejected_and_never_creates_actions(self) -> None:
        strategist = FakeStrategist(
            StrategistDispatchResult(
                status="proposed",
                proposal=StrategicProposal(
                    schema_version=1,
                    kind="descend",
                    rationale="go deeper",
                ),
            )
        )
        outcome = self.controller(strategist).consider(screen(), normalized(), emergency())
        self.assertEqual(outcome.status, "proposed")
        self.assertEqual(outcome.proposal_kind, "descend")
        self.assertEqual(outcome.evaluation_status, "rejected")
        self.assertEqual(outcome.unexpected_execution_actions, 0)

    def test_same_intent_cooldown_and_budget_limit_model_calls(self) -> None:
        strategist = FakeStrategist(
            StrategistDispatchResult(
                status="proposed",
                proposal=StrategicProposal(schema_version=1, kind="hold", rationale="wait"),
            )
        )
        ctl = self.controller(strategist, max_calls=2, cooldown=10.0)
        self.assertEqual(ctl.consider(screen(), normalized(), emergency()).status, "proposed")
        self.assertEqual(ctl.consider(screen(), normalized(), emergency()).status, "cooldown")
        self.clock[0] += 11
        self.assertEqual(ctl.consider(screen(), normalized(), emergency()).status, "proposed")
        self.clock[0] += 11
        self.assertEqual(ctl.consider(screen(), normalized(), emergency()).status, "budget_exhausted")
        self.assertEqual(len(strategist.calls), 2)

    def test_non_strategic_decision_never_calls_candidate(self) -> None:
        strategist = FakeStrategist(
            StrategistDispatchResult(
                status="proposed",
                proposal=StrategicProposal(schema_version=1, kind="hold", rationale="wait"),
            )
        )
        decision = PolicyDecision(
            layer="midlevel",
            intent="explore_step",
            reason="safe visible step",
        )
        outcome = self.controller(strategist).consider(
            screen(hp="10(10)"), normalized(hp="10(10)"), decision
        )
        self.assertEqual(outcome.status, "not_needed")
        self.assertEqual(strategist.calls, [])


class TestCandidateShadowBrainIsolation(unittest.TestCase):
    def test_shadow_failure_cannot_change_reviewed_gameplay_action(self) -> None:
        class BrokenShadow:
            def consider(self, *args, **kwargs):
                raise RuntimeError("candidate down")

        game = SimpleNamespace(
            name="nethack",
            adapter="cli",
            raw={"cli": {"cols": 80, "rows": 5}},
        )
        with tempfile.TemporaryDirectory() as tmp:
            g = SimpleNamespace(state_dir=Path(tmp) / "state", repo_root=Path(tmp))
            with patch(
                "docich.nethack_candidate_shadow.NethackCandidateShadowController",
                return_value=BrokenShadow(),
            ):
                brain = NethackPolicyBrain(g, game)
            obs = Observation(
                game="nethack",
                title="NetHack",
                adapter="cli",
                ts=1.0,
                kind="text",
                text=screen(hp="10(10)"),
            )
            actions = brain.decide(obs)
            self.assertEqual(len(actions), 1)
            self.assertIn(actions[0].text, {"h", "j", "k", "l"})
            self.assertIsNone(brain.last_candidate_shadow)


if __name__ == "__main__":
    unittest.main()

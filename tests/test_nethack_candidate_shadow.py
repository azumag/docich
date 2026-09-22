from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

from docich.actions import Action
from docich.nethack_candidate_eval import load_candidate_manifest
from docich.nethack_candidate_shadow import (
    NethackCandidateShadowConfig,
    NethackCandidateShadowController,
    NethackCandidateShadowError,
    load_candidate_shadow_config,
)
from docich.nethack_observation import normalize_tty
from docich.nethack_policy import PolicyDecision
from docich.nethack_regression import _canonical_hash
from docich.nethack_strategist import (
    DISPATCH_ERROR_KINDS,
    CommandStrategist,
    StrategistDispatchResult,
)
from docich.nethack_strategy import StrategicProposal


def screen(*, hp: str = "2(10)", condition: str = "") -> str:
    return (
        "danger\n"
        "###@.\n"
        "     \n"
        f"Dlvl:3 HP:{hp} Pw:7(10) AC:2 Exp:4\n"
        f"T:123 {condition}\n"
    )


def observation(*, hp: str = "2(10)", condition: str = ""):
    return normalize_tty(screen(hp=hp, condition=condition), cols=80, rows=5)


def emergency(intent: str = "survival_emergency") -> PolicyDecision:
    return PolicyDecision(
        layer="strategic",
        intent=intent,
        reason="visible public state requires strategic review",
        requires_llm=True,
    )


class FakeStrategist:
    def __init__(self, proposal=None, error=None):
        self.proposal = proposal
        self.error = error
        self.requests = []

    def dispatch(self, request):
        self.requests.append(request)
        if self.error is not None:
            return StrategistDispatchResult(status="error", error=self.error)
        return StrategistDispatchResult(status="proposed", proposal=self.proposal)


class BlockingStrategist(FakeStrategist):
    def __init__(self, proposal):
        super().__init__(proposal=proposal)
        self.started = threading.Event()
        self.release = threading.Event()

    def dispatch(self, request):
        self.requests.append(request)
        self.started.set()
        self.release.wait(timeout=2.0)
        return StrategistDispatchResult(status="proposed", proposal=self.proposal)


class TestCandidateShadowConfig(unittest.TestCase):
    def test_disabled_standard_shape_needs_no_manifest(self):
        game = SimpleNamespace(
            raw={
                "nethack": {
                    "candidate_shadow": {
                        "enabled": False,
                        "manifest": "",
                    }
                }
            }
        )
        cfg = load_candidate_shadow_config(game)
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.manifest_path, "")
        self.assertEqual(cfg.max_calls, 24)

    def test_enabled_requires_manifest_and_bounds(self):
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
        self.root = Path(self.tempdir.name) / "repo"
        self.state = self.root / "run"
        self.root.mkdir(parents=True)
        self.g = SimpleNamespace(state_dir=self.state, repo_root=self.root)
        self.game = SimpleNamespace(name="nethack", raw={})
        self.manifest_path = self.root / "candidate.json"
        self.manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "candidate_id": "candidate-a",
                    "version": "v1",
                    "command": ["fake-candidate", "--json"],
                    "timeout_s": 2.0,
                    "max_cases": 64,
                    "min_replay_cases": 1,
                }
            ),
            encoding="utf-8",
        )
        self.manifest = load_candidate_manifest(self.manifest_path)
        self.suite_id = self._write_suite()
        self._write_offline_report()
        self.clock = [100.0]

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _write_suite(self) -> str:
        cases = [
            {
                "case_id": "case-1",
                "lesson_key": "lesson-1",
                "category": "survival_signal",
                "kind": "policy_survival_emergency",
                "scored": True,
                "evidence_count": 1,
                "run_ids": [],
                "source_expeditions": [],
                "fixture": {},
                "expectation": {},
                "replay_request": None,
                "evidence": {},
                "policy_effect": "none",
            }
        ]
        suite_id = _canonical_hash({"schema_version": 1, "cases": cases})
        path = self.state / "nethack" / "regression" / "suite.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "suite_id": suite_id,
                    "schema_version": 1,
                    "generated_at": "2026-09-16T10:00:00+00:00",
                    "source": "p5c_lessons_regression",
                    "cases": cases,
                    "policy_effect": "none",
                    "automatic_promotion": False,
                }
            ),
            encoding="utf-8",
        )
        return suite_id

    def _write_offline_report(self, **overrides) -> None:
        report = {
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
            "automatic_promotion": False,
            "policy_effect": "none",
        }
        report.update(overrides)
        path = (
            self.state
            / "nethack"
            / "regression"
            / "candidates"
            / self.manifest.candidate_id
            / self.manifest.version
            / f"{self.suite_id}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report), encoding="utf-8")

    def _cfg(self, *, cooldown=0.0, max_calls=24):
        return NethackCandidateShadowConfig(
            enabled=True,
            manifest_path=str(self.manifest_path),
            cooldown_s=cooldown,
            max_calls=max_calls,
        )

    def _controller(self, strategist, *, cooldown=0.0, max_calls=24):
        return NethackCandidateShadowController(
            self.g,
            self.game,
            config=self._cfg(cooldown=cooldown, max_calls=max_calls),
            strategist=strategist,
            monotonic=lambda: self.clock[0],
            wall_time=lambda: 1234.5,
        )

    def _persisted_log_text(self) -> str:
        base = (
            self.state
            / "nethack"
            / "candidate-shadow"
            / self.manifest.candidate_id
            / self.manifest.version
        )
        paths = sorted(base.glob("*.jsonl"))
        self.assertEqual(len(paths), 1)
        return paths[0].read_text(encoding="utf-8")

    def _install_run_context(self):
        run_id = str(uuid.uuid4())
        root = self.state / "nethack"
        runs = root / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        (root / "current.json").write_text(
            json.dumps({"schema_version": 1, "run_id": run_id}), encoding="utf-8"
        )
        (runs / f"{run_id}.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "run_id": run_id,
                    "expedition": 3,
                    "status": "active",
                    "sessions": [{"started_at": "x", "ended_at": None}],
                }
            ),
            encoding="utf-8",
        )
        return run_id

    def test_offline_gate_requires_successful_matching_p5d_report(self):
        self._write_offline_report(candidate_safety_contract_passed=False)
        with self.assertRaises(NethackCandidateShadowError):
            self._controller(
                FakeStrategist(
                    StrategicProposal(schema_version=1, kind="rest", rationale="wait one turn with dot")
                )
            )

    def test_offline_gate_rejects_tampered_suite_content(self):
        suite_path = self.state / "nethack" / "regression" / "suite.json"
        payload = json.loads(suite_path.read_text(encoding="utf-8"))
        payload["cases"].append(
            {
                "case_id": "tampered",
                "policy_effect": "none",
            }
        )
        suite_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(NethackCandidateShadowError):
            self._controller(
                FakeStrategist(
                    StrategicProposal(schema_version=1, kind="rest", rationale="wait one turn with dot")
                )
            )

    def test_candidate_is_nonblocking_and_never_replaces_production_action(self):
        blocker = BlockingStrategist(
            StrategicProposal(schema_version=1, kind="descend", rationale="go down")
        )
        ctl = self._controller(blocker)
        baseline = (Action(type="text", text="h"),)
        outcome = ctl.consider(screen(), observation(), emergency(), baseline)
        self.assertEqual(outcome.status, "queued")
        self.assertEqual([action.text for action in baseline], ["h"])
        self.assertTrue(blocker.started.wait(timeout=1.0))
        self.assertFalse(ctl.wait_for_idle(timeout=0.01))
        blocker.release.set()
        self.assertTrue(ctl.wait_for_idle(timeout=1.0))
        completed = ctl.last_completed
        self.assertEqual(completed.status, "proposed")
        self.assertEqual(completed.proposal_kind, "descend")
        self.assertEqual(completed.evaluation_status, "rejected")

    def test_log_is_run_scoped_public_and_contains_no_command_argv(self):
        run_id = self._install_run_context()
        strategist = FakeStrategist(
            StrategicProposal(schema_version=1, kind="rest", rationale="wait one turn with dot")
        )
        ctl = self._controller(strategist)
        outcome = ctl.consider(
            screen(), observation(), emergency(), (Action(type="text", text="h"),)
        )
        self.assertEqual(outcome.status, "queued")
        self.assertTrue(ctl.wait_for_idle(timeout=1.0))
        path = (
            self.state
            / "nethack"
            / "candidate-shadow"
            / "candidate-a"
            / "v1"
            / f"{run_id}.jsonl"
        )
        event = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(event["run_id"], run_id)
        self.assertEqual(event["expedition"], 3)
        self.assertEqual(event["session_index"], 0)
        self.assertEqual(event["turn"], 123)
        self.assertEqual(event["dungeon_level"], 3)
        self.assertEqual(event["current_actions"], [{"type": "text", "text": "h"}])
        self.assertIn("critical_hp", event["critical_tags"])
        self.assertFalse(event["candidate_action_sent"])
        self.assertEqual(event["execution"], "candidate_shadow_only")
        self.assertEqual(event["policy_effect"], "none")
        self.assertEqual(event["candidate_proposal"]["kind"], "rest")
        self.assertEqual(event["candidate_evaluation"]["status"], "approved")
        self.assertIsNone(event["candidate_error_kind"])
        self.assertNotIn("command", event)
        self.assertEqual(event["command_sha256"], self.manifest.command_hash)
        self.assertEqual(len(event["command_sha256"]), 64)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_dispatch_error_is_logged_and_gameplay_side_remains_fail_open(self):
        sentinel = "SECRET-candidate-token-8f2a"
        ctl = self._controller(FakeStrategist(error=f"provider down {sentinel}"))
        outcome = ctl.consider(screen(), observation(), emergency(), ())
        self.assertEqual(outcome.status, "queued")
        self.assertTrue(ctl.wait_for_idle(timeout=1.0))
        self.assertEqual(ctl.last_completed.status, "error")
        # Raw detail may stay process-local, but never reaches the JSONL.
        self.assertEqual(ctl.last_completed.error, f"provider down {sentinel}")
        text = self._persisted_log_text()
        self.assertNotIn(sentinel, text)
        event = json.loads(text.splitlines()[-1])
        self.assertEqual(event["candidate_error_kind"], "internal_error")
        self.assertNotIn("candidate_error", event)

    def test_persisted_log_never_contains_candidate_stderr_secret(self):
        secret = "AKIAIOSFODNN7EXAMPLE-candidate-secret"
        strategist = CommandStrategist(
            [sys.executable, "-c", f"import sys; sys.stderr.write({secret!r}); sys.exit(3)"],
            timeout_s=5.0,
        )
        ctl = self._controller(strategist)
        self.assertEqual(ctl.consider(screen(), observation(), emergency(), ()).status, "queued")
        self.assertTrue(ctl.wait_for_idle(timeout=5.0))
        self.assertEqual(ctl.last_completed.status, "error")
        self.assertIn(secret, ctl.last_completed.error or "")
        text = self._persisted_log_text()
        self.assertNotIn(secret, text)
        event = json.loads(text.splitlines()[-1])
        self.assertEqual(event["candidate_error_kind"], "process_failed")
        self.assertIn(event["candidate_error_kind"], DISPATCH_ERROR_KINDS)

    def test_persisted_log_never_contains_exception_secret(self):
        secret = "SECRET-exception-detail-31b7"

        class ExplodingStrategist:
            def dispatch(self, request):
                raise RuntimeError(f"candidate exploded: {secret}")

        ctl = self._controller(ExplodingStrategist())
        self.assertEqual(ctl.consider(screen(), observation(), emergency(), ()).status, "queued")
        self.assertTrue(ctl.wait_for_idle(timeout=1.0))
        self.assertIn(secret, ctl.last_completed.error or "")
        text = self._persisted_log_text()
        self.assertNotIn(secret, text)
        event = json.loads(text.splitlines()[-1])
        self.assertEqual(event["candidate_error_kind"], "internal_error")

    def test_persisted_log_rejects_non_allowlisted_error_kind(self):
        secret = "SECRET-forged-kind-c0ffee"

        class HostileStrategist:
            def dispatch(self, request):
                return StrategistDispatchResult(
                    status="error",
                    error=secret,
                    error_kind=secret,  # type: ignore[arg-type]
                )

        ctl = self._controller(HostileStrategist())
        self.assertEqual(ctl.consider(screen(), observation(), emergency(), ()).status, "queued")
        self.assertTrue(ctl.wait_for_idle(timeout=1.0))
        text = self._persisted_log_text()
        self.assertNotIn(secret, text)
        event = json.loads(text.splitlines()[-1])
        self.assertEqual(event["candidate_error_kind"], "internal_error")

    def test_safe_error_kind_fails_closed_for_unknown_values(self):
        from docich.nethack_candidate_shadow import _safe_error_kind

        for raw in (None, 3, b"timeout", {"kind": "timeout"}, ["timeout"], "Timeout", "raw stderr", ""):
            self.assertEqual(_safe_error_kind(raw), "internal_error")
        for kind in sorted(DISPATCH_ERROR_KINDS):
            self.assertEqual(_safe_error_kind(kind), kind)

    def test_persisted_log_classifies_timeout(self):
        strategist = CommandStrategist(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout_s=0.5,
        )
        ctl = self._controller(strategist)
        self.assertEqual(ctl.consider(screen(), observation(), emergency(), ()).status, "queued")
        self.assertTrue(ctl.wait_for_idle(timeout=5.0))
        self.assertEqual(ctl.last_completed.status, "error")
        text = self._persisted_log_text()
        event = json.loads(text.splitlines()[-1])
        self.assertEqual(event["candidate_error_kind"], "timeout")

    def test_only_strategic_decisions_are_called_and_budget_cooldown_are_bounded(self):
        strategist = FakeStrategist(
            StrategicProposal(schema_version=1, kind="rest", rationale="wait one turn with dot")
        )
        ctl = self._controller(strategist, cooldown=10.0, max_calls=2)
        local = PolicyDecision(
            layer="midlevel",
            intent="explore_step",
            reason="safe visible step",
            actions=(Action(type="text", text="h"),),
        )
        self.assertEqual(
            ctl.consider(screen(hp="10(10)"), observation(hp="10(10)"), local, local.actions).status,
            "not_needed",
        )
        self.assertEqual(strategist.requests, [])

        self.assertEqual(ctl.consider(screen(), observation(), emergency(), ()).status, "queued")
        self.assertTrue(ctl.wait_for_idle(timeout=1.0))
        self.assertEqual(ctl.consider(screen(), observation(), emergency(), ()).status, "cooldown")
        self.clock[0] += 11.0
        self.assertEqual(ctl.consider(screen(), observation(), emergency(), ()).status, "queued")
        self.assertTrue(ctl.wait_for_idle(timeout=1.0))
        self.clock[0] += 11.0
        self.assertEqual(
            ctl.consider(screen(), observation(), emergency(), ()).status,
            "budget_exhausted",
        )
        self.assertEqual(ctl.calls_used, 2)
        self.assertEqual(len(strategist.requests), 2)

    def test_disabled_controller_never_requires_manifest_or_calls_candidate(self):
        strategist = FakeStrategist(
            StrategicProposal(schema_version=1, kind="rest", rationale="wait one turn with dot")
        )
        ctl = NethackCandidateShadowController(
            self.g,
            self.game,
            config=NethackCandidateShadowConfig(enabled=False),
            strategist=strategist,
        )
        outcome = ctl.consider(screen(), observation(), emergency(), ())
        self.assertEqual(outcome.status, "disabled")
        self.assertEqual(strategist.requests, [])


if __name__ == "__main__":
    unittest.main()

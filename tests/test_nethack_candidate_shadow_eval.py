from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from docich.nethack_candidate_eval import load_candidate_manifest
from docich.nethack_candidate_shadow_eval import evaluate_live_candidate_shadow
from docich.nethack_regression import _canonical_hash


class TestCandidateShadowEvaluation(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "repo"
        self.state = self.root / "run"
        self.root.mkdir(parents=True)
        self.g = SimpleNamespace(state_dir=self.state, repo_root=self.root)
        self.now = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
        self.manifest_path = self.root / "candidate.json"
        self.manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "candidate_id": "candidate-a",
                    "version": "v1",
                    "command": ["candidate", "--json"],
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
        self.log_root = (
            self.state
            / "nethack"
            / "candidate-shadow"
            / self.manifest.candidate_id
            / self.manifest.version
        )
        self.log_root.mkdir(parents=True, exist_ok=True)

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
                    "generated_at": self.now.isoformat(),
                    "source": "p5c_lessons_regression",
                    "cases": cases,
                    "policy_effect": "none",
                    "automatic_promotion": False,
                }
            ),
            encoding="utf-8",
        )
        return suite_id

    def _write_offline_report(self) -> None:
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
        path.write_text(
            json.dumps(
                {
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
            ),
            encoding="utf-8",
        )

    def _run(self, *, status="dead", death_signature="killed_by:grid bug") -> str:
        run_id = str(uuid.uuid4())
        path = self.state / "nethack" / "runs" / f"{run_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "run_id": run_id,
            "expedition": 4,
            "status": status,
            "score": 1200,
            "turns": 4000,
            "max_depth": 5,
            "death_reason": "killed by a grid bug" if status == "dead" else None,
            "got_amulet": False,
            "retrospective": {
                "death_signature": death_signature if status == "dead" else None,
                "same_death_total_count": 2 if status == "dead" else 0,
            },
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        return run_id

    def _event(
        self,
        *,
        run_id: str,
        proposal_kind="rest",
        evaluation="approved",
        candidate_status="proposed",
        current_actions=None,
        critical_tags=None,
        would_count=0,
        **overrides,
    ):
        event = {
            "schema_version": 1,
            "ts": 1234.5,
            "candidate_id": self.manifest.candidate_id,
            "candidate_version": self.manifest.version,
            "candidate_fingerprint": self.manifest.fingerprint,
            "command_sha256": self.manifest.command_hash,
            "suite_id": self.suite_id,
            "run_id": run_id,
            "expedition": 4,
            "session_index": 0,
            "turn": 123,
            "dungeon_level": 3,
            "critical_tags": critical_tags or ["critical_hp", "intent:survival_emergency"],
            "current_decision": {
                "layer": "strategic",
                "intent": "survival_emergency",
                "reason": "critical HP",
                "requires_llm": True,
            },
            "current_actions": [] if current_actions is None else current_actions,
            "request": {"schema_version": 1},
            "candidate_status": candidate_status,
            "candidate_proposal": (
                {
                    "schema_version": 1,
                    "kind": proposal_kind,
                    "rationale": "shadow",
                    "inventory_letter": None,
                    "prompt_answer": None,
                    "narration": "",
                }
                if candidate_status == "proposed"
                else None
            ),
            "candidate_evaluation": (
                {"status": evaluation, "reason": "shadow check"}
                if candidate_status == "proposed"
                else None
            ),
            "candidate_error": "provider down" if candidate_status == "error" else None,
            "would_execute_allowed": proposal_kind == "rest" and evaluation == "approved",
            "would_execute_action_count": would_count,
            "candidate_action_sent": False,
            "execution": "candidate_shadow_only",
            "policy_effect": "none",
        }
        event.update(overrides)
        return event

    def _write_events(self, run_id: str, events) -> Path:
        path = self.log_root / f"{run_id}.jsonl"
        path.write_text(
            "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
            encoding="utf-8",
        )
        return path

    def test_run_metrics_and_terminal_correlation_are_descriptive_only(self):
        run_id = self._run()
        self._write_events(
            run_id,
            [
                self._event(run_id=run_id, proposal_kind="rest", evaluation="approved"),
                self._event(
                    run_id=run_id,
                    proposal_kind="descend",
                    evaluation="rejected",
                    critical_tags=["critical_hp"],
                ),
                self._event(
                    run_id=run_id,
                    candidate_status="error",
                    proposal_kind="rest",
                    critical_tags=["critical_hp"],
                ),
            ],
        )
        report = evaluate_live_candidate_shadow(self.g, self.manifest, now=self.now)

        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["total_events"], 3)
        self.assertEqual(report["candidate_proposed"], 2)
        self.assertEqual(report["candidate_errors"], 1)
        self.assertEqual(report["approved_proposals"], 1)
        self.assertEqual(report["rejected_proposals"], 1)
        self.assertAlmostEqual(report["proposal_reject_rate"], 0.5)
        self.assertEqual(report["proposal_kind_counts"], {"descend": 1, "rest": 1})
        self.assertEqual(report["critical_events"], 3)
        self.assertEqual(report["critical_rejected"], 1)
        self.assertEqual(report["critical_errors"], 1)
        self.assertEqual(report["tracked_runs"], 1)
        self.assertEqual(report["terminal_correlated_runs"], 1)
        self.assertTrue(report["candidate_shadow_integrity_passed"])
        self.assertTrue(report["correlation_is_not_causation"])
        self.assertFalse(report["performance_improvement_assessed"])
        self.assertFalse(report["eligible_for_promotion_review"])
        self.assertFalse(report["automatic_promotion"])
        self.assertEqual(report["policy_effect"], "none")

        run = report["runs"][0]
        self.assertEqual(run["run_id"], run_id)
        self.assertEqual(run["terminal_evidence"]["death_signature"], "killed_by:grid bug")
        self.assertEqual(run["terminal_evidence"]["same_death_total_count"], 2)
        self.assertEqual(run["candidate_nonhold_on_current_no_action"], 1)

        group = report["death_signature_correlations"][0]
        self.assertEqual(group["death_signature"], "killed_by:grid bug")
        self.assertEqual(group["runs"], 1)
        self.assertEqual(group["proposal_kind_counts"], {"descend": 1, "rest": 1})
        self.assertEqual(group["interpretation"], "correlation_only")

        self.assertNotIn("command", report)
        self.assertEqual(report["command_sha256"], self.manifest.command_hash)

    def test_intent_proposal_matrix_is_aggregated(self):
        run_id = self._run(status="ended", death_signature=None)
        first = self._event(run_id=run_id, proposal_kind="rest", evaluation="approved")
        second = self._event(run_id=run_id, proposal_kind="inspect", evaluation="rejected")
        second["current_decision"]["intent"] = "status_emergency"
        self._write_events(run_id, [first, second])
        report = evaluate_live_candidate_shadow(self.g, self.manifest, now=self.now)
        self.assertEqual(
            report["intent_proposal_counts"],
            {"status_emergency|inspect": 1, "survival_emergency|rest": 1},
        )

    def test_action_sent_or_policy_effect_violation_fails_integrity(self):
        run_id = self._run()
        bad = self._event(run_id=run_id, candidate_action_sent=True)
        bad2 = self._event(run_id=run_id, policy_effect="candidate")
        self._write_events(run_id, [bad, bad2])
        report = evaluate_live_candidate_shadow(self.g, self.manifest, now=self.now)
        self.assertEqual(report["total_events"], 0)
        self.assertEqual(report["invalid_identity_events"], 2)
        self.assertEqual(report["safety_violation_events"], 2)
        self.assertFalse(report["candidate_shadow_integrity_passed"])
        self.assertFalse(report["eligible_for_promotion_review"])

    def test_wrong_candidate_identity_is_excluded(self):
        run_id = self._run()
        wrong = self._event(run_id=run_id, command_sha256="0" * 64)
        good = self._event(run_id=run_id)
        self._write_events(run_id, [wrong, good])
        report = evaluate_live_candidate_shadow(self.g, self.manifest, now=self.now)
        self.assertEqual(report["invalid_identity_events"], 1)
        self.assertEqual(report["total_events"], 1)
        self.assertFalse(report["candidate_shadow_integrity_passed"])

    def test_unexpected_would_execute_action_marks_integrity_failure(self):
        run_id = self._run()
        self._write_events(run_id, [self._event(run_id=run_id, would_count=1)])
        report = evaluate_live_candidate_shadow(self.g, self.manifest, now=self.now)
        self.assertEqual(report["unexpected_would_execute_action_events"], 1)
        self.assertFalse(report["candidate_shadow_integrity_passed"])

    def test_malformed_lines_are_counted_without_inventing_events(self):
        run_id = self._run()
        path = self.log_root / f"{run_id}.jsonl"
        path.write_text(
            "not-json\n" + json.dumps(self._event(run_id=run_id)) + "\n",
            encoding="utf-8",
        )
        report = evaluate_live_candidate_shadow(self.g, self.manifest, now=self.now)
        self.assertEqual(report["malformed_lines"], 1)
        self.assertEqual(report["total_events"], 1)
        self.assertFalse(report["candidate_shadow_integrity_passed"])

    def test_untracked_log_remains_separate_from_terminal_runs(self):
        path = self.log_root / "untracked.jsonl"
        event = self._event(run_id=str(uuid.uuid4()))
        event.pop("run_id")
        path.write_text(json.dumps(event) + "\n", encoding="utf-8")
        report = evaluate_live_candidate_shadow(self.g, self.manifest, now=self.now)
        self.assertEqual(report["tracked_runs"], 0)
        self.assertEqual(report["terminal_correlated_runs"], 0)
        self.assertEqual(report["runs"][0]["run_id"], "untracked")

    def test_no_data_never_becomes_performance_or_promotion_evidence(self):
        report = evaluate_live_candidate_shadow(self.g, self.manifest, now=self.now)
        self.assertEqual(report["status"], "no_data")
        self.assertEqual(report["total_events"], 0)
        self.assertFalse(report["performance_improvement_assessed"])
        self.assertFalse(report["eligible_for_promotion_review"])
        self.assertEqual(report["policy_effect"], "none")
        summary = self.log_root / "summary.json"
        self.assertTrue(summary.is_file())


if __name__ == "__main__":
    unittest.main()

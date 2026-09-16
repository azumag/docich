from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from docich import config
from docich.nethack_candidate_eval import load_candidate_manifest
from docich.nethack_canary import (
    NethackCanaryError,
    NethackCanaryPlan,
    load_canary_plan,
    run_canary,
)
from docich.nethack_regression import _canonical_hash


class TestNethackControlledCanary(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "repo"
        self.playground = Path(self.tempdir.name) / "production-playground"
        self.save_dir = self.playground / "save"
        self.dump_dir = self.playground / "dumps"
        self.xlogfile = self.playground / "xlogfile"
        self.save_dir.mkdir(parents=True)
        self.dump_dir.mkdir(parents=True)
        self.xlogfile.write_text("production\n", encoding="utf-8")
        (self.root / "config" / "games").mkdir(parents=True)
        (self.root / "config" / "docich.toml").write_text(
            '[paths]\nstate_dir = "run"\ngames_dir = "config/games"\n',
            encoding="utf-8",
        )
        (self.root / "config" / "games" / "nethack.toml").write_text(
            '[game]\nname="nethack"\ntitle="NetHack"\nadapter="cli"\n'
            '[cli]\ncommand="nethack"\ncols=80\nrows=24\n'
            '[nethack]\npersistent_run=true\nplayer_name="docich"\n'
            f'save_dir="{self.save_dir}"\n'
            f'xlogfile="{self.xlogfile}"\n'
            f'dump_dir="{self.dump_dir}"\n',
            encoding="utf-8",
        )
        self.g = config.load_global(self.root)
        self.nh = self.g.state_dir / "nethack"
        self.regression = self.nh / "regression"
        self.regression.mkdir(parents=True)
        self.manifest_path = self.root / "candidate.json"
        self.manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "candidate_id": "cand-a",
                    "version": "v1",
                    "command": ["candidate", "--json"],
                    "timeout_s": 2.0,
                }
            ),
            encoding="utf-8",
        )
        self.manifest = load_candidate_manifest(self.manifest_path)
        cases = []
        suite_id = _canonical_hash({"schema_version": 1, "cases": cases})
        (self.regression / "suite.json").write_text(
            json.dumps(
                {
                    "suite_id": suite_id,
                    "schema_version": 1,
                    "generated_at": "2026-09-16T00:00:00+00:00",
                    "source": "test",
                    "cases": cases,
                    "policy_effect": "none",
                    "automatic_promotion": False,
                }
            ),
            encoding="utf-8",
        )
        report_dir = self.regression / "candidates" / self.manifest.candidate_id / self.manifest.version
        report_dir.mkdir(parents=True)
        (report_dir / f"{suite_id}.json").write_text(
            json.dumps(
                {
                    "candidate_id": self.manifest.candidate_id,
                    "candidate_version": self.manifest.version,
                    "candidate_fingerprint": self.manifest.fingerprint,
                    "command_sha256": self.manifest.command_hash,
                    "suite_id": suite_id,
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
        self.plan = NethackCanaryPlan(
            experiment_id="exp-a",
            candidate_manifest=str(self.manifest_path),
            worker_command=("fake-worker",),
            episodes_per_arm=2,
            min_completed_per_arm=2,
            episode_timeout_s=3.0,
            max_turns=5000,
            seed_base=100,
            require_seed_control=True,
            required_isolation_mode="container",
        )
        self.now = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def worker_result(self, request, *, status="dead", score=100, depth=3, death="killed by a grid bug"):
        arm = request["arm"]
        return {
            "schema_version": 1,
            "worker_status": "completed",
            "episode_id": request["episode_id"],
            "arm": arm,
            "isolation_mode": "container",
            "arena": request["arena"],
            "player_name": request["player_name"],
            "seed": request["seed"],
            "seed_applied": True,
            "controller_kind": "baseline_p3b" if arm == "baseline" else "candidate_strategist",
            "terminal_status": status,
            "score": score,
            "turns": 1000,
            "max_depth": depth,
            "death_reason": death if status == "dead" else None,
            "got_amulet": status == "ascended",
            "exit_reason": "terminal",
            "candidate_action_source": "baseline_p3b" if arm == "baseline" else "candidate_strategist",
            "production_state_touched": False,
        }

    def test_paired_baseline_candidate_results_are_descriptive_only(self) -> None:
        calls = []

        def fake_worker(g, plan, request):
            calls.append((request["episode_id"], request["arm"]))
            if request["arm"] == "candidate":
                return self.worker_result(request, score=250, depth=5, death="killed by a jackal")
            return self.worker_result(request, score=100, depth=3)

        with patch("docich.nethack_canary._run_worker", side_effect=fake_worker):
            report = run_canary(self.g, self.plan, now=self.now)

        self.assertEqual(
            calls,
            [("000", "baseline"), ("000", "candidate"), ("001", "candidate"), ("001", "baseline")],
        )
        self.assertTrue(report["safety_integrity_passed"])
        self.assertTrue(report["performance_evidence_available"])
        self.assertEqual(report["paired_seed_count"], 2)
        self.assertEqual(report["baseline"]["median_score"], 100.0)
        self.assertEqual(report["candidate"]["median_score"], 250.0)
        self.assertEqual(report["descriptive_differences"]["median_score_candidate_minus_baseline"], 150.0)
        self.assertTrue(report["comparison_is_descriptive_only"])
        self.assertFalse(report["statistical_significance_assessed"])
        self.assertFalse(report["eligible_for_promotion_review"])
        self.assertFalse(report["automatic_promotion"])
        self.assertEqual(report["policy_effect"], "none")
        encoded = json.dumps(report)
        self.assertNotIn("candidate --json", encoded)
        self.assertNotIn("fake-worker", encoded)
        self.assertEqual(len(report["candidate_command_sha256"]), 64)
        self.assertEqual(len(report["worker_command_sha256"]), 64)

    def test_production_mutation_aborts_experiment(self) -> None:
        calls = []

        def touching_worker(g, plan, request):
            calls.append(request["arm"])
            self.xlogfile.write_text("MUTATED\n", encoding="utf-8")
            return self.worker_result(request)

        with patch("docich.nethack_canary._run_worker", side_effect=touching_worker):
            report = run_canary(self.g, self.plan, now=self.now)
        self.assertTrue(report["aborted"])
        self.assertFalse(report["safety_integrity_passed"])
        self.assertTrue(any("production_fingerprint_changed" in item for item in report["safety_violations"]))
        self.assertEqual(len(calls), 1)
        self.assertFalse(report["performance_evidence_available"])

    def test_active_production_nethack_blocks_before_worker(self) -> None:
        self.g.state_dir.mkdir(parents=True, exist_ok=True)
        (self.g.state_dir / "game_switch.json").write_text(
            json.dumps({"phase": "ready", "active": {"game": "nethack"}}),
            encoding="utf-8",
        )
        with patch("docich.nethack_canary._run_worker") as worker:
            with self.assertRaises(NethackCanaryError):
                run_canary(self.g, self.plan, now=self.now)
        worker.assert_not_called()

    def test_worker_candidate_action_source_mismatch_fails_integrity(self) -> None:
        def fake_worker(g, plan, request):
            raw = self.worker_result(request)
            if request["arm"] == "candidate":
                raw["candidate_action_source"] = "baseline_p3b"
            return raw

        with patch("docich.nethack_canary._run_worker", side_effect=fake_worker):
            report = run_canary(self.g, self.plan, now=self.now)
        self.assertTrue(report["aborted"])
        self.assertFalse(report["safety_integrity_passed"])
        self.assertTrue(any("worker_integrity:candidate" in item for item in report["safety_violations"]))

    def test_required_seed_must_be_applied(self) -> None:
        def fake_worker(g, plan, request):
            raw = self.worker_result(request)
            raw["seed_applied"] = False
            return raw

        with patch("docich.nethack_canary._run_worker", side_effect=fake_worker):
            report = run_canary(self.g, self.plan, now=self.now)
        self.assertFalse(report["safety_integrity_passed"])
        self.assertTrue(report["aborted"])

    def test_timeout_is_not_safety_violation_but_is_not_performance_evidence(self) -> None:
        plan = NethackCanaryPlan(
            **{**self.plan.__dict__, "episodes_per_arm": 1, "min_completed_per_arm": 1, "require_seed_control": False}
        )

        def fake_worker(g, plan, request):
            return {"schema_version": 1, "worker_status": "timeout", "error": "worker timeout"}

        with patch("docich.nethack_canary._run_worker", side_effect=fake_worker):
            report = run_canary(self.g, plan, now=self.now)
        self.assertTrue(report["safety_integrity_passed"])
        self.assertFalse(report["performance_evidence_available"])
        self.assertEqual(report["baseline"]["timeouts"], 1)
        self.assertEqual(report["candidate"]["timeouts"], 1)

    def test_plan_is_strict_and_bounded(self) -> None:
        path = Path(self.tempdir.name) / "plan.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "experiment_id": "exp-1",
                    "candidate_manifest": str(self.manifest_path),
                    "worker_command": ["worker"],
                    "episodes_per_arm": 3,
                    "min_completed_per_arm": 2,
                    "max_turns": 1000,
                    "seed_base": 42,
                    "require_seed_control": True,
                    "required_isolation_mode": "container",
                }
            ),
            encoding="utf-8",
        )
        plan = load_canary_plan(path)
        self.assertEqual(plan.episodes_per_arm, 3)
        self.assertEqual(plan.seed_base, 42)

        bad = Path(self.tempdir.name) / "bad.json"
        bad.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "experiment_id": "exp-1",
                    "candidate_manifest": str(self.manifest_path),
                    "worker_command": ["worker"],
                    "auto_promote": True,
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(NethackCanaryError):
            load_canary_plan(bad)


if __name__ == "__main__":
    unittest.main()

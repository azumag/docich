from __future__ import annotations

import json
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

from docich import config
from docich.nethack_candidate_eval import (
    CandidateManifest,
    NethackCandidateError,
    evaluate_candidate,
    load_candidate_manifest,
    parse_public_replay_request,
)
from docich.nethack_regression import build_suite
from docich.nethack_strategist import CommandStrategist, StrategistDispatchResult
from docich.nethack_strategy import StrategicProposal


class FakeStrategist:
    def __init__(self, proposal=None, error=None, error_kind=None):
        self.proposal = proposal
        self.error = error
        self.error_kind = error_kind
        self.requests = []

    def dispatch(self, request):
        self.requests.append(request)
        if self.error is not None:
            return StrategistDispatchResult(
                status="error", error=self.error, error_kind=self.error_kind
            )
        return StrategistDispatchResult(status="proposed", proposal=self.proposal)


class TestNethackCandidateEval(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "repo"
        (self.root / "config" / "games").mkdir(parents=True)
        (self.root / "config" / "docich.toml").write_text(
            '[paths]\nstate_dir = "run"\ngames_dir = "config/games"\n', encoding="utf-8"
        )
        (self.root / "config" / "games" / "nethack.toml").write_text(
            '[game]\nname="nethack"\ntitle="NetHack"\nadapter="cli"\n'
            '[cli]\ncommand="nethack"\n',
            encoding="utf-8",
        )
        self.g = config.load_global(self.root)
        self.nh = self.root / "run" / "nethack"
        self.runs = self.nh / "runs"
        self.runs.mkdir(parents=True)
        self.now = datetime(2026, 9, 16, 11, 0, tzinfo=timezone.utc)
        self.start = 1_789_540_000
        self.end = 1_789_543_600

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    @staticmethod
    def run_lesson(key, category, evidence=None):
        return {
            "lesson_key": key,
            "source": "p5a_retrospective",
            "status": "candidate",
            "category": category,
            "text": category,
            "expedition": 1,
            "evidence": evidence or {},
            "policy_effect": "none",
        }

    def make_run(self, expedition, lesson):
        run_id = str(uuid.uuid4())
        payload = {
            "schema_version": 1,
            "run_id": run_id,
            "expedition": expedition,
            "status": "dead",
            "started_epoch": self.start,
            "last_finished_at": datetime.fromtimestamp(self.end, tz=timezone.utc).isoformat(),
            "death_reason": "killed by a grid bug",
            "terminal": {"source": "xlogfile", "endtime": self.end},
            "lessons": [lesson],
        }
        (self.runs / f"{run_id}.json").write_text(json.dumps(payload), encoding="utf-8")
        return run_id

    def write_memory(self, entries):
        self.nh.mkdir(parents=True, exist_ok=True)
        (self.nh / "lessons.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "updated_at": self.now.isoformat(),
                    "lessons": entries,
                    "policy_effect": "none",
                }
            ),
            encoding="utf-8",
        )

    @staticmethod
    def memory(key, category, run_id, evidence_count=1):
        return {
            "lesson_key": key,
            "source": "p5a_retrospective",
            "status": "candidate",
            "category": category,
            "text": category,
            "evidence_count": evidence_count,
            "first_expedition": 1,
            "last_expedition": max(1, evidence_count),
            "run_ids": [run_id],
            "policy_effect": "none",
        }

    def build_survival_suite(self, *, with_hidden_replay=False):
        key = "a" * 20
        run_id = self.make_run(1, self.run_lesson(key, "survival_signal"))
        self.write_memory([self.memory(key, "survival_signal", run_id)])
        if with_hidden_replay:
            path = self.nh / "strategist" / "advisory.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            request = {
                "schema_version": 1,
                "intent": "survival_emergency",
                "reason": "critical",
                "observation": {
                    "message": "msg",
                    "prompt": "none",
                    "player": [3, 0],
                    "vitals": {"hp": 1, "hp_max": 10, "hp_ratio": 0.1},
                    "conditions": [],
                    "local_map": ["..@.."],
                    "hidden_map": ["secret"],
                },
                "inventory": [],
                "constraints": [],
            }
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "ts": self.end - 1,
                        "intent": "survival_emergency",
                        "request": request,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
        return build_suite(self.g, now=self.now)

    @staticmethod
    def manifest(**kwargs):
        values = dict(
            candidate_id="candidate-a",
            version="v1",
            command=("fake-candidate",),
            timeout_s=2.0,
            max_request_bytes=32768,
            max_response_bytes=16384,
            max_cases=64,
            min_replay_cases=1,
            expected_suite_id=None,
        )
        values.update(kwargs)
        return CandidateManifest(**values)

    @staticmethod
    def public_request_with_hp_ratio(hp_ratio):
        return {
            "schema_version": 1,
            "intent": "survival_emergency",
            "reason": "critical",
            "observation": {
                "message": "danger",
                "prompt": "none",
                "player": [2, 1],
                "vitals": {"hp": 2, "hp_max": 10, "hp_ratio": hp_ratio},
                "conditions": [],
                "local_map": ["..@.."],
            },
            "inventory": [],
            "constraints": [],
        }

    def test_safe_rest_candidate_passes_behavior_review_but_never_promotion(self):
        suite = self.build_survival_suite()
        fake = FakeStrategist(
            StrategicProposal(schema_version=1, kind="rest", rationale="wait one turn with dot")
        )
        report = evaluate_candidate(self.g, self.manifest(), strategist=fake, now=self.now)
        self.assertEqual(len(fake.requests), 1)
        self.assertEqual(report["suite_id"], suite["suite_id"])
        self.assertTrue(report["candidate_safety_contract_passed"])
        self.assertTrue(report["eligible_for_behavior_review"])
        self.assertFalse(report["eligible_for_promotion_review"])
        self.assertFalse(report["performance_improvement_assessed"])
        self.assertFalse(report["automatic_promotion"])
        self.assertEqual(report["policy_effect"], "none")
        self.assertNotIn("command", report)
        self.assertEqual(len(report["command_sha256"]), 64)
        self.assertEqual(report["results"][0]["execution_action_count"], 1)

    def test_disallowed_candidate_kind_is_rejected(self):
        self.build_survival_suite()
        fake = FakeStrategist(
            StrategicProposal(schema_version=1, kind="descend", rationale="go deeper")
        )
        report = evaluate_candidate(self.g, self.manifest(), strategist=fake, now=self.now)
        self.assertFalse(report["candidate_safety_contract_passed"])
        self.assertEqual(report["rejected_proposals"], 1)
        self.assertEqual(report["results"][0]["evaluation_status"], "rejected")
        self.assertEqual(report["results"][0]["execution_action_count"], 0)

    def test_dispatch_error_fails_candidate_contract(self):
        self.build_survival_suite()
        fake = FakeStrategist(error="provider down")
        report = evaluate_candidate(self.g, self.manifest(), strategist=fake, now=self.now)
        self.assertFalse(report["candidate_safety_contract_passed"])
        self.assertEqual(report["dispatch_errors"], 1)
        self.assertEqual(report["results"][0]["status"], "dispatch_error")

    def test_dispatch_error_report_persists_category_not_raw_detail(self):
        self.build_survival_suite()
        sentinel = "SECRET-candidate-report-error-4c2d"
        fake = FakeStrategist(
            error=f"provider down: {sentinel}",
            error_kind="process_failed",
        )
        report = evaluate_candidate(self.g, self.manifest(), strategist=fake, now=self.now)
        result = report["results"][0]
        self.assertEqual(result["error_kind"], "process_failed")
        self.assertNotIn("error", result)
        report_paths = list((self.nh / "regression" / "candidates").rglob("*.json"))
        self.assertEqual(len(report_paths), 1)
        persisted = report_paths[0].read_text(encoding="utf-8")
        self.assertNotIn(sentinel, persisted)
        self.assertNotIn('"error":', persisted)

    def test_dispatch_exception_report_fails_closed_without_raw_detail(self):
        self.build_survival_suite()
        sentinel = "SECRET-candidate-exception-71ef"

        class ExplodingStrategist:
            def dispatch(self, request):
                raise RuntimeError(f"candidate exploded: {sentinel}")

        report = evaluate_candidate(
            self.g,
            self.manifest(),
            strategist=ExplodingStrategist(),
            now=self.now,
        )
        self.assertEqual(report["results"][0]["error_kind"], "internal_error")
        self.assertNotIn("error", report["results"][0])
        report_paths = list((self.nh / "regression" / "candidates").rglob("*.json"))
        self.assertEqual(len(report_paths), 1)
        persisted = report_paths[0].read_text(encoding="utf-8")
        self.assertNotIn(sentinel, persisted)

    def test_stderr_secret_is_not_persisted_in_candidate_report(self):
        self.build_survival_suite()
        sentinel = "SECRET-candidate-stderr-82ab"
        strategist = CommandStrategist(
            [
                sys.executable,
                "-c",
                f"import sys; sys.stderr.write({sentinel!r}); sys.exit(3)",
            ],
            timeout_s=2.0,
        )
        report = evaluate_candidate(self.g, self.manifest(), strategist=strategist, now=self.now)
        self.assertEqual(report["results"][0]["error_kind"], "process_failed")
        report_paths = list((self.nh / "regression" / "candidates").rglob("*.json"))
        self.assertEqual(len(report_paths), 1)
        persisted = report_paths[0].read_text(encoding="utf-8")
        self.assertNotIn(sentinel, persisted)

    def test_hidden_or_unknown_recorded_request_is_never_forwarded(self):
        suite = self.build_survival_suite(with_hidden_replay=True)
        self.assertIsNotNone(suite["cases"][0]["replay_request"])
        fake = FakeStrategist(
            StrategicProposal(schema_version=1, kind="rest", rationale="wait one turn with dot")
        )
        report = evaluate_candidate(self.g, self.manifest(), strategist=fake, now=self.now)
        self.assertTrue(report["candidate_safety_contract_passed"])
        self.assertEqual(report["results"][0]["request_source"], "synthetic_public_fixture")
        self.assertEqual(report["results"][0]["execution_action_count"], 1)
        self.assertNotIn("hidden_map", fake.requests[0].observation)

    def test_public_replay_rejects_non_finite_or_out_of_range_hp_ratio(self):
        for hp_ratio in (float("nan"), float("inf"), float("-inf"), -0.01, 1.01):
            with self.subTest(hp_ratio=hp_ratio):
                with self.assertRaises(NethackCandidateError):
                    parse_public_replay_request(self.public_request_with_hp_ratio(hp_ratio))

        request, observation, _ = parse_public_replay_request(
            self.public_request_with_hp_ratio(0.2)
        )
        self.assertEqual(request.observation["vitals"]["hp_ratio"], 0.2)
        self.assertEqual(observation.vitals.hp_ratio, 0.2)

    def test_baseline_failure_blocks_candidate_before_dispatch(self):
        key = "b" * 20
        run_id = self.make_run(
            1,
            self.run_lesson(
                key,
                "repeated_death",
                {"death_signature": "killed_by:grid bug", "total": 1},
            ),
        )
        self.write_memory([self.memory(key, "repeated_death", run_id)])
        build_suite(self.g, now=self.now)
        fake = FakeStrategist(
            StrategicProposal(schema_version=1, kind="rest", rationale="wait one turn with dot")
        )
        report = evaluate_candidate(self.g, self.manifest(), strategist=fake, now=self.now)
        self.assertEqual(report["status"], "blocked_baseline")
        self.assertEqual(fake.requests, [])
        self.assertFalse(report["eligible_for_behavior_review"])

    def test_expected_suite_id_prevents_accidental_cross_suite_replay(self):
        self.build_survival_suite()
        fake = FakeStrategist(
            StrategicProposal(schema_version=1, kind="rest", rationale="wait one turn with dot")
        )
        with self.assertRaises(NethackCandidateError):
            evaluate_candidate(
                self.g,
                self.manifest(expected_suite_id="0" * 64),
                strategist=fake,
                now=self.now,
            )
        self.assertEqual(fake.requests, [])

    def test_incomplete_coverage_cannot_pass_candidate_contract(self):
        key1, key2 = "c" * 20, "d" * 20
        run1 = self.make_run(1, self.run_lesson(key1, "survival_signal"))
        run2 = self.make_run(2, self.run_lesson(key2, "food_survival"))
        self.write_memory(
            [
                self.memory(key1, "survival_signal", run1),
                self.memory(key2, "food_survival", run2),
            ]
        )
        build_suite(self.g, now=self.now)
        fake = FakeStrategist(
            StrategicProposal(schema_version=1, kind="rest", rationale="wait one turn with dot")
        )
        report = evaluate_candidate(
            self.g,
            self.manifest(max_cases=1),
            strategist=fake,
            now=self.now,
        )
        self.assertEqual(report["replayable_cases"], 2)
        self.assertEqual(report["evaluated_cases"], 1)
        self.assertFalse(report["coverage_complete"])
        self.assertFalse(report["candidate_safety_contract_passed"])

    def test_manifest_is_strict_and_versioned(self):
        path = Path(self.tempdir.name) / "candidate.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "candidate_id": "cand",
                    "version": "v2",
                    "command": ["candidate", "--json"],
                    "timeout_s": 4,
                }
            ),
            encoding="utf-8",
        )
        manifest = load_candidate_manifest(path)
        self.assertEqual(manifest.candidate_id, "cand")
        self.assertEqual(manifest.version, "v2")
        self.assertEqual(manifest.command, ("candidate", "--json"))

        bad = Path(self.tempdir.name) / "bad.json"
        bad.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "candidate_id": "cand",
                    "version": "v2",
                    "command": ["candidate"],
                    "auto_promote": True,
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(NethackCandidateError):
            load_candidate_manifest(bad)


if __name__ == "__main__":
    unittest.main()

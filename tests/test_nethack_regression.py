from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

from docich import config
from docich.nethack_regression import (
    NethackRegressionError,
    build_suite,
    evaluate_suite,
)


class TestNethackRegression(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "repo"
        (self.root / "config" / "games").mkdir(parents=True)
        (self.root / "config" / "docich.toml").write_text(
            '[paths]\nstate_dir = "run"\ngames_dir = "config/games"\n',
            encoding="utf-8",
        )
        (self.root / "config" / "games" / "nethack.toml").write_text(
            '[game]\nname = "nethack"\ntitle = "NetHack"\nadapter = "cli"\n'
            '[cli]\ncommand = "nethack"\n',
            encoding="utf-8",
        )
        self.g = config.load_global(self.root)
        self.nh = self.root / "run" / "nethack"
        self.runs = self.nh / "runs"
        self.runs.mkdir(parents=True)
        self.now = datetime(2026, 9, 16, 10, 30, tzinfo=timezone.utc)
        self.start = 1_789_540_000
        self.end = 1_789_543_600

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _run(
        self,
        expedition: int,
        lessons: list[dict[str, object]],
        *,
        death_reason: str = "killed by a water elemental",
    ) -> str:
        run_id = str(uuid.uuid4())
        payload = {
            "schema_version": 1,
            "run_id": run_id,
            "expedition": expedition,
            "status": "dead",
            "started_epoch": self.start,
            "last_finished_at": datetime.fromtimestamp(self.end, tz=timezone.utc).isoformat(),
            "death_reason": death_reason,
            "terminal": {"source": "xlogfile", "endtime": self.end},
            "lessons": lessons,
        }
        (self.runs / f"{run_id}.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        return run_id

    @staticmethod
    def _run_lesson(key: str, category: str, evidence: dict[str, object]) -> dict[str, object]:
        return {
            "lesson_key": key,
            "source": "p5a_retrospective",
            "status": "candidate",
            "category": category,
            "text": f"lesson {category}",
            "expedition": 1,
            "evidence": evidence,
            "policy_effect": "none",
        }

    @staticmethod
    def _memory_lesson(
        key: str, category: str, run_ids: list[str], *, evidence_count: int = 1
    ) -> dict[str, object]:
        return {
            "lesson_key": key,
            "source": "p5a_retrospective",
            "status": "candidate",
            "category": category,
            "text": f"lesson {category}",
            "evidence_count": evidence_count,
            "first_expedition": 1,
            "last_expedition": max(1, evidence_count),
            "run_ids": run_ids,
            "policy_effect": "none",
        }

    def _write_memory(self, lessons: list[dict[str, object]]) -> None:
        (self.nh / "lessons.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "updated_at": self.now.isoformat(),
                    "lessons": lessons,
                    "policy_effect": "none",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def _advisory(self, events: list[dict[str, object]]) -> None:
        path = self.nh / "strategist" / "advisory.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as stream:
            for event in events:
                stream.write(json.dumps({"schema_version": 1, **event}) + "\n")

    def test_builds_scored_cases_and_current_contracts_pass(self) -> None:
        keys = {
            "survival": "a" * 20,
            "food": "b" * 20,
            "drift": "c" * 20,
            "repeat": "d" * 20,
            "unknown": "e" * 20,
            "gap": "f" * 20,
        }
        run_id = self._run(
            2,
            [
                self._run_lesson(keys["survival"], "survival_signal", {"death_signature": "killed_by:water elemental"}),
                self._run_lesson(keys["food"], "food_survival", {"death_signature": "starvation"}),
                self._run_lesson(keys["drift"], "proposal_drift", {"rejected_evaluations": 1}),
                self._run_lesson(
                    keys["repeat"],
                    "repeated_death",
                    {"death_signature": "killed_by:water elemental", "prior_expeditions": [1], "total": 2},
                ),
                self._run_lesson(keys["unknown"], "terminal_evidence", {"terminal": {"analysis_error": "xlogfile-missing"}}),
                self._run_lesson(keys["gap"], "evidence_gap", {"advisory_status": "missing"}),
            ],
        )
        self._write_memory(
            [
                self._memory_lesson(keys["survival"], "survival_signal", [run_id]),
                self._memory_lesson(keys["food"], "food_survival", [run_id]),
                self._memory_lesson(keys["drift"], "proposal_drift", [run_id]),
                self._memory_lesson(keys["repeat"], "repeated_death", [run_id]),
                self._memory_lesson(keys["unknown"], "terminal_evidence", [run_id]),
                self._memory_lesson(keys["gap"], "evidence_gap", [run_id]),
            ]
        )
        suite = build_suite(self.g, now=self.now)
        self.assertEqual(len(suite["cases"]), 6)
        self.assertEqual(suite["policy_effect"], "none")
        self.assertIs(suite["automatic_promotion"], False)
        report = evaluate_suite(self.g, now=self.now)
        self.assertEqual(report["scored_cases"], 6)
        self.assertEqual(report["failed_cases"], 0)
        self.assertTrue(report["baseline_contract_passed"])
        self.assertTrue(report["ready_for_candidate_evaluation"])
        self.assertEqual(report["policy_effect"], "none")
        self.assertIs(report["automatic_promotion"], False)

    def test_public_strategic_request_is_attached_for_future_replay(self) -> None:
        key = "1" * 20
        run_id = self._run(
            1,
            [self._run_lesson(key, "survival_signal", {"death_signature": "killed_by:grid bug"})],
        )
        self._write_memory([self._memory_lesson(key, "survival_signal", [run_id])])
        public_request = {
            "schema_version": 1,
            "intent": "survival_emergency",
            "reason": "visible HP is critical",
            "observation": {"vitals": {"hp": 1, "hp_max": 10}, "conditions": []},
            "inventory": [],
            "constraints": ["public only"],
        }
        self._advisory(
            [
                {"ts": self.start - 100, "intent": "survival_emergency", "request": {"bad": "outside-window"}},
                {"ts": self.end - 10, "intent": "survival_emergency", "request": public_request},
            ]
        )
        suite = build_suite(self.g, now=self.now)
        case = suite["cases"][0]
        self.assertEqual(case["replay_request"], public_request)
        self.assertEqual(case["fixture"]["origin"], "category_template")

    def test_proposal_drift_fixture_rejects_stale_inventory_letter(self) -> None:
        key = "2" * 20
        run_id = self._run(1, [self._run_lesson(key, "proposal_drift", {"rejected_evaluations": 1})])
        self._write_memory([self._memory_lesson(key, "proposal_drift", [run_id])])
        build_suite(self.g, now=self.now)
        report = evaluate_suite(self.g, now=self.now)
        result = report["results"][0]
        self.assertTrue(result["passed"])
        self.assertIn("evaluation=rejected", result["detail"])
        self.assertIn("execution_allowed=False", result["detail"])

    def test_repeated_death_case_requires_repeat_evidence(self) -> None:
        key = "3" * 20
        run_id = self._run(
            1,
            [
                self._run_lesson(
                    key,
                    "repeated_death",
                    {"death_signature": "killed_by:grid bug", "prior_expeditions": [], "total": 1},
                )
            ],
        )
        self._write_memory([self._memory_lesson(key, "repeated_death", [run_id])])
        build_suite(self.g, now=self.now)
        report = evaluate_suite(self.g, now=self.now)
        self.assertEqual(report["failed_cases"], 1)
        self.assertFalse(report["baseline_contract_passed"])

    def test_unknown_category_is_informational_and_never_promotes(self) -> None:
        key = "4" * 20
        run_id = self._run(1, [self._run_lesson(key, "future_category", {})])
        self._write_memory([self._memory_lesson(key, "future_category", [run_id])])
        suite = build_suite(self.g, now=self.now)
        case = suite["cases"][0]
        self.assertFalse(case["scored"])
        self.assertEqual(case["kind"], "manual_review")
        report = evaluate_suite(self.g, now=self.now)
        self.assertEqual(report["informational_cases"], 1)
        self.assertFalse(report["ready_for_candidate_evaluation"])
        self.assertIs(report["automatic_promotion"], False)

    def test_suite_hash_detects_fixture_tampering(self) -> None:
        key = "5" * 20
        run_id = self._run(1, [self._run_lesson(key, "survival_signal", {})])
        self._write_memory([self._memory_lesson(key, "survival_signal", [run_id])])
        build_suite(self.g, now=self.now)
        path = self.nh / "regression" / "suite.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["cases"][0]["fixture"]["tty"] = "tampered"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(NethackRegressionError):
            evaluate_suite(self.g, now=self.now)

    def test_non_candidate_or_policy_effecting_memory_is_rejected(self) -> None:
        run_id = str(uuid.uuid4())
        self._write_memory(
            [
                {
                    "lesson_key": "6" * 20,
                    "source": "p5a_retrospective",
                    "status": "promoted",
                    "category": "survival_signal",
                    "text": "bad",
                    "evidence_count": 1,
                    "first_expedition": 1,
                    "last_expedition": 1,
                    "run_ids": [run_id],
                    "policy_effect": "active",
                }
            ]
        )
        with self.assertRaises(NethackRegressionError):
            build_suite(self.g, now=self.now)


if __name__ == "__main__":
    unittest.main()

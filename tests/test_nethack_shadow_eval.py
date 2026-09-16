from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from docich.nethack_shadow_eval import (
    ReadinessCriteria,
    evaluate_jsonl_file,
    evaluate_jsonl_lines,
)


def event(
    status: str,
    *,
    source: str = "source-a",
    compared: int = 100,
    mismatched: int = 0,
    fields=(),
):
    return json.dumps(
        {
            "schema_version": 1,
            "status": status,
            "source": source,
            "mismatches": list(fields),
            "map_cells_compared": compared,
            "map_cells_mismatched": mismatched,
            "policy_effect": "none",
        }
    )


class TestShadowEvaluation(unittest.TestCase):
    def test_perfect_evidence_can_only_become_eligible_for_review(self) -> None:
        criteria = ReadinessCriteria(
            min_comparable=3,
            min_observation_match_rate=1.0,
            min_map_cell_match_rate=1.0,
            max_stale_rate=0.0,
        )
        report = evaluate_jsonl_lines(
            [event("match"), event("match"), event("match")],
            criteria=criteria,
        )
        self.assertEqual(len(report.sources), 1)
        metrics = report.sources[0]
        self.assertTrue(metrics.eligible_for_review)
        self.assertEqual(metrics.observation_match_rate, 1.0)
        self.assertEqual(metrics.map_cell_match_rate, 1.0)
        self.assertEqual(metrics.readiness_reasons, ())
        self.assertEqual(report.to_dict()["policy_effect"], "none")
        self.assertNotIn("promote", json.dumps(report.to_dict()).lower())

    def test_mismatch_fields_and_rates_are_aggregated(self) -> None:
        criteria = ReadinessCriteria(
            min_comparable=2,
            min_observation_match_rate=0.9,
            min_map_cell_match_rate=0.99,
            max_stale_rate=0.1,
        )
        report = evaluate_jsonl_lines(
            [
                event("match", compared=100),
                event(
                    "mismatch",
                    compared=100,
                    mismatched=5,
                    fields=("vitals.hp", "map_cells"),
                ),
            ],
            criteria=criteria,
        )
        metrics = report.sources[0]
        self.assertEqual(metrics.comparable, 2)
        self.assertEqual(metrics.matches, 1)
        self.assertEqual(metrics.mismatches, 1)
        self.assertEqual(metrics.observation_match_rate, 0.5)
        self.assertEqual(metrics.map_cell_match_rate, 0.975)
        self.assertEqual(dict(metrics.mismatch_fields)["vitals.hp"], 1)
        self.assertFalse(metrics.eligible_for_review)
        self.assertTrue(metrics.readiness_reasons)

    def test_sources_are_kept_separate(self) -> None:
        criteria = ReadinessCriteria(
            min_comparable=1,
            min_observation_match_rate=1.0,
            min_map_cell_match_rate=1.0,
            max_stale_rate=0.0,
        )
        report = evaluate_jsonl_lines(
            [event("match", source="a"), event("mismatch", source="b", fields=("prompt",))],
            criteria=criteria,
        )
        by_source = {source.source: source for source in report.sources}
        self.assertTrue(by_source["a"].eligible_for_review)
        self.assertFalse(by_source["b"].eligible_for_review)

    def test_stale_and_invalid_events_block_readiness(self) -> None:
        criteria = ReadinessCriteria(
            min_comparable=1,
            min_observation_match_rate=1.0,
            min_map_cell_match_rate=1.0,
            max_stale_rate=0.0,
        )
        report = evaluate_jsonl_lines(
            [event("match"), event("stale"), event("invalid")],
            criteria=criteria,
        )
        metrics = report.sources[0]
        self.assertEqual(metrics.stale, 1)
        self.assertEqual(metrics.invalid, 1)
        self.assertFalse(metrics.eligible_for_review)
        self.assertTrue(any("stale" in reason for reason in metrics.readiness_reasons))
        self.assertTrue(any("invalid" in reason for reason in metrics.readiness_reasons))

    def test_malformed_and_unrelated_events_are_counted_but_not_crashing(self) -> None:
        report = evaluate_jsonl_lines(
            [
                "not-json",
                json.dumps({"schema_version": 99, "status": "match"}),
                json.dumps({"schema_version": 1, "status": "disabled"}),
                "",
            ]
        )
        self.assertEqual(report.malformed_lines, 2)
        self.assertEqual(report.ignored_events, 1)
        self.assertEqual(report.sources, ())

    def test_file_size_limit_and_threshold_validation(self) -> None:
        with self.assertRaises(ValueError):
            ReadinessCriteria(min_comparable=0)
        with self.assertRaises(ValueError):
            ReadinessCriteria(min_observation_match_rate=1.1)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "comparisons.jsonl"
            path.write_text(event("match") + "\n", encoding="utf-8")
            report = evaluate_jsonl_file(
                path,
                criteria=ReadinessCriteria(
                    min_comparable=1,
                    min_observation_match_rate=1.0,
                    min_map_cell_match_rate=1.0,
                    max_stale_rate=0.0,
                ),
            )
            self.assertTrue(report.sources[0].eligible_for_review)

            path.write_text("x" * 2048, encoding="utf-8")
            with self.assertRaises(ValueError):
                evaluate_jsonl_file(path, max_bytes=1024)


if __name__ == "__main__":
    unittest.main()

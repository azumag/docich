import importlib.util
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
VMOPS = ROOT / "ops" / "vm_actions"
sys.path.insert(0, str(VMOPS))

import summarize_runtime_queue_attribution as attribution  # noqa: E402


def load_collector():
    spec = importlib.util.spec_from_file_location(
        "budget_detail_collect_diagnostics", VMOPS / "collect_diagnostics.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BudgetExhaustedDetailProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.soren = Path(self.tmp.name) / "soren"
        self.now = int(time.time())
        self.stats = self.soren / "tmp" / "state" / "ai_stats"
        self.stats.mkdir(parents=True)
        self.collector = load_collector()

    def write_events(self, events):
        day = time.strftime("%Y%m%d", time.localtime(self.now))
        with open(self.stats / f"{day}.jsonl", "w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event) + "\n")

    def budget(self, label, error=None):
        event = {
            "ts": self.now,
            "event": "budget_exhausted",
            "label": label,
            "agent": "vercel:SUPERSECRET_MODEL",
            "rc": "99",
        }
        if error is not None:
            event["error"] = error
        return event

    def test_collects_only_bounded_fixed_radio_detail(self):
        self.write_events(
            [
                self.budget("RADIO:private-topic:prepass", "exec=1;skip=2;last_budget=17;rem=0"),
                self.budget("RADIO:private-topic:main", "exec=2;skip=1;last_budget=43;rem=0"),
                self.budget("RADIO:private-topic:main", "exec=1;skip=3;last_budget=7;rem=0"),
            ]
        )
        ai = self.collector._collect_ai(self.soren, self.now)
        self.assertEqual(ai["budget_exhausted_detail_sampled"], 3)
        self.assertEqual(ai["budget_exhausted_detail_malformed"], 0)
        self.assertEqual(ai["budget_exhausted_detail_missing"], 0)
        self.assertEqual(
            ai["budget_exhausted_detail_components"]["radio_prepass"],
            {
                "sampled": 1,
                "exec_sum": 1,
                "skip_sum": 2,
                "last_budget_min_sec": 17,
                "last_budget_max_sec": 17,
            },
        )
        self.assertEqual(
            ai["budget_exhausted_detail_components"]["radio_main"],
            {
                "sampled": 2,
                "exec_sum": 3,
                "skip_sum": 4,
                "last_budget_min_sec": 7,
                "last_budget_max_sec": 43,
            },
        )
        rendered = json.dumps(ai)
        self.assertNotIn("private-topic", rendered)
        self.assertNotIn("SUPERSECRET_MODEL", rendered)

    def test_malformed_and_legacy_detail_fail_closed_without_raw_leak(self):
        self.write_events(
            [
                self.budget("RADIO:topic:main", "exec=100;skip=1;last_budget=20;rem=0"),
                self.budget("RADIO:topic:main", "exec=1;skip=1;last_budget=241;rem=0"),
                self.budget("RADIO:topic:prepass", "exec=1;skip=1;last_budget=20;rem=1"),
                self.budget("RADIO:topic:prepass", "token=SUPERSECRET provider=vercel"),
                self.budget("RADIO:topic:main"),
            ]
        )
        ai = self.collector._collect_ai(self.soren, self.now)
        self.assertEqual(ai["budget_exhausted_detail_sampled"], 0)
        self.assertEqual(ai["budget_exhausted_detail_malformed"], 4)
        self.assertEqual(ai["budget_exhausted_detail_missing"], 1)
        self.assertEqual(
            sum(row["sampled"] for row in ai["budget_exhausted_detail_components"].values()),
            0,
        )
        rendered = json.dumps(ai)
        self.assertNotIn("SUPERSECRET", rendered)
        self.assertNotIn("provider=vercel", rendered)

    def test_public_summary_exposes_fixed_detail_only(self):
        data = {
            "status": "ok",
            "workers": {},
            "queues": {"queue_giveups_15m": 0},
            "ai": {
                "budget_exhausted_15m": 3,
                "budget_exhausted_components": {
                    "radio_prepass": 1,
                    "radio_main": 2,
                    "news_spam_check": 0,
                    "comment": 0,
                    "improvement": 0,
                    "other": 0,
                },
                "budget_exhausted_detail_sampled": 2,
                "budget_exhausted_detail_malformed": 1,
                "budget_exhausted_detail_missing": 0,
                "budget_exhausted_detail_components": {
                    "radio_prepass": {
                        "sampled": 1,
                        "exec_sum": 1,
                        "skip_sum": 2,
                        "last_budget_min_sec": 17,
                        "last_budget_max_sec": 17,
                    },
                    "radio_main": {
                        "sampled": 1,
                        "exec_sum": 2,
                        "skip_sum": 1,
                        "last_budget_min_sec": 43,
                        "last_budget_max_sec": 43,
                    },
                    "SUPERSECRET_COMPONENT": {"sampled": 999},
                },
                "recent_events": [],
            },
            "improvement": {},
            "corners": {},
            "storage_artifacts": {},
            "bundle_storage": {},
        }
        severity, summary = attribution.render(data)
        self.assertEqual(severity, "ok")
        self.assertIn("ai_budget_exhausted_detail_sampled=2", summary)
        self.assertIn("ai_budget_exhausted_detail_malformed=1", summary)
        self.assertIn("ai_budget_exhausted_detail_coverage_exact=1", summary)
        self.assertIn("ai_budget_exhausted_detail_radio_prepass_exec_sum=1", summary)
        self.assertIn("ai_budget_exhausted_detail_radio_main_skip_sum=1", summary)
        self.assertIn("ai_budget_exhausted_detail_radio_main_last_budget_max_sec=43", summary)
        self.assertNotIn("SUPERSECRET", summary)

    def test_inconsistent_detail_projection_zeroes_component_values(self):
        sampled, malformed, missing, rows, consistent, coverage_exact = (
            attribution.budget_exhausted_detail_metrics(
                {
                    "ai": {
                        "budget_exhausted_components": {
                            "radio_prepass": 0,
                            "radio_main": 1,
                        },
                        "budget_exhausted_detail_sampled": 1,
                        "budget_exhausted_detail_malformed": 0,
                        "budget_exhausted_detail_missing": 0,
                        "budget_exhausted_detail_components": {
                            "radio_prepass": {
                                "sampled": 0,
                                "exec_sum": 0,
                                "skip_sum": 0,
                                "last_budget_min_sec": 0,
                                "last_budget_max_sec": 0,
                            },
                            "radio_main": {
                                "sampled": 1,
                                "exec_sum": 100,
                                "skip_sum": 0,
                                "last_budget_min_sec": 20,
                                "last_budget_max_sec": 20,
                            },
                        },
                    }
                }
            )
        )
        self.assertEqual((sampled, malformed, missing), (1, 0, 0))
        self.assertFalse(consistent)
        self.assertFalse(coverage_exact)
        self.assertEqual(sum(row["sampled"] for row in rows.values()), 0)


if __name__ == "__main__":
    unittest.main()

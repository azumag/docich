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
        "budget_exhausted_collect_diagnostics", VMOPS / "collect_diagnostics.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BudgetExhaustedDiagnosticsTests(unittest.TestCase):
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

    def budget(self, label, **extra):
        event = {
            "ts": self.now,
            "event": "budget_exhausted",
            "label": label,
            "agent": "vercel:SUPERSECRET_MODEL",
            "rc": "99",
            "error": "token=SUPERSECRET_TOKEN provider=SUPERSECRET_PROVIDER",
        }
        event.update(extra)
        return event

    def test_collects_only_fixed_component_counts_and_hides_dynamic_fields(self):
        self.write_events(
            [
                self.budget("RADIO:private-topic:prepass"),
                self.budget("RADIO:private-topic:main"),
                self.budget("news:spam_check:private-topic"),
                self.budget("comment:private-topic"),
                self.budget("improvement:private-topic"),
                self.budget("totally-private-component"),
            ]
        )
        ai = self.collector._collect_ai(self.soren, self.now)
        self.assertEqual(ai["budget_exhausted"], 6)
        self.assertEqual(
            ai["budget_exhausted_components"],
            {
                "radio_prepass": 1,
                "radio_main": 1,
                "news_spam_check": 1,
                "comment": 1,
                "improvement": 1,
                "other": 1,
            },
        )
        self.assertEqual(ai["recent_events"], [])
        rendered = json.dumps(ai)
        self.assertNotIn("SUPERSECRET", rendered)
        self.assertNotIn("private-topic", rendered)
        self.assertNotIn("totally-private-component", rendered)

    def test_missing_event_keeps_zero_counters(self):
        self.write_events(
            [
                {
                    "ts": self.now,
                    "event": "attempt",
                    "label": "radio:main",
                    "agent": "local:m1",
                },
                {
                    "ts": self.now,
                    "event": "unknown_new_event",
                    "label": "SUPERSECRET_LABEL",
                    "error": "SUPERSECRET_ERROR",
                },
            ]
        )
        ai = self.collector._collect_ai(self.soren, self.now)
        self.assertEqual(ai["budget_exhausted"], 0)
        self.assertEqual(sum(ai["budget_exhausted_components"].values()), 0)
        self.assertNotIn("SUPERSECRET", json.dumps(ai))

    def test_budget_exhaustion_alone_does_not_raise_runtime_severity(self):
        workers = {
            "required_down": [],
            "required_stale": [],
            "paused": [],
            "pause_ownership": {},
            "unregistered": [],
            "unregistered_health": {},
            "duplicates": [],
            "zombies": [],
        }
        queues = {"stale_locks": 0}
        ai = {"all_failed": 0, "queue_giveups": 0, "budget_exhausted": 4}
        improvement = {"stale": False, "retry_pending": False}
        self.assertEqual(self.collector._severity(workers, queues, ai, improvement), "ok")

    def test_public_summary_exposes_only_fixed_budget_counters(self):
        data = {
            "status": "ok",
            "workers": {},
            "queues": {"queue_giveups_15m": 0},
            "ai": {
                "budget_exhausted_15m": 3,
                "budget_exhausted_components": {
                    "radio_prepass": 2,
                    "radio_main": 1,
                    "news_spam_check": 0,
                    "comment": 0,
                    "improvement": 0,
                    "other": 0,
                    "SUPERSECRET_COMPONENT": 999,
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
        self.assertIn("ai_budget_exhausted_15m=3", summary)
        self.assertIn("ai_budget_exhausted_attribution_consistent=1", summary)
        self.assertIn("ai_budget_exhausted_component_radio_prepass=2", summary)
        self.assertIn("ai_budget_exhausted_component_radio_main=1", summary)
        self.assertNotIn("SUPERSECRET", summary)

    def test_inconsistent_public_attribution_fails_closed(self):
        total, counts, consistent = attribution.budget_exhausted_metrics(
            {
                "ai": {
                    "budget_exhausted_15m": 2,
                    "budget_exhausted_components": {"radio_main": 7},
                }
            }
        )
        self.assertEqual(total, 2)
        self.assertFalse(consistent)
        self.assertEqual(sum(counts.values()), 0)


if __name__ == "__main__":
    unittest.main()

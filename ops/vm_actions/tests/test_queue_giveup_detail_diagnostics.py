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
        "queue_detail_collect_diagnostics", VMOPS / "collect_diagnostics.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class QueueGiveupDetailDiagnosticsTests(unittest.TestCase):
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

    def detail(self, value):
        return {
            "ts": self.now,
            "event": "queue_giveup_detail",
            "label": "QUEUE_GIVEUP",
            "agent": "",
            "rc": "92",
            "error": value,
        }

    def test_strict_detail_grammar_exposes_only_fixed_metrics(self):
        self.write_events(
            [
                self.detail("wait=0;holder=radio_main"),
                self.detail("wait=86400;holder=news"),
                self.detail("wait=-1;holder=radio_main"),
                self.detail("wait=86401;holder=radio_main"),
                self.detail("wait=0001;holder=radio_main"),
                self.detail("wait=1;holder=private_holder"),
                self.detail("wait=1;holder=radio_main;token=SECRET_QUEUE_SENTINEL"),
                self.detail("prefix-wait=1;holder=radio_main"),
                {
                    "ts": self.now,
                    "event": "queue_giveup",
                    "label": "RADIO:private-route:main",
                    "agent": "vercel:m1",
                    "rc": "92",
                },
            ]
        )

        ai = self.collector._collect_ai(self.soren, self.now)
        self.assertEqual(ai["queue_giveup_detail_sampled"], 2)
        self.assertEqual(ai["queue_giveup_detail_malformed"], 6)
        self.assertEqual(ai["queue_giveup_detail_wait_max_sec"], 86400)
        self.assertEqual(ai["queue_giveup_detail_holders"]["radio_main"], 1)
        self.assertEqual(ai["queue_giveup_detail_holders"]["news"], 1)
        self.assertEqual(sum(ai["queue_giveup_detail_holders"].values()), 2)
        self.assertEqual([event["event"] for event in ai["recent_events"]], ["queue_giveup"])

        rendered = json.dumps(ai, sort_keys=True)
        self.assertNotIn("SECRET_QUEUE_SENTINEL", rendered)
        self.assertNotIn("private_holder", rendered)
        self.assertNotIn("prefix-wait", rendered)

    def test_holder_summary_is_separate_from_caller_attribution(self):
        data = {
            "status": "warn",
            "workers": {},
            "queues": {"queue_giveups_15m": 2},
            "ai": {
                "queue_giveup_detail_sampled": 2,
                "queue_giveup_detail_malformed": 0,
                "queue_giveup_detail_wait_max_sec": 17,
                "queue_giveup_detail_holders": {
                    "radio_prepass": 0,
                    "radio_main": 1,
                    "news": 1,
                    "jiji": 0,
                    "celebration": 0,
                    "other": 0,
                    "unknown": 0,
                    "SECRET_EXTRA_HOLDER": 999,
                },
                "recent_events": [
                    {"event": "queue_giveup", "component": "RADIO:private:main"},
                    {"event": "queue_giveup", "component": "NEWS:spam_check:private"},
                ],
            },
            "improvement": {},
            "corners": {},
            "storage_artifacts": {},
            "bundle_storage": {},
        }

        severity, summary = attribution.render(data)
        self.assertEqual(severity, "warn")
        self.assertIn("ai_queue_giveup_caller_attribution_exact=1", summary)
        self.assertIn("ai_queue_giveup_component_radio_main=1", summary)
        self.assertIn("ai_queue_giveup_component_news_spam_check=1", summary)
        self.assertIn("ai_queue_giveup_holder_coverage_exact=1", summary)
        self.assertIn("ai_queue_giveup_holder_wait_max_sec=17", summary)
        self.assertIn("ai_queue_giveup_holder_radio_main=1", summary)
        self.assertIn("ai_queue_giveup_holder_news=1", summary)
        self.assertNotIn("SECRET_EXTRA_HOLDER", summary)
        self.assertNotIn("private", summary)

    def test_inconsistent_holder_map_fails_closed(self):
        data = {
            "queues": {"queue_giveups_15m": 1},
            "ai": {
                "queue_giveup_detail_sampled": 1,
                "queue_giveup_detail_malformed": 0,
                "queue_giveup_detail_wait_max_sec": 999999999,
                "queue_giveup_detail_holders": {"radio_main": 2},
            },
        }
        counts, sampled, malformed, wait_max, consistent, exact = (
            attribution.attribute_queue_giveup_holders(data)
        )
        self.assertEqual(sampled, 1)
        self.assertEqual(malformed, 0)
        self.assertFalse(consistent)
        self.assertFalse(exact)
        self.assertEqual(wait_max, 0)
        self.assertTrue(all(value == 0 for value in counts.values()))


if __name__ == "__main__":
    unittest.main()

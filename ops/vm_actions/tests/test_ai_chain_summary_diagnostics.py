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
        "chain_summary_collect_diagnostics", VMOPS / "collect_diagnostics.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ChainSummaryDiagnosticsTests(unittest.TestCase):
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

    def chain(self, value, **extra):
        event = {
            "ts": self.now,
            "event": "chain_summary",
            "label": "RADIO:SUPERSECRET_LABEL:prepass",
            "agent": "vercel:SUPERSECRET_MODEL",
            "rc": "79",
            "error": value,
        }
        event.update(extra)
        return event

    def test_fixed_chain_counters_ignore_malformed_and_hide_dynamic_fields(self):
        self.write_events(
            [
                self.chain("vrl=2;vda=2;nfs=1;term=winner"),
                self.chain("vrl=1;vda=1;nfs=0;term=winner"),
                self.chain("vrl=2;vda=2;nfs=0;term=all_failed"),
                self.chain("vrl=-1;vda=1;nfs=0;term=winner"),
                self.chain("vrl=999999999;vda=2;nfs=0;term=winner"),
                self.chain("vrl=2;vda=2;nfs=0;term=unknown"),
                self.chain("vrl=1;vda=2;nfs=0;term=winner"),
                self.chain("vrl=2;vda=2;nfs=1;term=winner;token=SUPERSECRET_TOKEN"),
                {
                    "ts": self.now,
                    "event": "fail",
                    "label": "RADIO:public:main",
                    "agent": "vercel:m1",
                    "rc": "79",
                    "error": "429",
                },
            ]
        )
        ai = self.collector._collect_ai(self.soren, self.now)
        self.assertEqual(ai["chain_summary_sampled"], 3)
        self.assertEqual(ai["multi_vercel_429_chains"], 2)
        self.assertEqual(ai["multi_vercel_429_non_vercel_recovered"], 1)
        self.assertEqual(ai["multi_vercel_429_all_failed"], 1)
        self.assertEqual([event["event"] for event in ai["recent_events"]], ["fail"])
        rendered = json.dumps(ai)
        self.assertNotIn("SUPERSECRET_LABEL", rendered)
        self.assertNotIn("SUPERSECRET_MODEL", rendered)
        self.assertNotIn("SUPERSECRET_TOKEN", rendered)

    def test_chain_summaries_do_not_consume_recent_event_budget(self):
        events = []
        for index in range(self.collector.MAX_RECENT_EVENTS + 5):
            events.append(self.chain("vrl=2;vda=2;nfs=0;term=queue_giveup"))
            events.append(
                {
                    "ts": self.now,
                    "event": "fail",
                    "label": "RADIO:public:main",
                    "agent": f"vercel:m{index}",
                    "rc": "1",
                    "error": "failure",
                }
            )
        self.write_events(events)
        ai = self.collector._collect_ai(self.soren, self.now)
        self.assertEqual(ai["chain_summary_sampled"], self.collector.MAX_RECENT_EVENTS + 5)
        self.assertEqual(len(ai["recent_events"]), self.collector.MAX_RECENT_EVENTS)
        self.assertTrue(all(event["event"] == "fail" for event in ai["recent_events"]))

    def test_public_runtime_summary_includes_fixed_chain_counters(self):
        severity, summary = attribution.render(
            {
                "status": "warn",
                "workers": {},
                "queues": {"queue_giveups_15m": 0},
                "ai": {
                    "chain_summary_sampled": 5,
                    "multi_vercel_429_chains": 2,
                    "multi_vercel_429_non_vercel_recovered": 1,
                    "multi_vercel_429_all_failed": 1,
                    "recent_events": [],
                },
                "improvement": {},
                "corners": {},
                "storage_artifacts": {},
                "bundle_storage": {},
            }
        )
        self.assertEqual(severity, "warn")
        self.assertIn("ai_chain_summary_sampled=5", summary)
        self.assertIn("ai_multi_vercel_429_chains=2", summary)
        self.assertIn("ai_multi_vercel_429_non_vercel_recovered=1", summary)
        self.assertIn("ai_multi_vercel_429_all_failed=1", summary)
        self.assertNotIn("label=", summary)
        self.assertNotIn("model=", summary)


if __name__ == "__main__":
    unittest.main()

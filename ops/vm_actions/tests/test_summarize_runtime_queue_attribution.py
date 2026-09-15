import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
VMOPS = ROOT / "ops" / "vm_actions"
sys.path.insert(0, str(VMOPS))

import summarize_runtime_queue_attribution as attribution  # noqa: E402


class QueueGiveupAttributionTests(unittest.TestCase):
    def test_missing_recent_sample_fails_closed_to_unknown(self):
        counts, consistent, exact = attribution.attribute_queue_giveups(
            {"queues": {"queue_giveups_15m": 2}, "ai": {}}
        )
        self.assertFalse(consistent)
        self.assertFalse(exact)
        self.assertEqual(counts["unknown"], 2)
        self.assertEqual(sum(counts.values()), 2)

    def test_bounded_sample_remainder_becomes_unknown(self):
        counts, consistent, exact = attribution.attribute_queue_giveups(
            {
                "queues": {"queue_giveups_15m": 3},
                "ai": {
                    "recent_events": [
                        {"event": "queue_giveup", "component": "RADIO:secret-route:main"},
                        {"event": "queue_giveup", "component": "NEWS:spam_check:secret-route"},
                        {"event": "fail", "component": "RADIO:secret-route:main"},
                    ]
                },
            }
        )
        self.assertTrue(consistent)
        self.assertFalse(exact)
        self.assertEqual(counts["radio_main"], 1)
        self.assertEqual(counts["news_spam_check"], 1)
        self.assertEqual(counts["unknown"], 1)
        self.assertEqual(sum(counts.values()), 3)

    def test_exact_sample_uses_only_fixed_component_buckets(self):
        counts, consistent, exact = attribution.attribute_queue_giveups(
            {
                "queues": {"queue_giveups_15m": 2},
                "ai": {
                    "recent_events": [
                        {"event": "queue_giveup", "component": "RADIO:private:prepass"},
                        {"event": "queue_giveup", "component": "COMMENT:private"},
                    ]
                },
            }
        )
        self.assertTrue(consistent)
        self.assertTrue(exact)
        self.assertEqual(counts["radio_prepass"], 1)
        self.assertEqual(counts["comment"], 1)
        self.assertEqual(counts["unknown"], 0)
        self.assertEqual(sum(counts.values()), 2)

    def test_sample_larger_than_aggregate_fails_closed(self):
        counts, consistent, exact = attribution.attribute_queue_giveups(
            {
                "queues": {"queue_giveups_15m": 1},
                "ai": {
                    "recent_events": [
                        {"event": "queue_giveup", "component": "RADIO:one"},
                        {"event": "queue_giveup", "component": "COMMENT:two"},
                    ]
                },
            }
        )
        self.assertFalse(consistent)
        self.assertFalse(exact)
        self.assertEqual(counts["unknown"], 1)
        self.assertEqual(sum(counts.values()), 1)
        self.assertTrue(all(counts[name] == 0 for name in attribution.COMPONENTS))

    def test_render_preserves_base_severity_and_hides_dynamic_labels(self):
        severity, summary = attribution.render(
            {
                "status": "warn",
                "workers": {},
                "queues": {"queue_giveups_15m": 2},
                "ai": {
                    "recent_events": [
                        {"event": "queue_giveup", "component": "RADIO:SUPERSECRET:main"}
                    ]
                },
                "improvement": {},
                "corners": {},
                "storage_artifacts": {},
                "bundle_storage": {},
            }
        )
        self.assertEqual(severity, "warn")
        self.assertIn("ai_queue_giveup_component_radio_main=1", summary)
        self.assertIn("ai_queue_giveup_component_unknown=1", summary)
        self.assertIn("ai_queue_giveup_attribution_exact=0", summary)
        self.assertIn("ai_rate_limit_pressure=0", summary)
        self.assertNotIn("SUPERSECRET", summary)

    def test_render_composes_pressure_with_queue_attribution(self):
        severity, summary = attribution.render(
            {
                "status": "warn",
                "workers": {},
                "queues": {"queue_giveups_15m": 2},
                "ai": {
                    "attempts_15m": 100,
                    "rate_limits_15m": 50,
                    "recent_events": [
                        {"event": "queue_giveup", "component": "RADIO:secret-route:main"}
                    ],
                },
                "improvement": {},
                "corners": {},
                "storage_artifacts": {},
                "bundle_storage": {},
            }
        )
        # Both observability signals must survive the single severity/summary
        # step-output pair the VM monitor reads.
        self.assertIn("ai_rate_limit_pressure=1", summary)
        self.assertIn("ai_queue_giveup_component_radio_main=1", summary)
        self.assertIn("ai_queue_giveup_component_unknown=1", summary)


if __name__ == "__main__":
    unittest.main()

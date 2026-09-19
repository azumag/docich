import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
VMOPS = ROOT / "ops" / "vm_actions"
sys.path.insert(0, str(VMOPS))

import summarize_runtime_queue_attribution as attribution  # noqa: E402


class InvalidOutputAttributionTests(unittest.TestCase):
    def test_invalid_output_is_attributed_to_fixed_component_buckets(self):
        data = {
            "status": "warn",
            "workers": {},
            "queues": {},
            "ai": {
                "recent_events": [
                    {
                        "event": "fail",
                        "component": "COMMENT:reply-private",
                        "provider": "private-provider-a",
                        "model": "private-model-a",
                        "rc": "1",
                        "error_preview": "validator rejected empty output SECRET_A",
                    },
                    {
                        "event": "fail",
                        "component": "RADIO:main-private",
                        "provider": "private-provider-b",
                        "model": "private-model-b",
                        "rc": "1",
                        "error_preview": "invalid output SECRET_B",
                    },
                    {
                        "event": "fail",
                        "component": "private-dynamic-component",
                        "provider": "private-provider-c",
                        "model": "private-model-c",
                        "rc": "1",
                        "error_preview": "output empty SECRET_C",
                    },
                    {
                        "event": "fail",
                        "component": "COMMENT:not-invalid",
                        "provider": "private-provider-d",
                        "model": "private-model-d",
                        "rc": "1",
                        "error_preview": "opaque failure SECRET_D",
                    },
                ]
            },
            "improvement": {},
        }

        counts = attribution.invalid_output_component_metrics(data)
        self.assertEqual(counts["comment"], 1)
        self.assertEqual(counts["radio_main"], 1)
        self.assertEqual(counts["other"], 1)
        self.assertEqual(counts["radio_prepass"], 0)
        self.assertEqual(counts["news_spam_check"], 0)
        self.assertEqual(counts["improvement"], 0)

        _, summary = attribution.render(data)
        self.assertIn("ai_recent_invalid_output_component_comment=1", summary)
        self.assertIn("ai_recent_invalid_output_component_radio_main=1", summary)
        self.assertIn("ai_recent_invalid_output_component_other=1", summary)
        self.assertIn("ai_recent_invalid_output_component_radio_prepass=0", summary)
        for secret in (
            "reply-private",
            "main-private",
            "private-dynamic-component",
            "private-provider",
            "private-model",
            "SECRET_A",
            "SECRET_B",
            "SECRET_C",
            "SECRET_D",
        ):
            self.assertNotIn(secret, summary)

    def test_malformed_recent_events_fail_closed_to_zero_counts(self):
        data = {
            "status": "ok",
            "workers": {},
            "queues": {},
            "ai": {"recent_events": "not-a-list"},
            "improvement": {},
        }
        counts = attribution.invalid_output_component_metrics(data)
        self.assertEqual(counts, {component: 0 for component in attribution.COMPONENTS})
        _, summary = attribution.render(data)
        for component in attribution.COMPONENTS:
            self.assertIn(f"ai_recent_invalid_output_component_{component}=0", summary)


if __name__ == "__main__":
    unittest.main()

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "ops" / "vm_actions" / "summarize_runtime.py"


def load_module():
    spec = importlib.util.spec_from_file_location("summarize_runtime_queue_giveup", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RuntimeQueueGiveupSummaryTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()

    def test_queue_giveups_use_fixed_component_buckets_only(self):
        data = {
            "status": "warn",
            "workers": {},
            "queues": {"queue_giveups_15m": 3},
            "ai": {
                "recent_events": [
                    {
                        "event": "queue_giveup",
                        "component": "RADIO:secret-prepass:prepass",
                        "provider": "private-provider-a",
                        "model": "private-model-a",
                    },
                    {
                        "event": "queue_giveup",
                        "component": "RADIO:secret-main",
                        "provider": "private-provider-b",
                        "model": "private-model-b",
                    },
                    {
                        "event": "queue_giveup",
                        "component": "private-dynamic-component",
                        "provider": "private-provider-c",
                        "model": "private-model-c",
                    },
                ]
            },
            "improvement": {},
        }

        _, summary = self.mod.summarize(data)

        self.assertIn("queue_giveups_15m=3", summary)
        self.assertIn("ai_recent_queue_giveup_sampled=3", summary)
        self.assertIn("ai_recent_queue_giveup_component_radio_prepass=1", summary)
        self.assertIn("ai_recent_queue_giveup_component_radio_main=1", summary)
        self.assertIn("ai_recent_queue_giveup_component_other=1", summary)
        self.assertIn("ai_recent_queue_giveup_component_comment=0", summary)
        self.assertNotIn("secret-prepass", summary)
        self.assertNotIn("secret-main", summary)
        self.assertNotIn("private-dynamic-component", summary)
        self.assertNotIn("private-provider", summary)
        self.assertNotIn("private-model", summary)


if __name__ == "__main__":
    unittest.main()

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "ops" / "vm_actions" / "summarize_runtime.py"


def load_module():
    spec = importlib.util.spec_from_file_location("summarize_runtime_all_failed", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AllFailedAttributionTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()

    def test_full_window_attribution_uses_bounded_components(self):
        data = {
            "status": "warn",
            "workers": {},
            "queues": {},
            "ai": {
                "all_failed_15m": 3,
                "recent_events": [],
                "anomalous_components": {
                    "RADIO:news:prepass": {"failures": 0, "all_failed": 1, "agents": []},
                    "COMMENT": {"failures": 0, "all_failed": 2, "agents": []},
                },
            },
            "improvement": {},
        }
        _, summary = self.mod.summarize(data)
        self.assertIn("ai_recent_all_failed_sampled=0", summary)
        self.assertIn("ai_all_failed_attribution_sampled=3", summary)
        self.assertIn("ai_all_failed_attribution_unknown=0", summary)
        self.assertIn("ai_all_failed_component_radio_prepass=1", summary)
        self.assertIn("ai_all_failed_component_comment=2", summary)

    def test_truncated_component_map_keeps_unknown_remainder(self):
        data = {
            "status": "warn",
            "workers": {},
            "queues": {},
            "ai": {
                "all_failed_15m": 4,
                "recent_events": [],
                "anomalous_components": {
                    "COMMENT": {"failures": 0, "all_failed": 1, "agents": []},
                },
            },
            "improvement": {},
        }
        _, summary = self.mod.summarize(data)
        self.assertIn("ai_all_failed_attribution_sampled=1", summary)
        self.assertIn("ai_all_failed_attribution_unknown=3", summary)
        self.assertIn("ai_all_failed_attribution_consistent=1", summary)


if __name__ == "__main__":
    unittest.main()

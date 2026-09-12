import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "ops" / "vm_actions" / "summarize_runtime.py"


def load_module():
    spec = importlib.util.spec_from_file_location("summarize_runtime_rate_limit_distinct_agents", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RuntimeRateLimitDistinctAgentSummaryTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()

    def test_distinct_agent_count_deduplicates_private_model_identity(self):
        data = {
            "status": "warn",
            "workers": {},
            "queues": {},
            "ai": {
                "recent_events": [
                    {
                        "event": "fail",
                        "component": "RADIO:secret:prepass",
                        "provider": "vercel",
                        "model": "private-model-a",
                        "rc": "79",
                        "error_preview": "rate limit",
                    },
                    {
                        "event": "fail",
                        "component": "RADIO:secret:main",
                        "provider": "vercel",
                        "model": "private-model-a",
                        "rc": "79",
                        "error_preview": "rate limit",
                    },
                    {
                        "event": "fail",
                        "component": "RADIO:secret:prepass",
                        "provider": "vercel",
                        "model": "private-model-b",
                        "rc": "79",
                        "error_preview": "rate limit",
                    },
                ]
            },
            "improvement": {},
        }

        _, summary = self.mod.summarize(data)

        self.assertIn("ai_recent_rate_limit_backend_vercel=3", summary)
        self.assertIn("ai_recent_rate_limit_backend_vercel_distinct_agents=2", summary)
        self.assertNotIn("private-model-a", summary)
        self.assertNotIn("private-model-b", summary)
        self.assertNotIn("RADIO:secret", summary)

    def test_empty_window_emits_zero_distinct_agents_for_all_fixed_backends(self):
        data = {
            "status": "ok",
            "workers": {},
            "queues": {},
            "ai": {},
            "improvement": {},
        }

        _, summary = self.mod.summarize(data)

        for family in self.mod.BACKEND_FAMILIES:
            self.assertIn(f"ai_recent_rate_limit_backend_{family}_distinct_agents=0", summary)


if __name__ == "__main__":
    unittest.main()

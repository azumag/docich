import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "ops" / "vm_actions" / "summarize_runtime.py"


def load_module():
    spec = importlib.util.spec_from_file_location("summarize_runtime_rate_limit_backends", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RuntimeRateLimitBackendSummaryTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()

    def test_rate_limits_are_collapsed_to_fixed_backend_families_without_model_leak(self):
        providers = (
            ("local", "local"),
            ("codex", "codex"),
            ("opencode-go", "opencode"),
            ("vercel", "vercel"),
            ("amd", "amd"),
            ("secret-provider", "other"),
        )
        events = []
        for index, (provider, _family) in enumerate(providers):
            events.append(
                {
                    "event": "fail",
                    "component": f"RADIO:secret-show-{index}:main",
                    "provider": provider,
                    "model": f"private-model-{index}",
                    "rc": "79",
                    "error_preview": "rate limit private diagnostic",
                }
            )
        data = {
            "status": "warn",
            "workers": {},
            "queues": {},
            "ai": {"recent_events": events},
            "improvement": {},
        }

        _, summary = self.mod.summarize(data)

        self.assertIn("ai_recent_fail_rate_limit=6", summary)
        for _provider, family in providers:
            self.assertIn(f"ai_recent_rate_limit_backend_{family}=1", summary)
        self.assertNotIn("secret-provider", summary)
        self.assertNotIn("secret-show", summary)
        for index in range(len(providers)):
            self.assertNotIn(f"private-model-{index}", summary)

    def test_missing_recent_events_emit_zero_for_every_fixed_backend(self):
        data = {
            "status": "ok",
            "workers": {},
            "queues": {},
            "ai": {},
            "improvement": {},
        }

        _, summary = self.mod.summarize(data)

        for family in self.mod.BACKEND_FAMILIES:
            self.assertIn(f"ai_recent_rate_limit_backend_{family}=0", summary)


if __name__ == "__main__":
    unittest.main()

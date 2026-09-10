import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "ops" / "vm_actions" / "summarize_runtime.py"


def load_module():
    spec = importlib.util.spec_from_file_location("summarize_runtime", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RuntimeSummaryTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()

    def test_summary_adds_bounded_ai_counts_and_fixed_causes(self):
        data = {
            "status": "warn",
            "workers": {
                "required_down": [],
                "required_stale": [],
                "paused": ["hidden-worker"],
                "duplicates": [],
                "zombies": [],
                "stale_pid_files": ["hidden-a", "hidden-b"],
                "unregistered": ["hidden-c"],
            },
            "queues": {"stale_locks": 0, "queue_giveups_15m": 1},
            "ai": {
                "attempts_15m": 17,
                "successes": 2,
                "failures_15m": 15,
                "rate_limits_15m": 1,
                "fallbacks_15m": 0,
                "all_failed_15m": 8,
                "recent_events": [
                    {"event": "fail", "component": "RADIO:news:prepass", "rc": "79", "error_preview": "429 slow down"},
                    {"event": "fail", "component": "RADIO:main", "rc": "1", "error_preview": "request timed out"},
                    {"event": "fail", "component": "COMMENT", "rc": "1", "error_preview": "403 RestrictedModelsError"},
                    {"event": "fail", "component": "IMPROVE:candidate", "rc": "1", "error_preview": "503 service unavailable"},
                    {"event": "fail", "component": "NEWS:brief", "rc": "1", "error_preview": "model not found"},
                    {"event": "fail", "component": "private-dynamic-component", "rc": "1", "error_preview": "validator rejected empty output"},
                    {"event": "fail", "component": "private-dynamic-component", "rc": "1", "error_preview": "opaque provider failure SECRET_VALUE"},
                    {"event": "all_failed", "component": "RADIO:news:prepass", "rc": ""},
                    {"event": "all_failed", "component": "COMMENT", "rc": ""},
                    {"event": "winner", "component": "RADIO:main", "rc": "0", "error_preview": "must not count"},
                ],
            },
            "improvement": {"stale": False, "retry_pending": False},
        }
        severity, summary = self.mod.summarize(data)
        self.assertEqual(severity, "warn")
        self.assertIn("ai_attempts_15m=17", summary)
        self.assertIn("ai_successes_15m=2", summary)
        self.assertIn("ai_recent_fail_sampled=7", summary)
        for cause in self.mod.CAUSES:
            self.assertIn(f"ai_recent_fail_{cause}=1", summary)
        self.assertIn("ai_recent_fail_component_radio_prepass=1", summary)
        self.assertIn("ai_recent_fail_component_radio_main=2", summary)
        self.assertIn("ai_recent_fail_component_comment=1", summary)
        self.assertIn("ai_recent_fail_component_improvement=1", summary)
        self.assertIn("ai_recent_fail_component_other=2", summary)
        self.assertIn("ai_recent_all_failed_sampled=2", summary)
        self.assertIn("ai_recent_all_failed_component_radio_prepass=1", summary)
        self.assertIn("ai_recent_all_failed_component_comment=1", summary)
        self.assertNotIn("private-dynamic-component", summary)
        self.assertNotIn("SECRET_VALUE", summary)
        self.assertNotIn("hidden-worker", summary)

    def test_provider_and_model_identifiers_never_enter_summary(self):
        data = {
            "status": "warn",
            "workers": {},
            "queues": {},
            "ai": {
                "recent_events": [
                    {
                        "event": "fail",
                        "component": "RADIO:secret-program-name",
                        "provider": "private-provider",
                        "model": "private-model",
                        "rc": "1",
                        "error_preview": "500 internal server error",
                    },
                    {
                        "event": "all_failed",
                        "component": "RADIO:secret-program-name",
                        "provider": "private-provider",
                        "model": "private-model",
                    },
                ]
            },
            "improvement": {},
        }
        _, summary = self.mod.summarize(data)
        self.assertIn("ai_recent_fail_provider_server=1", summary)
        self.assertIn("ai_recent_fail_component_radio_main=1", summary)
        self.assertIn("ai_recent_all_failed_component_radio_main=1", summary)
        self.assertNotIn("secret-program-name", summary)
        self.assertNotIn("private-provider", summary)
        self.assertNotIn("private-model", summary)
        self.assertNotIn("internal server error", summary)

    def test_malformed_numeric_values_fail_closed_to_zero(self):
        data = {
            "status": "ok",
            "workers": {"required_down": "bad"},
            "queues": {"stale_locks": True},
            "ai": {"attempts_15m": -1, "successes": "3", "recent_events": "bad"},
            "improvement": {"stale": "true", "retry_pending": 1},
        }
        severity, summary = self.mod.summarize(data)
        self.assertEqual(severity, "ok")
        self.assertIn("required_down=0", summary)
        self.assertIn("stale_locks=0", summary)
        self.assertIn("ai_attempts_15m=0", summary)
        self.assertIn("ai_successes_15m=0", summary)
        self.assertIn("ai_recent_all_failed_sampled=0", summary)
        self.assertIn("improvement_stale=0", summary)
        self.assertIn("retry_pending=0", summary)

    def test_invalid_severity_is_rejected(self):
        with self.assertRaises(ValueError):
            self.mod.summarize({"status": "unknown"})


if __name__ == "__main__":
    unittest.main()

import importlib.util
import math
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "ops" / "vm_actions" / "shared_overlay_memory_alert.py"


def load_module():
    spec = importlib.util.spec_from_file_location("shared_overlay_memory_alert", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SharedOverlayMemoryAlertTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()

    def wrapper(self, renderer_rss_mb):
        return {
            "status": "diagnosed",
            "diagnostics": {
                "browser_processes": {
                    "status": "ok",
                    "scan_complete": 1,
                    "by_owner": {
                        "soren-shared-overlay": {
                            "renderer_rss_mb": renderer_rss_mb,
                        },
                    },
                },
            },
        }

    def test_thresholds_are_inclusive_and_fixed(self):
        self.assertEqual(self.mod.assess(self.wrapper(4095.9))["severity"], "ok")
        self.assertEqual(self.mod.assess(self.wrapper(4096))["severity"], "warn")
        self.assertEqual(self.mod.assess(self.wrapper(8191.9))["severity"], "warn")
        self.assertEqual(self.mod.assess(self.wrapper(8192))["severity"], "critical")
        self.assertEqual(self.mod.WARN_RENDERER_RSS_MB, 4096)
        self.assertEqual(self.mod.CRITICAL_RENDERER_RSS_MB, 8192)

    def test_missing_or_incomplete_scan_is_unknown_not_recovery(self):
        cases = [
            None,
            {},
            {"status": "diagnosed", "diagnostics": {}},
            {"status": "diagnosed", "diagnostics": {"browser_processes": {
                "status": "unknown", "scan_complete": 0, "by_owner": {},
            }}},
            {"status": "diagnosed", "diagnostics": {"browser_processes": {
                "status": "ok", "scan_complete": 1, "by_owner": {},
            }}},
        ]
        for case in cases:
            with self.subTest(case=case):
                result = self.mod.assess(case)
                self.assertEqual(result["available"], 0)
                self.assertEqual(result["severity"], "unknown")
                self.assertIsNone(result["renderer_rss_mb"])

    def test_invalid_numbers_are_unknown(self):
        for value in (True, -1, float("nan"), float("inf"), "8192", None):
            with self.subTest(value=value):
                result = self.mod.assess(self.wrapper(value))
                self.assertEqual(result["available"], 0)
                self.assertEqual(result["severity"], "unknown")

    def test_only_fixed_metric_is_returned(self):
        wrapper = self.wrapper(5000)
        wrapper["diagnostics"]["browser_processes"]["by_owner"]["soren-shared-overlay"].update({
            "pid": 12345,
            "cmdline": "/private/path/chrome --secret",
        })
        result = self.mod.assess(wrapper)
        self.assertEqual(set(result), {"available", "severity", "renderer_rss_mb"})
        self.assertEqual(result["severity"], "warn")
        self.assertNotIn("pid", str(result))
        self.assertNotIn("private", str(result))


if __name__ == "__main__":
    unittest.main()

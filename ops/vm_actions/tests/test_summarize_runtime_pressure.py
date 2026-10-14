import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "ops" / "vm_actions" / "summarize_runtime_pressure.py"


def load_module():
    spec = importlib.util.spec_from_file_location("summarize_runtime_pressure", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RuntimeRateLimitPressureTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()

    def test_material_pressure_is_signaled(self):
        self.assertEqual(
            1,
            self.mod.rate_limit_pressure({"attempts_15m": 16, "rate_limits_15m": 6}),
        )

    def test_few_transient_rate_limits_do_not_signal(self):
        self.assertEqual(
            0,
            self.mod.rate_limit_pressure({"attempts_15m": 20, "rate_limits_15m": 3}),
        )

    def test_minimum_count_prevents_small_window_noise(self):
        self.assertEqual(
            0,
            self.mod.rate_limit_pressure({"attempts_15m": 10, "rate_limits_15m": 4}),
        )

    def test_ratio_threshold_prevents_busy_low_ratio_window(self):
        self.assertEqual(
            0,
            self.mod.rate_limit_pressure({"attempts_15m": 20, "rate_limits_15m": 5}),
        )

    def test_zero_attempts_are_safe(self):
        self.assertEqual(0, self.mod.rate_limit_pressure({}))

    def test_summary_appends_fixed_signal_without_changing_severity(self):
        data = {
            "status": "ok",
            "workers": {},
            "queues": {},
            "ai": {"attempts_15m": 13, "rate_limits_15m": 7},
            "improvement": {},
        }
        severity, summary = self.mod.summarize(data)
        self.assertEqual("ok", severity)
        self.assertIn("ai_rate_limit_pressure=1", summary)


if __name__ == "__main__":
    unittest.main()

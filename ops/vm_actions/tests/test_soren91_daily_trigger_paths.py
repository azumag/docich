from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "soren91-daily-improve.yml"


class Soren91DailyTriggerPathsTests(unittest.TestCase):
    def test_all_reviewed_daily_helpers_trigger_production_validation(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        required_paths = (
            ".github/workflows/soren91-daily-improve.yml",
            "ops/vm_actions/run_soren91_daily_improve.sh",
            "ops/vm_actions/classify_soren91_daily_gateway.py",
            "ops/vm_actions/classify_soren91_opencode_nonzero.py",
            "ops/vm_actions/soren91_opencode_capture_shim.sh",
            "ops/vm_actions/soren91_opencode_fixed_exec.sh",
            "games/soviet_now",
        )
        for path in required_paths:
            with self.subTest(path=path):
                self.assertIn(f"      - '{path}'", text)


if __name__ == "__main__":
    unittest.main()

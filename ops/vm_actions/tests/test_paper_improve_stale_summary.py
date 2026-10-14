import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
VMOPS = ROOT / "ops" / "vm_actions"
sys.path.insert(0, str(VMOPS))
SCRIPT = VMOPS / "summarize_runtime_queue_attribution.py"


def load_module():
    spec = importlib.util.spec_from_file_location("paper_improve_stale_summary", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def payload(status="running", age_sec=0):
    return {
        "status": "ok",
        "workers": {},
        "queues": {"queue_giveups_15m": 0},
        "ai": {"recent_events": []},
        "improvement": {},
        "corners": {
            "paper_improve": {
                "status": status,
                "age_sec": age_sec,
                "detail": "SECRET_DETAIL",
            }
        },
        "storage_artifacts": {},
        "bundle_storage": {},
    }


class PaperImproveStaleSummaryTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()

    def test_running_over_conservative_bound_is_stale_without_changing_severity(self):
        severity, summary = self.mod.render(payload(age_sec=1801))
        self.assertEqual(severity, "ok")
        self.assertIn("corner_paper_improve_stale=1", summary)
        self.assertNotIn("SECRET_DETAIL", summary)

    def test_fresh_active_and_old_terminal_metadata_are_not_stale(self):
        for status, age, expected in (
            ("queued", 1800, 0),
            ("running", 120, 0),
            ("completed", 7200, 0),
        ):
            with self.subTest(status=status, age=age):
                _, summary = self.mod.render(payload(status=status, age_sec=age))
                self.assertIn(f"corner_paper_improve_stale={expected}", summary)

    def test_malformed_age_fails_closed(self):
        for age in (True, "7200", -1, None):
            with self.subTest(age=age):
                _, summary = self.mod.render(payload(age_sec=age))
                self.assertIn("corner_paper_improve_stale=0", summary)


if __name__ == "__main__":
    unittest.main()

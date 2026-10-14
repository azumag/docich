import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "ops" / "vm_actions" / "summarize_runtime.py"


def load_module():
    spec = importlib.util.spec_from_file_location("summarize_runtime_stale_lock", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StaleLockSummaryTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_module()

    def test_stale_locks_are_attributed_to_fixed_public_buckets(self):
        data = {
            "status": "warn",
            "workers": {},
            "queues": {
                "stale_locks": 4,
                "lanes": {
                    "radio": {"stale_suspected": True, "owner_pid": 101},
                    "comment": {"stale_suspected": True, "owner_pid": 102},
                    "local": {"stale_suspected": True, "owner_pid": 103},
                    "SECRET_DYNAMIC_LANE": {
                        "stale_suspected": True,
                        "owner_pid": 104,
                        "owner_label": "SECRET_OWNER",
                    },
                    "not-stale": {"stale_suspected": False, "owner_pid": 105},
                },
            },
            "ai": {},
            "improvement": {},
        }
        severity, summary = self.mod.summarize(data)
        self.assertEqual(severity, "warn")
        self.assertIn("stale_locks=4", summary)
        self.assertIn("stale_lock_radio=1", summary)
        self.assertIn("stale_lock_comment=1", summary)
        self.assertIn("stale_lock_local=1", summary)
        self.assertIn("stale_lock_other=1", summary)
        self.assertNotIn("SECRET_DYNAMIC_LANE", summary)
        self.assertNotIn("SECRET_OWNER", summary)
        self.assertNotIn("owner_pid", summary)

    def test_malformed_lane_state_fails_closed_without_dynamic_name_leak(self):
        data = {
            "status": "warn",
            "workers": {},
            "queues": {
                "stale_locks": 2,
                "lanes": {
                    "radio": {"stale_suspected": "true"},
                    "SECRET_DYNAMIC_LANE": "bad",
                    17: {"stale_suspected": True},
                },
            },
            "ai": {},
            "improvement": {},
        }
        _, summary = self.mod.summarize(data)
        for lane in self.mod.STALE_LOCK_LANES:
            self.assertIn(f"stale_lock_{lane}=0", summary)
        self.assertNotIn("SECRET_DYNAMIC_LANE", summary)

    def test_missing_lanes_fail_closed_to_zero(self):
        data = {
            "status": "ok",
            "workers": {},
            "queues": {"stale_locks": 1},
            "ai": {},
            "improvement": {},
        }
        _, summary = self.mod.summarize(data)
        for lane in self.mod.STALE_LOCK_LANES:
            self.assertIn(f"stale_lock_{lane}=0", summary)


if __name__ == "__main__":
    unittest.main()

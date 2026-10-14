import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"


def load_collector():
    spec = importlib.util.spec_from_file_location("collect_diagnostics_blockers", str(COLLECTOR))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ImprovementBlockerDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.collector = load_collector()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vmops-improve-blockers-")
        self.soren = Path(self.tmp.name) / "soren"
        self.state = self.soren / "tmp" / "state"
        self.state.mkdir(parents=True)
        self.now = int(time.time())

    def tearDown(self):
        self.tmp.cleanup()

    def collect(self):
        return self.collector._collect_improvement(self.soren, self.now)

    def test_idle_without_pending_work_has_no_blocker(self):
        result = self.collect()
        self.assertEqual(result["blocked_by"], [])

    def test_file_backed_scheduler_gates_are_fixed_enums(self):
        (self.soren / "tmp" / "improve.lock").write_text("{}\n")
        (self.state / "improve_retry_batch.json").write_text("{}\n")
        (self.state / "rate_limit_backoff").write_text("1\n")
        (self.state / "peak_hour_defer").write_text("1\n")
        # Presence is authoritative for _improve_ab_pending, even if the state
        # is malformed or aborted and still awaiting bookkeeping.
        (self.state / "ab_state.json").write_text("not-json\n")
        (self.state / "improve_daemon.paused").write_text("1\n")

        result = self.collect()

        self.assertEqual(
            result["blocked_by"],
            ["rate_limit_backoff", "peak_hour_defer", "ab_pending", "daemon_paused"],
        )
        self.assertNotIn("unknown", result["blocked_by"])

    def test_pending_retry_without_known_gate_is_unknown(self):
        (self.soren / "tmp" / "improve.lock").write_text("{}\n")
        (self.state / "improve_retry_batch.json").write_text("{}\n")

        result = self.collect()

        self.assertEqual(result["blocked_by"], ["unknown"])
        self.assertEqual(result["retry_pending"], True)
        self.assertEqual(result["lock_present"], True)

    def test_running_improvement_is_not_reported_as_blocked(self):
        (self.state / "improve_state.json").write_text(
            json.dumps(
                {
                    "status": "running",
                    "pid": os.getpid(),
                    "phase": "analyze",
                    "started_at": self.now - 5,
                    "updated_at": self.now,
                }
            )
        )
        (self.state / "rate_limit_backoff").write_text("1\n")
        (self.state / "peak_hour_defer").write_text("1\n")
        (self.state / "ab_state.json").write_text("{}\n")
        (self.state / "improve_daemon.paused").write_text("1\n")

        result = self.collect()

        self.assertEqual(result["running"], True)
        self.assertEqual(result["blocked_by"], [])


if __name__ == "__main__":
    unittest.main()

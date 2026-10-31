import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"


def load_collector():
    spec = importlib.util.spec_from_file_location("collect_diagnostics_lifecycle_severity", str(COLLECTOR))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LifecycleHandoverRequiredWorkerSeverityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.collector = load_collector()

    @staticmethod
    def workers(*, down=None, stale=None, paused=None):
        return {
            "required_down": down or [],
            "required_stale": stale or [],
            "paused": paused or [],
            "pause_ownership": {"lifecycle_owned": 0, "operator_owned": 0, "unknown": 0},
            "unregistered": [],
            "unregistered_health": {"alive": 0, "paused": 0, "stale_only": 0, "unknown": 0},
            "duplicates": [],
            "zombies": [],
        }

    def severity(self, workers, lifecycle_pause=None):
        return self.collector._severity(
            workers,
            {"stale_locks": 0},
            {"all_failed": 0, "queue_giveups": 0},
            {"stale": False, "retry_pending": False},
            lifecycle_pause=lifecycle_pause,
        )

    @staticmethod
    def active_handover():
        return {"active": True, "waiting_for": "lifecycle_handover"}

    def test_required_down_with_active_handover_warns(self):
        self.assertEqual(self.severity(self.workers(down=["prediction_worker"]), self.active_handover()), "warn")

    def test_required_down_without_lifecycle_evidence_stays_critical(self):
        workers = self.workers(down=["prediction_worker"])
        for lifecycle_pause in (None, "invalid", {}, {"active": True}):
            with self.subTest(lifecycle_pause=lifecycle_pause):
                self.assertEqual(self.severity(workers, lifecycle_pause), "critical")

    def test_required_down_with_wrong_wait_reason_stays_critical(self):
        self.assertEqual(
            self.severity(self.workers(down=["prediction_worker"]), {"active": True, "waiting_for": "boundary"}),
            "critical",
        )

    def test_required_down_with_inactive_handover_stays_critical(self):
        self.assertEqual(
            self.severity(
                self.workers(down=["prediction_worker"]),
                {"active": False, "waiting_for": "lifecycle_handover"},
            ),
            "critical",
        )

    def test_required_stale_is_critical_even_during_active_handover(self):
        self.assertEqual(
            self.severity(self.workers(stale=["prediction_worker"]), self.active_handover()),
            "critical",
        )

    def test_normal_required_stop_outside_handover_stays_critical(self):
        self.assertEqual(self.severity(self.workers(down=["prediction_worker"])), "critical")

    def test_paused_worker_with_active_handover_stays_warn(self):
        workers = self.workers(paused=["prediction_worker"])
        workers["pause_ownership"]["unknown"] = 1
        self.assertEqual(self.severity(workers, self.active_handover()), "warn")


if __name__ == "__main__":
    unittest.main()

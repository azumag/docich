import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"
SUMMARIZER = ROOT / "ops" / "vm_actions" / "summarize_worker_state.py"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ActionableWorkerSeverityTests(unittest.TestCase):
    def setUp(self):
        self.collector = load(COLLECTOR, "collect_diagnostics_actionable")
        self.summarizer = load(SUMMARIZER, "summarize_worker_state_actionable")
        self.tmp = tempfile.TemporaryDirectory(prefix="vmops-actionable-")
        self.soren = Path(self.tmp.name) / "soren"
        self.state = self.soren / "tmp" / "state"
        self.state.mkdir(parents=True)
        self.old_workers = self.collector.WORKERS
        self.old_required_workers = self.collector.required_workers
        self.collector.WORKERS = (("prediction_worker", False, "prediction", None, "pid"),)
        self.collector.required_workers = lambda: ()

    def tearDown(self):
        self.collector.WORKERS = self.old_workers
        self.collector.required_workers = self.old_required_workers
        self.tmp.cleanup()

    @staticmethod
    def quiet_runtime():
        return {"stale_locks": 0}, {"all_failed": 0, "queue_giveups": 0}, {"stale": False, "retry_pending": False}

    def severity(self, workers):
        queues, ai, improvement = self.quiet_runtime()
        return self.collector._severity(workers, queues, ai, improvement)

    def write_lifecycle_pause(self, request_id, *, status="boundary", deadline=100, record=True):
        (self.state / "prediction_worker.paused").write_text(f"lifecycle:{request_id}\n")
        lifecycle = self.state / "game_lifecycle"
        lifecycle.mkdir(exist_ok=True)
        identity = {
            "schema": 1,
            "request_id": request_id,
            "game": "fixture",
            "generation": 1,
            "deadline_epoch": deadline,
            "deadline_at": "fixture-deadline",
        }
        (lifecycle / "request.json").write_text(json.dumps(identity))
        (lifecycle / "ack.json").write_text(json.dumps({**identity, "status": status}))
        if record:
            (lifecycle / "prediction_pause.json").write_text(
                json.dumps({"request_id": request_id, "improvement_marker_created": True})
            )

    def test_matching_active_lifecycle_pause_is_non_actionable(self):
        request_id = "11111111-1111-4111-8111-111111111111"
        self.write_lifecycle_pause(request_id, status="boundary", deadline=100)
        workers = self.collector._collect_workers(self.soren, 1)
        self.assertEqual(workers["pause_ownership"], {"lifecycle_owned": 1, "operator_owned": 0, "unknown": 0})
        self.assertEqual(self.severity(workers), "ok")

    def test_stopped_lifecycle_pause_is_owned_while_waiting_fresh_start(self):
        request_id = "22222222-2222-4222-8222-222222222222"
        self.write_lifecycle_pause(request_id, status="stopped", deadline=1)
        workers = self.collector._collect_workers(self.soren, 1000)
        self.assertEqual(workers["pause_ownership"]["lifecycle_owned"], 1)
        self.assertEqual(self.severity(workers), "ok")

    def test_expired_lifecycle_pause_becomes_unknown_and_warns(self):
        request_id = "33333333-3333-4333-8333-333333333333"
        self.write_lifecycle_pause(request_id, status="stopping", deadline=10)
        workers = self.collector._collect_workers(self.soren, 11)
        self.assertEqual(workers["pause_ownership"]["unknown"], 1)
        self.assertEqual(self.severity(workers), "warn")

    def test_terminal_nonparked_lifecycle_pause_becomes_unknown_and_warns(self):
        request_id = "44444444-4444-4444-8444-444444444444"
        self.write_lifecycle_pause(request_id, status="cancelled", deadline=100)
        workers = self.collector._collect_workers(self.soren, 1)
        self.assertEqual(workers["pause_ownership"]["unknown"], 1)
        self.assertEqual(self.severity(workers), "warn")

    def test_mismatched_lifecycle_ack_identity_becomes_unknown_and_warns(self):
        request_id = "55555555-5555-4555-8555-555555555555"
        self.write_lifecycle_pause(request_id, status="boundary", deadline=100)
        lifecycle = self.state / "game_lifecycle"
        ack = json.loads((lifecycle / "ack.json").read_text())
        ack["generation"] = 2
        (lifecycle / "ack.json").write_text(json.dumps(ack))
        workers = self.collector._collect_workers(self.soren, 1)
        self.assertEqual(workers["pause_ownership"]["unknown"], 1)
        self.assertEqual(self.severity(workers), "warn")

    def test_webui_operator_pause_is_non_actionable(self):
        (self.state / "prediction_worker.paused").write_text(
            json.dumps({"paused": True, "ts": 1, "source": "webui"}) + "\n"
        )
        workers = self.collector._collect_workers(self.soren, 1)
        self.assertEqual(workers["pause_ownership"]["operator_owned"], 1)
        self.assertEqual(self.severity(workers), "ok")

    def test_unproved_pause_remains_warn_fail_closed(self):
        (self.state / "prediction_worker.paused").write_text("{}\n")
        workers = self.collector._collect_workers(self.soren, 1)
        self.assertEqual(workers["pause_ownership"]["unknown"], 1)
        self.assertEqual(self.severity(workers), "warn")

    def test_lifecycle_marker_without_matching_record_remains_warn(self):
        request_id = "66666666-6666-4666-8666-666666666666"
        self.write_lifecycle_pause(request_id, status="boundary", deadline=100, record=False)
        workers = self.collector._collect_workers(self.soren, 1)
        self.assertEqual(workers["pause_ownership"]["unknown"], 1)
        self.assertEqual(self.severity(workers), "warn")

    def test_stale_only_unregistered_pid_is_non_actionable(self):
        (self.state / "orphan_overlay.pid").write_text("99999999\n")
        workers = self.collector._collect_workers(self.soren, 1)
        self.assertEqual(workers["unregistered_health"], {"alive": 0, "paused": 0, "stale_only": 1, "unknown": 0})
        self.assertEqual(self.severity(workers), "ok")

    def test_alive_or_unknown_unregistered_remains_warn(self):
        workers = {
            "required_down": [],
            "required_stale": [],
            "paused": [],
            "pause_ownership": {"lifecycle_owned": 0, "operator_owned": 0, "unknown": 0},
            "unregistered": ["hidden"],
            "unregistered_health": {"alive": 1, "paused": 0, "stale_only": 0, "unknown": 0},
            "duplicates": [],
            "zombies": [],
        }
        self.assertEqual(self.severity(workers), "warn")
        workers["unregistered_health"] = {"alive": 0, "paused": 0, "stale_only": 0, "unknown": 0}
        self.assertEqual(self.severity(workers), "warn")

    def test_summary_exposes_only_fixed_ownership_counts(self):
        data = {
            "workers": {
                "details": {"prediction_worker": {"paused": True, "stale_pid_file": False}},
                "unregistered": ["private-worker-name"],
                "pause_ownership": {"lifecycle_owned": 1, "operator_owned": 0, "unknown": 0},
                "unregistered_health": {"alive": 0, "paused": 0, "stale_only": 1, "unknown": 0},
            }
        }
        out = self.summarizer.summarize_worker_state(data)
        self.assertIn("pause_owner_lifecycle=1", out)
        self.assertIn("pause_owner_unknown=0", out)
        self.assertIn("unregistered_stale_only=1", out)
        self.assertNotIn("private-worker-name", out)


if __name__ == "__main__":
    unittest.main()
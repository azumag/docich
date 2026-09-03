"""P4 tests: JSONL game-switch event log (design v2 §9)."""

import json
import os
import stat
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import game_switch  # noqa: E402
from docich.adapters import AdapterError  # noqa: E402
from docich.naming import runtime_names  # noqa: E402


class FakeAdapter:
    name = "cli"
    agent_enabled = True

    def __init__(self, spec, behavior, runtime):
        self.spec = spec
        self.behavior = behavior
        self.runtime = runtime

    def _fail(self, key):
        value = self.behavior.get(key)
        if value is not None:
            raise value

    def preflight(self, deadline, cancel):
        self._fail("preflight_error")

    def materialize_runtime(self, deadline, cancel):
        self._fail("materialize_error")
        self.runtime["materialized"] = True
        self.runtime["alive"] = True

    def readiness(self, deadline, cancel):
        self._fail("readiness_error")

    def alive(self, deadline, cancel):
        return bool(self.runtime.get("materialized") and self.runtime.get("alive"))

    def cleanup_runtime(self, deadline, cancel):
        self.runtime["alive"] = False
        self.runtime["cleaned"] = True

    def start_agent(self, deadline, cancel):
        self._fail("agent_start_error")
        self.runtime["agent_started"] = True

    def stop_agent(self, deadline, cancel):
        self.runtime["agent_stopped"] = True


class FakeFactory:
    def __init__(self, behaviors):
        self.behaviors = behaviors
        self.adapters = {}

    def __call__(self, spec):
        if spec.game not in self.behaviors:
            raise AdapterError(f"unknown game: {spec.game}")
        key = (spec.game, spec.generation)
        if key not in self.adapters:
            self.adapters[key] = FakeAdapter(spec, self.behaviors[spec.game], {})
            self.adapters[key].spec = spec
        else:
            self.adapters[key].spec = spec
        return self.adapters[key]


class EventLogTestBase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tempdir.name) / "run"
        self.store = game_switch.GameSwitchStore(self.state_dir)
        self.factory = FakeFactory({"nethack": {}, "robots": {}})
        self.coordinator = game_switch.GameSwitchCoordinator(
            self.store,
            self.factory,
            quiesce_verify_timeout_s=0.3,
            poll_interval_s=0.01,
            default_timeout_s=60,
            step_timeouts=game_switch.StepTimeouts(
                preflight_s=2.0,
                stop_agent_s=2.0,
                start_s=2.0,
                agent_start_s=2.0,
                cleanup_s=2.0,
                probe_s=0.5,
            ),
        )
        self.log_path = self.state_dir / "logs" / "game_switch.log"

    def tearDown(self):
        self.tempdir.cleanup()

    def events(self):
        return self.coordinator.event_log.read_all()

    def event_names(self):
        return [e["event"] for e in self.events()]


class TestEventSequence(EventLogTestBase):
    def test_start_emits_full_lifecycle(self):
        result = self.coordinator.start("nethack")
        self.assertEqual(result.status, "succeeded")
        names = self.event_names()
        for expected in (
            "requested", "accepted", "validated", "prepared",
            "quiesce_started", "quiesced", "candidate_started",
            "probe_started", "ready", "agent_staged", "committed",
            "mirror_updated",
        ):
            self.assertIn(expected, names)
        self.assertEqual(names[0], "requested")
        committed = [e for e in self.events() if e["event"] == "committed"][0]
        self.assertEqual(committed["result"], "succeeded")
        self.assertEqual(committed["to_game"], "nethack")
        self.assertEqual(committed["generation"], 1)

    def test_events_carry_request_identity_and_schema(self):
        request_id = str(uuid.uuid4())
        self.coordinator.switch("robots", request_id=request_id)
        for event in self.events():
            self.assertEqual(event["schema_version"], 1)
            for key in (
                "timestamp", "duration_ms", "event", "request_id", "operation",
                "phase", "result", "error_code", "cleanup_pending",
            ):
                self.assertIn(key, event)
            self.assertEqual(event["request_id"], request_id)
            self.assertEqual(event["operation"], "switch")
            self.assertGreaterEqual(event["duration_ms"], 0)

    def test_log_file_is_private_jsonl(self):
        self.coordinator.start("nethack")
        self.assertEqual(stat.S_IMODE(self.log_path.stat().st_mode), 0o600)
        raw = self.log_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertGreater(len(raw), 5)
        for line in raw:
            json.loads(line)

    def test_no_secrets_or_argv_in_log(self):
        self.coordinator.start("nethack")
        blob = self.log_path.read_text(encoding="utf-8")
        for banned in ("argv", "token", "stream_key", "DOCICH_", "password"):
            self.assertNotIn(banned, blob)

    def test_secret_bearing_detail_is_redacted(self):
        self.factory.behaviors["robots"]["preflight_error"] = AdapterError(
            "probe failed for https://hooks.example.invalid/x?token=SECRET-TOKEN-123 "
            "with stream_key=AKIA-SECRET-KEY-XYZ argv=['--key','hunter2hunter2hunter2hunter2AB']"
        )
        self.coordinator.start("nethack")
        result = self.coordinator.switch("robots")
        self.assertEqual(result.status, "rolled_back")
        blob = self.log_path.read_text(encoding="utf-8")
        for leaked in (
            "SECRET-TOKEN-123",
            "AKIA-SECRET-KEY-XYZ",
            "token=SECRET",
            "hunter2hunter2hunter2hunter2AB",
        ):
            self.assertNotIn(leaked, blob)
        # Redacted forms and safe context survive.
        self.assertIn("token=<redacted>", blob)
        self.assertIn("stream_key=<redacted>", blob)
        self.assertIn("hooks.example.invalid", blob)
        self.assertIn("prepare_failed", blob)
        # The request itself still fails closed with the raw detail intact.
        receipt = self.store.receipts.load(result.request_id)
        self.assertEqual(receipt["status"], "rolled_back")

    def test_sanitizer_keeps_safe_identifiers(self):
        from docich.game_switch import _sanitize_log_detail

        text = _sanitize_log_detail(
            "game-g1 agent stopped (generation=2) request 12345678-1234-5678-1234-567812345678"
        )
        assert text is not None
        for kept in ("game-g1", "generation=2", "12345678-1234-5678-1234-567812345678"):
            self.assertIn(kept, text)
        self.assertIsNone(_sanitize_log_detail(None))
        self.assertIsNone(_sanitize_log_detail(""))

    def test_failed_switch_logs_rollback(self):
        self.factory.behaviors["robots"]["readiness_error"] = game_switch.ReadinessTimeoutError("slow")
        self.coordinator.start("nethack")
        result = self.coordinator.switch("robots")
        self.assertEqual(result.status, "rolled_back")
        names = self.event_names()
        self.assertIn("rollback_started", names)
        self.assertIn("rollback_ready", names)
        self.assertNotIn("rollback_failed", names)

    def test_start_failure_logs_failed_with_code(self):
        self.factory.behaviors["robots"]["materialize_error"] = AdapterError("boom")
        result = self.coordinator.start("robots")
        self.assertEqual(result.status, "failed")
        failed = [e for e in self.events() if e["event"] == "failed"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["error_code"], "start_failed")
        self.assertEqual(failed[0]["result"], "failed")

    def test_recover_logs_recovery_events(self):
        self.factory.behaviors["robots"]["materialize_error"] = AdapterError("boom")
        self.coordinator.start("robots")
        result = self.coordinator.recover()
        self.assertEqual(result.status, "succeeded")
        names = self.event_names()
        self.assertIn("recovery_started", names)
        self.assertIn("recovery_finished", names)

    def test_log_write_failure_does_not_break_request(self):
        self.coordinator.start("nethack")
        # Make the log unwritable: further emits must be best-effort.
        os.chmod(self.log_path.parent, 0o500)
        try:
            result = self.coordinator.stop()
        finally:
            os.chmod(self.log_path.parent, 0o700)
        self.assertEqual(result.status, "succeeded")


if __name__ == "__main__":
    unittest.main()
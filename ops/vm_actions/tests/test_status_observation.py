import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
GATEWAY = ROOT / "ops/vm_actions/gateway.py"
SPEC = importlib.util.spec_from_file_location("vm_gateway_status_observation", GATEWAY)
gw = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gw)


class InodeStatusTests(unittest.TestCase):
    def test_unknown_inode_capacity_is_none_not_zero(self):
        fake = SimpleNamespace(f_blocks=100, f_bfree=30, f_bavail=20, f_frsize=4096, f_files=0, f_favail=0)
        with mock.patch.object(gw.os, "statvfs", return_value=fake):
            data = gw._filesystem_status(Path("/private/production"))
        self.assertIsNone(data["files_total"])
        self.assertIsNone(data["files_available"])


class ProjectionGuardEventTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="vmops-guard-event-"))
        self.soren = self.base / "soren"
        (self.soren / "tmp" / "state").mkdir(parents=True)
        self.path = self.soren / "tmp" / "state" / "projection_guard_last_event.json"

    def write(self, payload):
        self.path.write_text(json.dumps(payload) + "\n")

    def test_absent_file_reports_not_present(self):
        self.assertEqual(gw._projection_guard_event(str(self.soren)),
                         {"present": False, "readable": False, "event": None, "policy": None, "recorded_at": None})

    def test_missing_soren_root_reports_not_present(self):
        self.assertFalse(gw._projection_guard_event(None)["present"])

    def test_whitelists_fields_and_withholds_paths_and_reason(self):
        self.write({"schema_version": 1, "event": "would_refuse", "policy": "dry_run",
                    "reason": "/home/ubuntu/secret/path", "quarantine_dir": "/var/q", "recorded_at": 1792750000})
        data = gw._projection_guard_event(str(self.soren))
        self.assertEqual(data, {"present": True, "readable": True, "event": "would_refuse",
                                "policy": "dry_run", "recorded_at": 1792750000})

    def test_unknown_event_and_policy_values_are_dropped(self):
        self.write({"schema_version": 1, "event": "/etc/passwd", "policy": "free text", "recorded_at": "yesterday"})
        data = gw._projection_guard_event(str(self.soren))
        self.assertTrue(data["readable"])
        self.assertIsNone(data["event"])
        self.assertIsNone(data["policy"])
        self.assertIsNone(data["recorded_at"])

    def test_wrong_schema_version_is_unreadable(self):
        self.write({"schema_version": 2, "event": "refused", "policy": "enforce_reviewed", "recorded_at": 1})
        data = gw._projection_guard_event(str(self.soren))
        self.assertTrue(data["present"])
        self.assertFalse(data["readable"])
        self.assertIsNone(data["event"])

    def test_invalid_json_is_unreadable(self):
        self.path.write_text("{not json")
        data = gw._projection_guard_event(str(self.soren))
        self.assertFalse(data["readable"])

    def test_oversized_file_is_refused(self):
        self.path.write_text(" " * (gw.PROJECTION_GUARD_EVENT_MAX_BYTES + 1) + "{}")
        data = gw._projection_guard_event(str(self.soren))
        self.assertTrue(data["present"])
        self.assertFalse(data["readable"])

    def test_symlink_is_refused_without_following(self):
        target = self.base / "elsewhere.json"
        target.write_text(json.dumps({"schema_version": 1, "event": "refused", "policy": "off", "recorded_at": 1}))
        os.symlink(target, self.path)
        data = gw._projection_guard_event(str(self.soren))
        self.assertTrue(data["present"])
        self.assertFalse(data["readable"])
        self.assertIsNone(data["event"])


if __name__ == "__main__":
    unittest.main()

"""Finite queue snapshots: no-follow, bounded and read-only."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]


class RetroProgramQueueDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        spec = importlib.util.spec_from_file_location(
            "queue_diagnostics", ROOT / "ops/vm_actions/collect_diagnostics.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.queue = self.root / "tmp/state/docich_program_queue"
        self.queue.mkdir(parents=True)

    def collect(self):
        return self.module._collect_retro_program_queues(self.root)

    def test_only_two_fixed_kinds_and_three_fields_are_published(self):
        for kind, filename in self.module.RETRO_PROGRAM_QUEUE_FILES.items():
            path = self.queue / filename
            path.write_text(json.dumps({"status": "waiting_turn", "owner_state": "/PRIVATE/PATH",
                "request_id": "PRIVATE_ID", "text": "PRIVATE_BODY", "secret": "PRIVATE_SECRET"}))
        (self.queue / "PRIVATE_UNLISTED.json").write_text('{"status":"running"}')
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.queue.iterdir()}
        with mock.patch.object(self.module, "_read_hanjuku_scene_record",
                               wraps=self.module._read_hanjuku_scene_record) as read:
            result = self.collect()
        self.assertEqual(set(result), {"scheduled", "manual"})
        self.assertEqual(read.call_count, 2)
        for row in result.values():
            self.assertEqual(row, {"present": True, "readable": True, "status": "waiting_turn"})
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.queue.iterdir()})

    def test_known_statuses_and_missing_or_unknown_status_are_distinct_from_bad_read(self):
        path = self.queue / "retro_corner.json"
        for status in self.module.RETRO_PROGRAM_QUEUE_STATUSES:
            with self.subTest(status=status):
                path.write_text(json.dumps({"status": status}))
                self.assertEqual(self.collect()["scheduled"],
                    {"present": True, "readable": True, "status": status})
        for status in (None, True, 42, [], {}, "PRIVATE_STATUS", "DONE"):
            with self.subTest(status=status):
                path.write_text(json.dumps({"status": status}))
                self.assertEqual(self.collect()["scheduled"],
                    {"present": True, "readable": True, "status": "unknown"})
        path.write_text('{}')
        self.assertEqual(self.collect()["scheduled"],
            {"present": True, "readable": True, "status": "unknown"})

    def test_missing_files_are_absent_without_creating_state_or_locks(self):
        self.assertEqual(self.collect(), {kind: {"present": False, "readable": False,
            "status": "unknown"} for kind in ("scheduled", "manual")})
        self.assertEqual(list(self.queue.iterdir()), [])
        missing = self.root / "absent"
        self.module._collect_retro_program_queues(missing)
        self.assertFalse(missing.exists())

    def test_bad_json_non_object_empty_oversize_and_fifo_cannot_be_readable(self):
        path = self.queue / "retro_corner.json"
        for raw in ("PRIVATE_BODY", "[]", "null", "", "{" * 2000,
                    " " * (self.module.HANJUKU_SCENE_RECORD_MAX_BYTES + 1)):
            with self.subTest(length=len(raw)):
                path.write_text(raw)
                self.assertEqual(self.collect()["scheduled"],
                    {"present": True, "readable": False, "status": "unknown"})
        path.unlink()
        os.mkfifo(path)
        self.assertEqual(self.collect()["scheduled"],
            {"present": True, "readable": False, "status": "unknown"})

    def test_leaf_and_ancestor_symlinks_never_publish_target_status(self):
        target = self.root / "private.json"
        target.write_text('{"status":"done","secret":"PRIVATE_SECRET"}')
        path = self.queue / "retro_corner.json"
        path.symlink_to(target)
        self.assertEqual(self.collect()["scheduled"],
            {"present": None, "readable": False, "status": "unknown"})
        path.unlink()
        path.symlink_to(self.root / "missing-target")
        self.assertIsNone(self.collect()["scheduled"]["present"])
        path.unlink()
        alternate = self.root / "alternate"
        alternate.mkdir()
        (alternate / "retro_corner.json").write_text('{"status":"done"}')
        self.queue.rmdir()
        self.queue.symlink_to(alternate, target_is_directory=True)
        self.assertEqual(self.collect(), {kind: {"present": None, "readable": False,
            "status": "unknown"} for kind in ("scheduled", "manual")})

    def test_permission_error_is_fixed_unknown_and_does_not_expose_exception(self):
        with mock.patch.object(self.module, "_read_hanjuku_scene_record",
                side_effect=PermissionError("PRIVATE_SECRET /PRIVATE/PATH")):
            self.assertEqual(self.collect(), {kind: {"present": None, "readable": False,
                "status": "unknown"} for kind in ("scheduled", "manual")})

    def test_full_program_projection_uses_soren_queue_root_and_adds_no_authority(self):
        (self.queue / "retro_corner_manual.json").write_text('{"status":"error"}')
        with mock.patch.object(self.module, "_rotation_timer_selection", return_value=("timer", False)), \
             mock.patch.object(self.module, "_unit_is_active", return_value=False), \
             mock.patch.object(self.module, "_unit_is_enabled", return_value=False), \
             mock.patch.object(self.module, "_collect_hanjuku_predictions", return_value={}), \
             mock.patch.object(self.module, "_collect_boundary", return_value={}), \
             mock.patch.object(self.module, "_collect_ab", return_value={}), \
             mock.patch.object(self.module, "_collect_soren_game", return_value={}):
            output = self.module._collect_programs(self.root / "absent-docich", self.root, 100)
        self.assertEqual(output["retro_program_queues"]["manual"],
            {"present": True, "readable": True, "status": "error"})
        self.assertNotIn("authority", json.dumps(output["retro_program_queues"]))


if __name__ == "__main__":
    unittest.main()

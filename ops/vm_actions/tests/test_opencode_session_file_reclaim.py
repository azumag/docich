import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

VM_ACTIONS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VM_ACTIONS))

import opencode_session_file_reclaim as reclaim  # noqa: E402

OLD = time.time_ns() - 48 * 3600 * 1_000_000_000


class OpencodeSessionFileReclaimTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.data = Path(self.tempdir.name) / "opencode"
        self.gate = Path(self.tempdir.name) / "gate.lock"
        self.data.mkdir()
        # snapshot tree: one old, one fresh
        old_snap = self.data / "snapshot" / "aa" / "obj" / "ab"
        old_snap.mkdir(parents=True)
        f = old_snap / "deadbeef"
        f.write_bytes(b"x" * 100)
        os.utime(f, ns=(OLD, OLD))
        os.utime(old_snap, ns=(OLD, OLD))
        os.utime(old_snap.parent, ns=(OLD, OLD))
        os.utime(self.data / "snapshot" / "aa", ns=(OLD, OLD))
        fresh_snap = self.data / "snapshot" / "bb" / "obj"
        fresh_snap.mkdir(parents=True)
        (fresh_snap / "beef").write_bytes(b"y" * 50)
        # storage/session_diff: one old json, one fresh, plus migration marker
        sd = self.data / "storage" / "session_diff"
        sd.mkdir(parents=True)
        old_json = sd / "ses_old.json"
        old_json.write_text("{}")
        os.utime(old_json, ns=(OLD, OLD))
        (sd / "ses_new.json").write_text("{}")
        (self.data / "storage" / "migration").write_text("1")
        # exclusions
        (self.data / "tool-output").mkdir()
        (self.data / "tool-output" / "t1").write_bytes(b"t" * 10)
        os.utime(self.data / "tool-output" / "t1", ns=(OLD, OLD))
        (self.data / "log").mkdir()
        (self.data / "log" / "opencode.log").write_bytes(b"l" * 10)
        os.utime(self.data / "log" / "opencode.log", ns=(OLD, OLD))
        (self.data / "auth.json").write_text("{}")
        os.utime(self.data / "auth.json", ns=(OLD, OLD))
        (self.data / "opencode.db").write_bytes(b"d" * 10)

    def tearDown(self):
        self.tempdir.cleanup()

    def invoke(self, **kw):
        args = dict(apply=False, batch=50, expect_plan="",
                    cutoff_hours=24.0, gate_wait_sec=5)
        args.update(kw)
        return reclaim.run(str(self.data), str(self.gate), **args)

    def test_dry_run_plans_only_expired_session_entries(self):
        result = self.invoke()
        paths = [p["path"] for p in result["plan"]]
        self.assertEqual(sorted(paths), ["snapshot/aa",
                                         "storage/session_diff/ses_old.json"])
        self.assertEqual(result["mode"], "dry-run")
        self.assertGreaterEqual(result["bytes_planned"], 102)

    def test_fresh_and_excluded_paths_never_planned(self):
        result = self.invoke()
        paths = [p["path"] for p in result["plan"]]
        for excluded in ("snapshot/bb", "storage/session_diff/ses_new.json",
                         "storage/migration", "tool-output/t1",
                         "log/opencode.log", "auth.json", "opencode.db"):
            self.assertNotIn(excluded, paths)

    def test_apply_with_wrong_digest_removes_nothing(self):
        with self.assertRaises(reclaim.ReclaimError) as ctx:
            self.invoke(apply=True, expect_plan="0" * 64)
        self.assertEqual(str(ctx.exception), "plan_mismatch")
        self.assertTrue((self.data / "snapshot" / "aa").exists())

    def test_apply_with_reviewed_digest_removes_only_planned(self):
        plan = self.invoke()
        result = self.invoke(apply=True, expect_plan=plan["plan_sha256"])
        self.assertFalse((self.data / "snapshot" / "aa").exists())
        self.assertFalse((self.data / "storage" / "session_diff" / "ses_old.json").exists())
        # Fresh + excluded survive.
        for kept in ("snapshot/bb", "storage/session_diff/ses_new.json",
                     "storage/migration", "tool-output/t1",
                     "log/opencode.log", "auth.json", "opencode.db"):
            self.assertTrue((self.data / kept).exists(), kept)
        self.assertGreaterEqual(result["bytes_removed"], 102)

    def test_symlink_inside_snapshot_rejects_run(self):
        (self.data / "snapshot" / "aa" / "obj" / "ab" / "deadbeef").unlink()
        (self.data / "snapshot" / "aa" / "obj" / "ab" / "deadbeef").symlink_to(
            self.data / "auth.json")
        with self.assertRaises(reclaim.ReclaimError) as ctx:
            self.invoke()
        self.assertEqual(str(ctx.exception), "unsafe_entry")

    def test_batch_bounds_are_enforced(self):
        for bad in (0, -1, reclaim.MAX_BATCH + 1):
            with self.assertRaises(reclaim.ReclaimError):
                self.invoke(batch=bad)

    def test_gate_contention_rejects(self):
        import fcntl
        fd = os.open(self.gate, os.O_CREAT | os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(reclaim.ReclaimError) as ctx:
                self.invoke(gate_wait_sec=0.4)
            self.assertEqual(str(ctx.exception), "gate_timeout")
        finally:
            os.close(fd)

    def test_result_has_fixed_shape(self):
        result = self.invoke()
        for key in ("schema", "mode", "batch", "entries_planned",
                    "entries_selected", "plan_sha256", "plan",
                    "bytes_planned", "bytes_removed"):
            self.assertIn(key, result)


if __name__ == "__main__":
    unittest.main()

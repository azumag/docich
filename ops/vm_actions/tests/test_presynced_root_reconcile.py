from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ops.vm_actions.reconcile_presynced_root as reconcile_root
from ops.vm_actions.reconcile_presynced_root import (
    REASON_STAGED_DRIFT,
    REASON_UNKNOWN_DRIFT,
    REASON_UNSUPPORTED_PATH,
    ReconcileError,
    reconcile,
)


class PresyncedRootReconcileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vmops-presync-root-")
        self.root = Path(self.tmp.name) / "docich"
        self.root.mkdir(parents=True)
        subprocess.run(["git", "init", "--quiet", str(self.root)], check=True)
        self._git("config", "user.email", "tests@example.invalid")
        self._git("config", "user.name", "vmops tests")

        (self.root / "app.py").write_text("old\n", encoding="utf-8")
        (self.root / "delete.txt").write_text("keep in old\n", encoding="utf-8")
        (self.root / "script.sh").write_text("#!/bin/sh\necho old\n", encoding="utf-8")
        os.chmod(self.root / "script.sh", 0o644)
        self._git("add", "app.py", "delete.txt", "script.sh")
        self._git("commit", "-m", "old baseline")
        self.old = self._git("rev-parse", "HEAD")

        (self.root / "app.py").write_text("middle\n", encoding="utf-8")
        self._git("commit", "-am", "reviewed middle")
        self.middle = self._git("rev-parse", "HEAD")

        (self.root / "app.py").write_text("new\n", encoding="utf-8")
        (self.root / "delete.txt").unlink()
        os.chmod(self.root / "script.sh", 0o755)
        self._git("add", "-A")
        self._git("commit", "-m", "reviewed candidate")
        self.new = self._git("rev-parse", "HEAD")

        self._git("checkout", "--detach", "--quiet", self.old)

    def tearDown(self):
        self.tmp.cleanup()

    def _git(self, *args: str) -> str:
        return subprocess.check_output(["git", "-C", str(self.root), *args], text=True).strip()

    def _status(self) -> str:
        return self._git("status", "--porcelain", "--untracked-files=no", "--ignore-submodules=all")

    def assert_reason(self, code: int, fn) -> ReconcileError:
        with self.assertRaises(ReconcileError) as ctx:
            fn()
        self.assertEqual(ctx.exception.code, code)
        return ctx.exception

    def test_clean_recorded_parent_is_successful_noop(self):
        reconcile(self.root, self.old, self.new)
        self.assertEqual(self._git("rev-parse", "HEAD"), self.old)
        self.assertEqual(self._status(), "")

    def test_exact_reviewed_candidate_bytes_are_restored_to_old(self):
        (self.root / "app.py").write_text("new\n", encoding="utf-8")
        reconcile(self.root, self.old, self.new)
        self.assertEqual((self.root / "app.py").read_text(encoding="utf-8"), "old\n")
        self.assertEqual(self._status(), "")

    def test_exact_reviewed_intermediate_bytes_are_restored_to_old(self):
        (self.root / "app.py").write_text("middle\n", encoding="utf-8")
        reconcile(self.root, self.old, self.new)
        self.assertEqual((self.root / "app.py").read_text(encoding="utf-8"), "old\n")
        self.assertEqual(self._status(), "")

    def test_reviewed_candidate_deletion_is_restored_to_old(self):
        (self.root / "delete.txt").unlink()
        reconcile(self.root, self.old, self.new)
        self.assertEqual((self.root / "delete.txt").read_text(encoding="utf-8"), "keep in old\n")
        self.assertEqual(self._status(), "")

    def test_reviewed_mode_change_is_restored_to_old_mode(self):
        path = self.root / "script.sh"
        os.chmod(path, 0o755)
        reconcile(self.root, self.old, self.new)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
        self.assertEqual(self._status(), "")

    def test_unknown_bytes_are_refused_without_overwrite_or_path_leak(self):
        path = self.root / "app.py"
        path.write_text("operator drift\n", encoding="utf-8")
        error = self.assert_reason(REASON_UNKNOWN_DRIFT, lambda: reconcile(self.root, self.old, self.new))
        self.assertEqual(path.read_text(encoding="utf-8"), "operator drift\n")
        self.assertNotIn("app.py", str(error))

    def test_same_length_unknown_bytes_do_not_pass_as_reviewed(self):
        path = self.root / "app.py"
        path.write_text("bad\n", encoding="utf-8")  # same length as "new\n"
        self.assert_reason(REASON_UNKNOWN_DRIFT, lambda: reconcile(self.root, self.old, self.new))
        self.assertEqual(path.read_text(encoding="utf-8"), "bad\n")

    def test_oversize_live_file_is_refused_before_content_read(self):
        path = self.root / "app.py"
        path.write_bytes(b"12345")
        with (
            mock.patch.object(reconcile_root, "MAX_FILE_BYTES", 4),
            mock.patch("os.fdopen", side_effect=AssertionError("oversize content must not be read")),
        ):
            self.assert_reason(REASON_UNSUPPORTED_PATH, lambda: reconcile_root._live(path))

    def test_staged_change_is_refused_and_index_is_untouched(self):
        path = self.root / "app.py"
        path.write_text("new\n", encoding="utf-8")
        self._git("add", "app.py")
        before = self._git("diff", "--cached", "--binary")
        self.assert_reason(REASON_STAGED_DRIFT, lambda: reconcile(self.root, self.old, self.new))
        self.assertEqual(self._git("diff", "--cached", "--binary"), before)
        self.assertEqual(path.read_text(encoding="utf-8"), "new\n")

    def test_symlink_replacement_is_refused_without_following_it(self):
        path = self.root / "app.py"
        path.unlink()
        target = self.root / "outside.txt"
        target.write_text("outside\n", encoding="utf-8")
        path.symlink_to(target)
        self.assert_reason(REASON_UNSUPPORTED_PATH, lambda: reconcile(self.root, self.old, self.new))
        self.assertTrue(path.is_symlink())
        self.assertEqual(target.read_text(encoding="utf-8"), "outside\n")

    def test_helper_ignores_untracked_files(self):
        (self.root / "untracked.txt").write_text("runtime state\n", encoding="utf-8")
        reconcile(self.root, self.old, self.new)
        self.assertTrue((self.root / "untracked.txt").exists())
        self.assertEqual(self._status(), "")

    def test_candidate_commit_remains_checked_out_old_after_reconcile(self):
        (self.root / "app.py").write_text("new\n", encoding="utf-8")
        reconcile(self.root, self.old, self.new)
        self.assertEqual(self._git("rev-parse", "HEAD"), self.old)
        self.assertNotEqual(self._git("rev-parse", "HEAD"), self.new)


if __name__ == "__main__":
    unittest.main()

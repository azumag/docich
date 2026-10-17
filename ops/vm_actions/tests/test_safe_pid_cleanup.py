#!/usr/bin/env python3
"""Tests for safe_pid_cleanup module."""

import os
import stat
import tempfile
import unittest
from pathlib import Path

import safe_pid_cleanup


class TestSafePidCleanup(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.root = self.tmpdir
        # Create tmp/state directory
        os.makedirs(os.path.join(self.root, "tmp", "state"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "tmp", ".soren_loop.lock"), exist_ok=True)

    def _create_pid_file(self, relpath, pid, owner_uid=None):
        """Helper to create a PID file."""
        full = os.path.join(self.root, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(str(pid))
        if owner_uid is not None:
            os.chown(full, owner_uid, -1)
        return full

    def test_path_not_allowed_outside_tmp_state(self):
        """PID file outside tmp/state/ should not be deleted."""
        path = os.path.join(self.root, "etc", "evil.pid")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("99999")
        ok, reason = safe_pid_cleanup.safe_unlink_pid_file(path, root=self.root)
        self.assertFalse(ok)
        self.assertEqual(reason, "path_not_allowed")
        self.assertTrue(os.path.exists(path))

    def test_path_not_allowed_root_level(self):
        """PID file at root level should not be deleted."""
        path = os.path.join(self.root, "radio_worker.pid")
        with open(path, "w") as f:
            f.write("99999")
        ok, reason = safe_pid_cleanup.safe_unlink_pid_file(path, root=self.root)
        self.assertFalse(ok)
        self.assertEqual(reason, "path_not_allowed")

    def test_symlink_rejected(self):
        """Symlink PID file should not be deleted."""
        target = os.path.join(self.root, "tmp", "state", "real.pid")
        with open(target, "w") as f:
            f.write("99999")
        link = os.path.join(self.root, "tmp", "state", "link.pid")
        os.symlink(target, link)
        ok, reason = safe_pid_cleanup.safe_unlink_pid_file(link, root=self.root)
        self.assertFalse(ok)
        self.assertEqual(reason, "symlink")

    def test_hardlink_rejected(self):
        """Hardlink PID file should not be deleted."""
        original = os.path.join(self.root, "tmp", "state", "original.pid")
        with open(original, "w") as f:
            f.write("99999")
        hardlink = os.path.join(self.root, "tmp", "state", "hardlink.pid")
        os.link(original, hardlink)
        ok, reason = safe_pid_cleanup.safe_unlink_pid_file(hardlink, root=self.root)
        self.assertFalse(ok)
        self.assertEqual(reason, "hardlink")

    def test_foreign_owner_rejected(self):
        """PID file owned by different user should not be deleted."""
        path = self._create_pid_file("tmp/state/foreign.pid", 99999)
        # Try to set owner to a different UID (0 = root, if not root)
        try:
            os.chown(path, 0, -1)
        except PermissionError:
            self.skipTest("Cannot chown to root (not running as root)")
        ok, reason = safe_pid_cleanup.safe_unlink_pid_file(path, root=self.root, expected_owner=os.getuid())
        self.assertFalse(ok)
        self.assertEqual(reason, "foreign_owner")

    def test_alive_pid_not_deleted(self):
        """PID file with running PID should not be deleted."""
        path = self._create_pid_file("tmp/state/alive.pid", os.getpid())
        ok, reason = safe_pid_cleanup.safe_unlink_pid_file(path, root=self.root)
        self.assertFalse(ok)
        self.assertEqual(reason, "pid_alive")
        self.assertTrue(os.path.exists(path))

    def test_dead_pid_deleted(self):
        """PID file with dead PID should be deleted."""
        path = self._create_pid_file("tmp/state/dead.pid", 99999999)
        ok, reason = safe_pid_cleanup.safe_unlink_pid_file(path, root=self.root)
        self.assertTrue(ok)
        self.assertEqual(reason, "deleted")
        self.assertFalse(os.path.exists(path))

    def test_invalid_pid_not_deleted(self):
        """PID file with invalid content should not be deleted."""
        path = os.path.join(self.root, "tmp", "state", "invalid.pid")
        with open(path, "w") as f:
            f.write("not-a-number")
        ok, reason = safe_pid_cleanup.safe_unlink_pid_file(path, root=self.root)
        self.assertFalse(ok)
        self.assertTrue(reason.startswith("invalid_pid"))

    def test_empty_pid_not_deleted(self):
        """Empty PID file should not be deleted."""
        path = os.path.join(self.root, "tmp", "state", "empty.pid")
        with open(path, "w") as f:
            f.write("")
        ok, reason = safe_pid_cleanup.safe_unlink_pid_file(path, root=self.root)
        self.assertFalse(ok)
        self.assertTrue(reason.startswith("invalid_pid"))

    def test_soren_loop_lock_path_allowed(self):
        """PID file under tmp/.soren_loop.lock/ should be allowed."""
        path = self._create_pid_file("tmp/.soren_loop.lock/pid", 99999999)
        ok, reason = safe_pid_cleanup.safe_unlink_pid_file(path, root=self.root)
        self.assertTrue(ok)
        self.assertEqual(reason, "deleted")

    def test_dry_run_does_not_delete(self):
        """Dry run should not actually delete the file."""
        path = self._create_pid_file("tmp/state/dryrun.pid", 99999999)
        ok, reason = safe_pid_cleanup.safe_unlink_pid_file(path, root=self.root, dry_run=True)
        self.assertTrue(ok)
        self.assertEqual(reason, "would_delete")
        self.assertTrue(os.path.exists(path))

    def test_already_gone(self):
        """Non-existent file should return already_gone."""
        path = os.path.join(self.root, "tmp", "state", "nonexistent.pid")
        ok, reason = safe_pid_cleanup.safe_unlink_pid_file(path, root=self.root)
        self.assertFalse(ok)
        self.assertEqual(reason, "already_gone")

    def test_safe_cleanup_stale_pid_files(self):
        """safe_cleanup_stale_pid_files should scan and clean stale files."""
        # Create a stale PID file
        self._create_pid_file("tmp/state/stale_worker.pid", 99999999)
        # Create an alive PID file
        self._create_pid_file("tmp/state/alive_worker.pid", os.getpid())
        results = safe_pid_cleanup.safe_cleanup_stale_pid_files(root=self.root)
        # Should have 2 results
        self.assertEqual(len(results), 2)
        # Stale one should be deleted
        stale_result = [r for r in results if "stale_worker" in r[0]][0]
        self.assertTrue(stale_result[1])
        # Alive one should be skipped
        alive_result = [r for r in results if "alive_worker" in r[0]][0]
        self.assertFalse(alive_result[1])

    def test_safe_cleanup_empty_dir(self):
        """safe_cleanup_stale_pid_files on empty dir should return empty."""
        results = safe_pid_cleanup.safe_cleanup_stale_pid_files(root=self.root)
        self.assertEqual(results, [])

    def test_safe_cleanup_nonexistent_dir(self):
        """safe_cleanup_stale_pid_files on nonexistent root should return empty."""
        results = safe_pid_cleanup.safe_cleanup_stale_pid_files(root="/nonexistent/path")
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()

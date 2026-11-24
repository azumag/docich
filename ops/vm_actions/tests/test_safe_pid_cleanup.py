#!/usr/bin/env python3
"""Tests for safe_pid_cleanup module."""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from ops.vm_actions import safe_pid_cleanup


class TestSafePidCleanup(unittest.TestCase):
    DEAD_PID = 10101
    LIVE_PID = 20202

    def setUp(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        self.root = tmpdir.name
        self.running = self.enterContext(patch.object(
            safe_pid_cleanup, "_pid_is_running", side_effect=lambda pid: pid == self.LIVE_PID,
        ))
        self.enterContext(patch.object(safe_pid_cleanup, "_pid_is_zombie", return_value=False))
        self.enterContext(patch.object(safe_pid_cleanup, "_pid_reused", return_value=False))
        # All process observations are doubles; even signal 0 is forbidden here.
        self.enterContext(patch.object(os, "kill", side_effect=AssertionError("real PID probe")))
        # Create tmp/state directory
        os.makedirs(os.path.join(self.root, "tmp", "state"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "tmp", ".soren_loop.lock"), exist_ok=True)

    def _create_pid_file(self, relpath, pid):
        """Helper to create a PID file."""
        full = os.path.join(self.root, relpath)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(str(pid))
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
        ok, reason = safe_pid_cleanup.safe_unlink_pid_file(
            path, root=self.root, expected_owner=os.getuid() + 1,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "foreign_owner")

    def test_alive_pid_not_deleted(self):
        """PID file with running PID should not be deleted."""
        path = self._create_pid_file("tmp/state/alive.pid", self.LIVE_PID)
        ok, reason = safe_pid_cleanup.safe_unlink_pid_file(path, root=self.root)
        self.assertFalse(ok)
        self.assertEqual(reason, "pid_alive")
        self.assertTrue(os.path.exists(path))

    def test_dead_pid_retained_without_writer_lock(self):
        """A dead PID observation does not authorize unlinking the path."""
        path = self._create_pid_file("tmp/state/dead.pid", self.DEAD_PID)
        before = os.stat(path)
        ok, reason = safe_pid_cleanup.safe_unlink_pid_file(path, root=self.root)
        self.assertTrue(os.path.exists(path))
        self.assertFalse(ok)
        self.assertEqual(reason, "cleanup_disabled:no_writer_lock")
        self.assertEqual(os.stat(path).st_ino, before.st_ino)
        self.assertEqual(Path(path).read_text(), str(self.DEAD_PID))

    def test_atomic_replacement_after_pid_read_is_retained(self):
        """A new live worker's inode must survive an old dead PID probe."""
        path = self._create_pid_file("tmp/state/worker.pid", self.DEAD_PID)
        replacement = self._create_pid_file("tmp/state/new.pid", self.LIVE_PID)
        replacement_inode = os.stat(replacement).st_ino
        self.assertNotEqual(os.stat(path).st_ino, replacement_inode)

        def replace_on_probe(pid):
            if not replaced:
                self.assertEqual(pid, self.DEAD_PID)
                os.replace(replacement, path)
                replaced.append(True)
            return pid == self.LIVE_PID

        replaced = []
        self.running.side_effect = replace_on_probe
        ok, reason = safe_pid_cleanup.safe_unlink_pid_file(path, root=self.root)
        self.assertTrue(replaced)
        self.assertTrue(os.path.exists(path), "replacement PID file was deleted")
        self.assertFalse(ok)
        self.assertEqual(reason, "cleanup_disabled:no_writer_lock")
        self.assertEqual(os.stat(path).st_ino, replacement_inode)
        self.assertEqual(Path(path).read_text(), str(self.LIVE_PID))

    def test_atomic_replacement_after_final_exists_check_is_retained(self):
        """Even a replacement after the last observation cannot be unlinked."""
        path = self._create_pid_file("tmp/state/worker.pid", self.DEAD_PID)
        replacement = self._create_pid_file("tmp/state/new.pid", self.LIVE_PID)
        replacement_inode = os.stat(replacement).st_ino
        self.assertNotEqual(os.stat(path).st_ino, replacement_inode)
        real_exists = os.path.exists
        replaced = []

        def replace_after_check(candidate):
            exists = real_exists(candidate)
            if candidate == path and not replaced:
                self.assertTrue(exists)
                os.replace(replacement, path)
                replaced.append(True)
            return exists

        with patch.object(safe_pid_cleanup.os.path, "exists", side_effect=replace_after_check):
            ok, reason = safe_pid_cleanup.safe_unlink_pid_file(path, root=self.root)
        self.assertTrue(replaced)
        self.assertTrue(os.path.exists(path), "replacement PID file was deleted")
        self.assertFalse(ok)
        self.assertEqual(reason, "cleanup_disabled:no_writer_lock")
        self.assertEqual(os.stat(path).st_ino, replacement_inode)
        self.assertEqual(Path(path).read_text(), str(self.LIVE_PID))

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
        path = self._create_pid_file("tmp/.soren_loop.lock/pid", self.DEAD_PID)
        ok, reason = safe_pid_cleanup.safe_unlink_pid_file(path, root=self.root)
        self.assertFalse(ok)
        self.assertEqual(reason, "cleanup_disabled:no_writer_lock")
        self.assertTrue(os.path.exists(path))

    def test_dry_run_does_not_delete(self):
        """Legacy dry_run must not claim deletion would be safe."""
        path = self._create_pid_file("tmp/state/dryrun.pid", self.DEAD_PID)
        ok, reason = safe_pid_cleanup.safe_unlink_pid_file(path, root=self.root, dry_run=True)
        self.assertFalse(ok)
        self.assertEqual(reason, "cleanup_disabled:no_writer_lock")
        self.assertTrue(os.path.exists(path))

    def test_zombie_pid_retained_without_writer_lock(self):
        """Zombie observations also lack authority to delete a PID file."""
        path = self._create_pid_file("tmp/state/zombie.pid", self.LIVE_PID)
        with patch.object(safe_pid_cleanup, "_pid_is_zombie", return_value=True):
            ok, reason = safe_pid_cleanup.safe_unlink_pid_file(
                path, root=self.root, expected_cmdline="worker",
            )
        self.assertFalse(ok)
        self.assertEqual(reason, "cleanup_disabled:no_writer_lock")
        self.assertEqual(Path(path).read_text(), str(self.LIVE_PID))

    def test_reused_pid_reason_retained(self):
        path = self._create_pid_file("tmp/state/reused.pid", self.DEAD_PID)
        self.running.side_effect = [False, True]
        with patch.object(safe_pid_cleanup, "_pid_reused", return_value=True):
            ok, reason = safe_pid_cleanup.safe_unlink_pid_file(path, root=self.root)
        self.assertFalse(ok)
        self.assertEqual(reason, "pid_reused")
        self.assertTrue(os.path.exists(path))

    def test_already_gone(self):
        """Non-existent file should return already_gone."""
        path = os.path.join(self.root, "tmp", "state", "nonexistent.pid")
        ok, reason = safe_pid_cleanup.safe_unlink_pid_file(path, root=self.root)
        self.assertFalse(ok)
        self.assertEqual(reason, "already_gone")

    def test_safe_cleanup_stale_pid_files(self):
        """The legacy cleanup scan diagnoses and retains every PID file."""
        # Create a stale PID file
        stale = self._create_pid_file("tmp/state/stale_worker.pid", self.DEAD_PID)
        # Create an alive PID file
        alive = self._create_pid_file("tmp/state/alive_worker.pid", self.LIVE_PID)
        lock = self._create_pid_file("tmp/.soren_loop.lock/pid", self.DEAD_PID)
        results = safe_pid_cleanup.safe_cleanup_stale_pid_files(root=self.root)
        self.assertCountEqual(results, [
            (stale, False, "cleanup_disabled:no_writer_lock"),
            (alive, False, "pid_alive"),
            (lock, False, "cleanup_disabled:no_writer_lock"),
        ])
        for path in (stale, alive, lock):
            self.assertTrue(os.path.exists(path))

    def test_safe_cleanup_empty_dir(self):
        """safe_cleanup_stale_pid_files on empty dir should return empty."""
        results = safe_pid_cleanup.safe_cleanup_stale_pid_files(root=self.root)
        self.assertEqual(results, [])

    def test_safe_cleanup_nonexistent_dir(self):
        """safe_cleanup_stale_pid_files on nonexistent root should return empty."""
        results = safe_pid_cleanup.safe_cleanup_stale_pid_files(root="/nonexistent/path")
        self.assertEqual(results, [])

    def test_cli_retains_dead_and_live_files_with_or_without_dry_run(self):
        dead = self._create_pid_file("tmp/state/dead.pid", self.DEAD_PID)
        live = self._create_pid_file("tmp/state/live.pid", self.LIVE_PID)
        for argv in ([self.root], [self.root, "--dry-run"], ["--dry-run", self.root]):
            with self.subTest(argv=argv):
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    safe_pid_cleanup.main(argv)
                self.assertCountEqual(stdout.getvalue().splitlines(), [
                    f"SKIP: {dead} (cleanup_disabled:no_writer_lock)",
                    f"SKIP: {live} (pid_alive)",
                ])
                self.assertEqual(Path(dead).read_text(), str(self.DEAD_PID))
                self.assertEqual(Path(live).read_text(), str(self.LIVE_PID))

    def test_cli_flag_without_root_uses_default_root(self):
        # Stub the scan so this test never reads the default installation path.
        with patch.object(safe_pid_cleanup, "safe_cleanup_stale_pid_files", return_value=[]) as scan:
            safe_pid_cleanup.main(["--dry-run"])
        scan.assert_called_once_with(root="/home/ubuntu/soren", dry_run=True)

    def test_cli_help_describes_diagnostics_only(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout), self.assertRaises(SystemExit) as exit_context:
            safe_pid_cleanup.main(["--help"])
        self.assertEqual(exit_context.exception.code, 0)
        help_text = " ".join(stdout.getvalue().split())
        self.assertIn("Never deletes PID files", help_text)
        self.assertIn("cleanup_disabled:no_writer_lock", help_text)
        self.assertIn("not cleanup", help_text)


if __name__ == "__main__":
    unittest.main()

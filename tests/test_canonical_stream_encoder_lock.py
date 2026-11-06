"""Cross-state-dir and symlink/permissions contracts for the opt-in host lock."""
import fcntl
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import docich.stream_instance_lock as locks  # noqa: E402


CHILD = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import docich.stream_instance_lock as lock
lock.CANONICAL_STREAM_ENCODER_LOCK_PATH = sys.argv[1]
try:
    with lock.open_canonical_stream_encoder_lock():
        raise SystemExit(0)
except BlockingIOError:
    raise SystemExit(11)
"""


class CanonicalStreamEncoderLockTest(unittest.TestCase):
    def contender(self, lock_path, state_dir):
        return subprocess.run(
            [sys.executable, "-c", CHILD, str(lock_path), str(ROOT / "src")],
            capture_output=True, text=True, timeout=12, check=False,
            env={**os.environ, "SOREN_DIRECT_STREAM_STATE_DIR": str(state_dir)},
        )

    def test_fixed_path_and_version_do_not_depend_on_runtime_env(self):
        self.assertEqual(locks.CANONICAL_STREAM_ENCODER_LOCK_CONTRACT_VERSION, 1)
        self.assertEqual(
            locks.CANONICAL_STREAM_ENCODER_LOCK_PATH,
            "/home/ubuntu/docich/run-soren-live/locks/direct_stream_encoder.lock",
        )
        source = (ROOT / "src/docich/stream_instance_lock.py").read_text()
        self.assertNotIn("games/soviet_now", source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("LOCK_UN", source)

    def test_cross_state_dir_contention_and_release(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            parent = root / "locks"
            parent.mkdir(mode=0o700)
            path = parent / "direct_stream_encoder.lock"
            with mock.patch.object(locks, "CANONICAL_STREAM_ENCODER_LOCK_PATH", str(path)):
                with locks.open_canonical_stream_encoder_lock() as handle:
                    self.assertTrue(handle.fileno() >= 0)
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                    self.assertEqual(
                        self.contender(path, root / "state/another-service").returncode, 11
                    )
                    self.assertEqual(
                        self.contender(path, root / "state/soviet").returncode, 11
                    )
                    self.assertTrue(fcntl.fcntl(handle.fileno(), fcntl.F_GETFD) & fcntl.FD_CLOEXEC)
                self.assertEqual(
                    self.contender(path, root / "state/another-service").returncode, 0
                )

    def test_missing_directory_or_insecure_parent_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "missing" / "encoder.lock"
            with mock.patch.object(locks, "CANONICAL_STREAM_ENCODER_LOCK_PATH", str(path)):
                with self.assertRaises(OSError):
                    locks.open_canonical_stream_encoder_lock()
                self.assertFalse(path.parent.exists())
                path.parent.mkdir(mode=0o777)
                path.parent.chmod(0o777)
                with self.assertRaises(PermissionError):
                    locks.open_canonical_stream_encoder_lock()
                self.assertFalse(path.exists())

    def test_symlinks_and_world_readable_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            private = root / "private"
            private.mkdir(mode=0o700)
            path = private / "encoder.lock"
            target = private / "target"
            target.write_text("unrelated")
            with mock.patch.object(locks, "CANONICAL_STREAM_ENCODER_LOCK_PATH", str(path)):
                path.symlink_to(target)
                with self.assertRaises(OSError):
                    locks.open_canonical_stream_encoder_lock()
                self.assertEqual(target.read_text(), "unrelated")
                path.unlink()
                path.write_text("")
                path.chmod(0o644)
                with self.assertRaises(PermissionError):
                    locks.open_canonical_stream_encoder_lock()
                path.unlink()
                path.mkdir()
                with self.assertRaises(OSError):
                    locks.open_canonical_stream_encoder_lock()


if __name__ == "__main__":
    unittest.main()

"""Hermetic single-encoder flock contract without FFmpeg or a VM."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from docich.stream_instance_lock import (  # noqa: E402
    SINGLE_ENCODER_LOCK_CONTRACT_VERSION,
    acquire_nonblocking_encoder_lock,
)

# Process contention must be exercised using separately opened file descriptions.
CHILD = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from docich.stream_instance_lock import acquire_nonblocking_encoder_lock
with Path(sys.argv[1]).open('a+') as handle:
    try:
        acquire_nonblocking_encoder_lock(handle.fileno())
    except BlockingIOError:
        raise SystemExit(11)
    raise SystemExit(0)
"""


class SingleEncoderLockTest(unittest.TestCase):
    def test_versioned_no_launch_or_service_side_effects(self):
        self.assertEqual(SINGLE_ENCODER_LOCK_CONTRACT_VERSION, 1)
        source = (ROOT / "src/docich/stream_instance_lock.py").read_text(encoding="utf-8")
        self.assertNotIn("games/soviet_now", source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("LOCK_UN", source)

    def contender(self, lock: Path):
        return subprocess.run(
            [sys.executable, "-c", CHILD, str(lock), str(ROOT / "src")],
            capture_output=True, text=True, check=False, timeout=12,
        )

    def test_one_process_holds_the_same_path_through_reentrant_fd_locking(self):
        with tempfile.TemporaryDirectory(prefix="single-encoder-flock-") as td:
            lock = Path(td) / "direct_stream.lock"
            with lock.open("a+") as handle:
                acquire_nonblocking_encoder_lock(handle.fileno())
                # Same FD reacquisition must not release or weaken the lock.
                acquire_nonblocking_encoder_lock(handle.fileno())
                blocked = self.contender(lock)
                self.assertEqual(blocked.returncode, 11, blocked.stderr)
            released = self.contender(lock)
            self.assertEqual(released.returncode, 0, released.stderr)

    def test_second_fd_in_same_process_cannot_take_the_lock(self):
        with tempfile.TemporaryDirectory(prefix="single-encoder-flock-") as td:
            lock = Path(td) / "direct_stream.lock"
            with lock.open("a+") as first, lock.open("a+") as second:
                acquire_nonblocking_encoder_lock(first.fileno())
                with self.assertRaises(BlockingIOError):
                    acquire_nonblocking_encoder_lock(second.fileno())


if __name__ == "__main__":
    unittest.main()

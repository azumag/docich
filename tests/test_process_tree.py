import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.process_tree import process_table, terminate_process_tree  # noqa: E402


class TestProcessTree(unittest.TestCase):
    def test_terminates_root_and_descendants(self):
        if not process_table():
            self.skipTest("process table is unavailable in this sandbox")
        child_code = "import time; time.sleep(30)"
        parent_code = (
            "import subprocess,sys,time; "
            "subprocess.Popen([sys.executable, '-c', sys.argv[1]]); "
            "time.sleep(30)"
        )
        parent = subprocess.Popen(
            [sys.executable, "-c", parent_code, child_code],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            # Give the child enough time to appear in ps/proc on both Linux and
            # macOS before taking the ownership snapshot.
            time.sleep(0.1)
            result = terminate_process_tree(
                [parent.pid], term_timeout_s=0.5, kill_timeout_s=0.5
            )
            parent.wait(timeout=2)
            self.assertEqual(result.remaining, ())
            self.assertFalse(_pid_is_running(parent.pid))
        finally:
            if _pid_is_running(parent.pid):
                os.kill(parent.pid, 9)
                parent.wait(timeout=2)


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


if __name__ == "__main__":
    unittest.main()

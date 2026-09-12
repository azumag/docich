import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "ops" / "vm_actions" / "restart_radio_worker.sh"


class RestartRadioWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        (self.root / "workers").mkdir(parents=True)
        (self.root / "tmp" / "state").mkdir(parents=True)
        self.worker_script = self.root / "workers" / "radio_worker.sh"
        self.worker_script.write_text(
            "#!/usr/bin/env bash\n"
            "trap 'exit 0' TERM\n"
            "while true; do sleep 0.1; done\n",
            encoding="utf-8",
        )
        self.worker_script.chmod(0o755)
        self.pid_file = self.root / "tmp" / "state" / "radio_worker.pid"
        self.children = []

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=2)
        self.tempdir.cleanup()

    def start_worker(self):
        child = subprocess.Popen(
            ["bash", str(self.worker_script)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.children.append(child)
        return child

    def test_replaces_only_live_radio_worker_and_waits_for_new_pid(self):
        old = self.start_worker()
        self.pid_file.write_text(f"{old.pid}\n", encoding="utf-8")
        replacement = {}

        def supervise():
            old.wait(timeout=5)
            new = self.start_worker()
            replacement["pid"] = new.pid
            self.pid_file.write_text(f"{new.pid}\n", encoding="utf-8")

        thread = threading.Thread(target=supervise, daemon=True)
        thread.start()
        result = subprocess.run(
            ["bash", str(HELPER), "--root", str(self.root)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
            check=False,
        )
        thread.join(timeout=5)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pid", replacement)
        self.assertNotEqual(old.pid, replacement["pid"])
        os.kill(replacement["pid"], 0)

    def test_refuses_pid_that_is_not_radio_worker(self):
        sleeper = subprocess.Popen(["sleep", "10"])
        self.children.append(sleeper)
        self.pid_file.write_text(f"{sleeper.pid}\n", encoding="utf-8")

        result = subprocess.run(
            ["bash", str(HELPER), "--root", str(self.root)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(sleeper.poll(), "helper must not signal unrelated process")


if __name__ == "__main__":
    unittest.main()

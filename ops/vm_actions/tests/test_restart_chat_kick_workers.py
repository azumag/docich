import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "ops" / "vm_actions" / "restart_chat_kick_workers.sh"


@unittest.skipUnless(
    sys.platform.startswith("linux"),
    "restart helper identifies workers through the Linux /proc filesystem",
)
class RestartChatKickWorkersTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        (self.root / "workers").mkdir(parents=True)
        (self.root / "tmp" / "state").mkdir(parents=True)
        self.scripts = {}
        for name in ("chat_worker", "kick_worker"):
            script = self.root / "workers" / f"{name}.sh"
            script.write_text(
                "#!/usr/bin/env bash\n"
                "trap 'exit 0' TERM\n"
                "while true; do sleep 0.1; done\n",
                encoding="utf-8",
            )
            script.chmod(0o755)
            self.scripts[name] = script
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

    def start_worker(self, name):
        child = subprocess.Popen(
            ["bash", str(self.scripts[name])],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.children.append(child)
        return child

    def pid_file(self, name):
        return self.root / "tmp" / "state" / f"{name}.pid"

    def record(self, name, pid):
        self.pid_file(name).write_text(f"{pid}\n", encoding="utf-8")

    def supervise(self, name, old, replacement):
        def run():
            old.wait(timeout=5)
            new = self.start_worker(name)
            replacement[name] = new.pid
            self.record(name, new.pid)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread

    def run_helper(self):
        return subprocess.run(
            ["bash", str(HELPER), "--root", str(self.root)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=90,
            check=False,
        )

    def test_replaces_both_chat_workers_and_waits_for_new_pids(self):
        old = {}
        record = {}
        threads = []
        for name in ("chat_worker", "kick_worker"):
            old[name] = self.start_worker(name)
            self.record(name, old[name].pid)
            threads.append(self.supervise(name, old[name], record))

        result = self.run_helper()
        for thread in threads:
            thread.join(timeout=5)

        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ("chat_worker", "kick_worker"):
            self.assertIn(name, record)
            self.assertNotEqual(old[name].pid, record[name])
            os.kill(record[name], 0)

    def test_skips_an_intentionally_paused_worker(self):
        # chat_worker is paused: the operator's stop is preserved, so the paused
        # worker must not be signalled and the operation still succeeds for the
        # remaining (healthy) kick_worker.
        paused = self.start_worker("chat_worker")
        self.record("chat_worker", paused.pid)
        (self.root / "tmp" / "state" / "chat_worker.paused").write_text("", encoding="utf-8")
        old_kick = self.start_worker("kick_worker")
        self.record("kick_worker", old_kick.pid)
        record = {}
        thread = self.supervise("kick_worker", old_kick, record)

        result = self.run_helper()
        thread.join(timeout=5)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(paused.poll(), "paused worker must not be signalled")
        self.assertIn("kick_worker", record)

    def test_refuses_pid_that_is_not_the_reviewed_worker(self):
        sleeper = subprocess.Popen(["sleep", "10"])
        self.children.append(sleeper)
        self.record("chat_worker", sleeper.pid)

        result = self.run_helper()

        self.assertEqual(result.returncode, 10, result.stderr)
        self.assertIsNone(sleeper.poll(), "helper must not signal an unrelated process")

    def test_missing_worker_fails_closed_with_its_own_code(self):
        # No pid file at all: nothing to activate, so the operation must not
        # report success for a worker it never replaced.
        result = self.run_helper()

        self.assertEqual(result.returncode, 10, result.stderr)

    def test_reports_which_worker_did_not_come_back(self):
        # kick_worker is live but nothing respawns it: the operation fails with
        # kick_worker's own code, and chat_worker is still attempted first.
        old_chat = self.start_worker("chat_worker")
        self.record("chat_worker", old_chat.pid)
        old_kick = self.start_worker("kick_worker")
        self.record("kick_worker", old_kick.pid)
        record = {}
        thread = self.supervise("chat_worker", old_chat, record)

        result = self.run_helper()
        thread.join(timeout=5)

        self.assertEqual(result.returncode, 22, result.stderr)
        self.assertIn("chat_worker", record)


if __name__ == "__main__":
    unittest.main()

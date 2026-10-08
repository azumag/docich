import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "ops" / "vm_actions" / "restart_poll_workers.sh"

TARGETS = ("audio_worker", "youtube_worker", "poll_worker", "prediction_worker", "stream_noon_audit")


@unittest.skipUnless(
    sys.platform.startswith("linux"),
    "restart helper identifies workers through the Linux /proc filesystem",
)
class RestartPollWorkersTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        (self.root / "workers").mkdir(parents=True)
        (self.root / "tmp" / "state").mkdir(parents=True)
        self.scripts = {}
        for name in TARGETS:
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
            timeout=120,
            check=False,
        )

    def test_replaces_every_supervised_poll_worker(self):
        old = {}
        record = {}
        threads = []
        for name in TARGETS:
            old[name] = self.start_worker(name)
            self.record(name, old[name].pid)
            threads.append(self.supervise(name, old[name], record))

        result = self.run_helper()
        for thread in threads:
            thread.join(timeout=5)

        self.assertEqual(result.returncode, 0, result.stderr)
        for name in TARGETS:
            self.assertIn(name, record)
            self.assertNotEqual(old[name].pid, record[name])
            os.kill(record[name], 0)

    def test_workers_without_a_pid_file_are_skipped_not_failed(self):
        # A worker that was never started (or is disabled by config) must not
        # fail a deploy that is restarting the others.
        old_audio = self.start_worker("audio_worker")
        self.record("audio_worker", old_audio.pid)
        record = {}
        thread = self.supervise("audio_worker", old_audio, record)

        result = self.run_helper()
        thread.join(timeout=5)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("audio_worker", record)
        for name in TARGETS:
            if name != "audio_worker":
                self.assertIn(f"skip: {name}", result.stderr)

    def test_skips_a_paused_worker(self):
        paused = self.start_worker("audio_worker")
        self.record("audio_worker", paused.pid)
        (self.root / "tmp" / "state" / "audio_worker.paused").write_text("", encoding="utf-8")

        result = self.run_helper()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(paused.poll(), "paused worker must not be signalled")
        self.assertIn("intentionally paused", result.stderr)

    def test_refuses_a_live_pid_that_is_not_the_reviewed_worker(self):
        sleeper = subprocess.Popen(["sleep", "30"])
        self.children.append(sleeper)
        self.record("audio_worker", sleeper.pid)

        result = self.run_helper()

        self.assertEqual(result.returncode, 10, result.stderr)
        self.assertIsNone(sleeper.poll(), "helper must not signal an unrelated process")

    def test_a_worker_that_outlives_the_term_wait_is_skipped_not_failed(self):
        # poll_worker is live and refuses to exit (this is what audio_worker does
        # while a playback child is in the foreground). The TERM is delivered, so
        # the rollout must log it and let the rest of the deploy continue rather
        # than failing the step and skipping the remaining steps.
        script = self.root / "workers" / "poll_worker.sh"
        script.write_text(
            "#!/usr/bin/env bash\n"
            "trap '' TERM\n"
            "while true; do sleep 0.1; done\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
        stubborn = self.start_worker("poll_worker")
        self.record("poll_worker", stubborn.pid)

        result = self.run_helper()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("still winding down", result.stderr)
        self.assertIn(f"TERM delivered to PID {stubborn.pid}", result.stderr)

    def test_reports_the_first_worker_that_did_not_come_back(self):
        # audio/youtube have no pid file (skipped); poll_worker is live but
        # nothing respawns it, so the operation fails with poll_worker's code.
        old_poll = self.start_worker("poll_worker")
        self.record("poll_worker", old_poll.pid)

        result = self.run_helper()

        self.assertEqual(result.returncode, 32, result.stderr)


if __name__ == "__main__":
    unittest.main()

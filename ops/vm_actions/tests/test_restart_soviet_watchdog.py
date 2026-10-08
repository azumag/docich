"""Regression tests for ops/vm_actions/restart_soviet_watchdog.sh (#970).

`soviet_watchdog.sh` has no pid file: it owns its singleton through the
directory lock `tmp/state/.soviet_watchdog.lock` (its `owner` file), so the
pid-file restart helpers cannot replace it. These tests pin the reviewed
behaviour of the dedicated helper: it signals only the live *reviewed*
watchdog, refuses a foreign live PID, honours the operator pause marker and a
parked game-only lifecycle handover, and fails closed when the supervisor does
not bring a replacement back.
"""

import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "ops" / "vm_actions" / "restart_soviet_watchdog.sh"
LOCK_DIR_REL = "tmp/state/.soviet_watchdog.lock"


@unittest.skipUnless(
    sys.platform.startswith("linux"),
    "restart helper identifies the watchdog through the Linux /proc filesystem",
)
class RestartSovietWatchdogTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        (self.root / "tmp" / "state").mkdir(parents=True)
        (self.root / "lib").mkdir(parents=True)
        self.script = self.root / "soviet_watchdog.sh"
        self.script.write_text(
            "#!/usr/bin/env bash\n"
            "trap 'exit 0' TERM\n"
            "while true; do sleep 0.1; done\n",
            encoding="utf-8",
        )
        self.script.chmod(0o755)
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

    def start_watchdog(self):
        child = subprocess.Popen(
            ["bash", str(self.script)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.children.append(child)
        return child

    def record_owner(self, pid):
        lock_dir = self.root / LOCK_DIR_REL
        lock_dir.mkdir(parents=True, exist_ok=True)
        (lock_dir / "owner").write_text(f"{pid}\n", encoding="utf-8")

    def clear_owner(self):
        lock_dir = self.root / LOCK_DIR_REL
        (lock_dir / "owner").unlink(missing_ok=True)

    def park_lifecycle(self):
        (self.root / "lib" / "game_lifecycle.sh").write_text(
            "#!/bin/bash\ngame_lifecycle_bridge_parked() { return 0; }\n",
            encoding="utf-8",
        )

    def supervise(self, old, replacement):
        def run():
            old.wait(timeout=5)
            new = self.start_watchdog()
            replacement["pid"] = new.pid
            self.record_owner(new.pid)

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

    def test_replaces_the_supervised_watchdog(self):
        old = self.start_watchdog()
        self.record_owner(old.pid)
        replacement = {}
        thread = self.supervise(old, replacement)

        result = self.run_helper()
        thread.join(timeout=5)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("pid", replacement)
        self.assertNotEqual(old.pid, replacement["pid"])
        os.kill(replacement["pid"], 0)

    def test_skips_an_intentionally_paused_watchdog(self):
        paused = self.start_watchdog()
        self.record_owner(paused.pid)
        (self.root / "tmp" / "state" / "soviet_watchdog.paused").write_text("", encoding="utf-8")

        result = self.run_helper()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(paused.poll(), "paused watchdog must not be signalled")
        self.assertIn("intentionally paused", result.stderr)

    def test_skips_when_no_live_lock_owner(self):
        # Never started / already restarting: the lock owner file is absent.
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("skip: no live supervised soviet_watchdog", result.stderr)

    def test_refuses_a_live_pid_that_is_not_the_watchdog(self):
        sleeper = subprocess.Popen(["sleep", "30"])
        self.children.append(sleeper)
        self.record_owner(sleeper.pid)

        result = self.run_helper()

        self.assertEqual(result.returncode, 10, result.stderr)
        self.assertIsNone(sleeper.poll(), "helper must not signal an unrelated process")
        self.assertIn("not the reviewed watchdog", result.stderr)

    def test_reports_a_watchdog_that_does_not_come_back(self):
        old = self.start_watchdog()
        self.record_owner(old.pid)

        result = self.run_helper()

        self.assertEqual(result.returncode, 12, result.stderr)
        self.assertIn("no live reviewed replacement", result.stderr)

    def test_reports_a_watchdog_that_survives_the_term_wait(self):
        # Rewrite the script before the process starts: the running bash keeps
        # the trap it was launched with.
        self.script.write_text(
            "#!/usr/bin/env bash\ntrap '' TERM\nwhile true; do sleep 0.1; done\n",
            encoding="utf-8",
        )
        self.script.chmod(0o755)
        stubborn = self.start_watchdog()
        self.record_owner(stubborn.pid)

        result = self.run_helper()

        self.assertEqual(result.returncode, 11, result.stderr)
        self.assertIn("still alive after TERM", result.stderr)

    def test_skips_while_a_game_only_handover_is_parked(self):
        parked = self.start_watchdog()
        self.record_owner(parked.pid)
        self.park_lifecycle()

        result = self.run_helper()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(parked.poll(), "parked watchdog must not be signalled")
        self.assertIn("parked", result.stderr)

    def test_helper_is_fixed_and_accepts_no_worker_name(self):
        body = HELPER.read_text(encoding="utf-8")
        self.assertIn('root="/home/ubuntu/soren"', body)
        self.assertIn(".soviet_watchdog.lock/owner", body)
        self.assertIn("soviet_watchdog.sh", body)
        for forbidden in ("systemctl", "sudo", "tmux", "pkill"):
            self.assertNotIn(forbidden, body)


if __name__ == "__main__":
    unittest.main()

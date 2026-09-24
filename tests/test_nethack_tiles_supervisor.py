import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.nethack_spectator_live import ActiveRuntime  # noqa: E402
from docich.nethack_tiles_supervisor import (  # noqa: E402
    NethackTilesSupervisor,
    browser_binary,
    manifest_matches,
    presentation_window_pattern,
)


class _FakeProcess:
    pid = 34567

    def __init__(self):
        self.returncode = None

    def poll(self):
        return self.returncode


class NethackTilesSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="nethack-tiles-")
        self.root = Path(self.tmp.name)
        self.runtime = ActiveRuntime(
            runtime_id="g9-abcdef",
            generation=9,
            adapter_session="docich-game-g9",
            game_window="game-g9",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def supervisor(self):
        runtime_dir = self.root / "runtimes" / self.runtime.runtime_id
        runtime_dir.mkdir(parents=True, exist_ok=True)
        return NethackTilesSupervisor(
            state_dir=self.root / "state",
            runtime_dir=runtime_dir,
            manifest_path=runtime_dir / "nethack_tiles.json",
            presentation_state_path=runtime_dir / "presentation.json",
            runtime=self.runtime,
        )

    def test_manifest_requires_exact_generation_owned_runtime(self):
        value = {
            "schema_version": 1,
            "runtime_id": self.runtime.runtime_id,
            "generation": self.runtime.generation,
            "adapter_session": self.runtime.adapter_session,
            "game_window": self.runtime.game_window,
        }
        self.assertTrue(manifest_matches(value, runtime=self.runtime))
        value["generation"] += 1
        self.assertFalse(manifest_matches(value, runtime=self.runtime))

    def test_browser_command_is_loopback_only_and_runtime_profile_scoped(self):
        supervisor = self.supervisor()
        supervisor._which = lambda name: "/usr/bin/chromium" if name == "chromium" else None
        command = supervisor._browser_command(43210)
        self.assertEqual(command[0], "/usr/bin/chromium")
        self.assertIn("--app=http://127.0.0.1:43210/", command)
        self.assertIn("--disable-background-networking", command)
        self.assertIn("--disable-extensions", command)
        profile = Path(
            next(arg.split("=", 1)[1] for arg in command if arg.startswith("--user-data-dir="))
        )
        self.assertEqual(profile.parent, supervisor.runtime_dir)
        self.assertFalse(any("remote-debugging" in arg for arg in command))

    def test_browser_binary_prefers_installed_google_chrome(self):
        calls = []
        paths = {
            "google-chrome-stable": "/usr/bin/google-chrome-stable",
            "chromium": "/usr/bin/chromium",
        }

        def which(name):
            calls.append(name)
            return paths.get(name)

        self.assertEqual(browser_binary(which), "/usr/bin/google-chrome-stable")
        self.assertEqual(calls, ["google-chrome-stable"])

    def test_presentation_window_pattern_is_exact_except_known_browser_suffixes(self):
        title = "docich.present-g9-abcdef"
        pattern = presentation_window_pattern(title)
        self.assertNotIn(r"\-", pattern)
        self.assertIsNotNone(re.fullmatch(pattern, title))
        self.assertIsNotNone(
            re.fullmatch(pattern, f"{title} - Google Chrome")
        )
        self.assertIsNotNone(
            re.fullmatch(pattern, f"{title} - Chromium Web Browser")
        )
        self.assertIsNone(re.fullmatch(pattern, f"{title} - Firefox"))
        self.assertIsNone(re.fullmatch(pattern, "xdocich.present-g9-abcdef"))
        with self.assertRaises(ValueError):
            presentation_window_pattern("viewer.*")

    def test_browser_window_wait_failure_distinguishes_exit_from_missing_window(self):
        process = _FakeProcess()
        self.assertEqual(
            NethackTilesSupervisor._window_wait_failure_reason(process),
            "browser_window_missing",
        )
        process.returncode = 1
        self.assertEqual(
            NethackTilesSupervisor._window_wait_failure_reason(process),
            "browser_exited",
        )

    def test_server_failure_falls_back_once_and_records_owned_tty(self):
        supervisor = self.supervisor()
        process = _FakeProcess()
        supervisor._stop_frame_service = mock.Mock(return_value=True)
        supervisor._start_tty = mock.Mock(side_effect=lambda: setattr(supervisor, "_tty", process))

        self.assertTrue(supervisor._fallback("server_start_failed"))
        record = json.loads(supervisor.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "fallback_tty")
        self.assertEqual(record["mode"], "tty")
        self.assertEqual(record["reason"], "server_start_failed")
        self.assertEqual(record["tty_pid"], process.pid)

        self.assertFalse(supervisor._fallback("browser_exited"))
        final = json.loads(supervisor.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(final["status"], "failed")
        supervisor._start_tty.assert_called_once()

    def test_fallback_start_failure_is_terminal_and_sanitized(self):
        supervisor = self.supervisor()
        supervisor._stop_frame_service = mock.Mock(return_value=True)
        supervisor._start_tty = mock.Mock(side_effect=RuntimeError("secret/path/browser argv"))

        self.assertFalse(supervisor._fallback("projection_failed"))
        record = json.loads(supervisor.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["reason"], "fallback_start_failed")
        self.assertNotIn("secret", json.dumps(record))
        self.assertNotIn("argv", json.dumps(record))

    def test_owned_process_group_cleanup_is_bounded(self):
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.assertTrue(NethackTilesSupervisor._stop_process(process))
        self.assertIsNotNone(process.poll())

    def test_cleanup_kills_descendant_that_ignores_term(self):
        script = (
            "import signal, subprocess, sys, time; "
            "subprocess.Popen([sys.executable, '-c', "
            "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)']); "
            "time.sleep(30)"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", script],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.1)
        try:
            try:
                os.killpg(process.pid, 0)
            except PermissionError:
                # Some managed macOS sandboxes allow targeted TERM/KILL but
                # forbid the POSIX existence probe. The Ubuntu CI runner does
                # support it and exercises the full descendant cleanup path.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    pass
                self.skipTest("sandbox denies process-group existence probes")
            stopped = NethackTilesSupervisor._stop_process(process)
            if not stopped:
                try:
                    os.killpg(process.pid, 0)
                except PermissionError:
                    self.skipTest("sandbox denies process-group probes after leader exit")
            self.assertTrue(stopped)
            self.assertIsNotNone(process.poll())
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()

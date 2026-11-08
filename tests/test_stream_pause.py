"""The docich operator pause marker and the supervisor's idling while it exists."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stream_runner_support import MAIN_DRIVER, ROOT, Sandbox  # noqa: E402

sys.path.insert(0, str(ROOT / "src"))
import docich.stream_pause as pause  # noqa: E402
import docich.soren_recovery_process as recovery  # noqa: E402


class MarkerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="stream-pause-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_set_clear_and_payload(self):
        self.assertFalse(pause.is_paused(self.root))
        pause.set_paused(self.root, True, source="webui")
        self.assertTrue(pause.is_paused(self.root))
        self.assertEqual(pause.marker_path(self.root), self.root / "tmp/state/docich_stream.paused")
        payload = json.loads(pause.marker_path(self.root).read_text())
        self.assertEqual((payload["paused"], payload["source"]), (True, "webui"))
        self.assertEqual(sorted(p.name for p in pause.marker_path(self.root).parent.iterdir()), ["docich_stream.paused"])
        pause.set_paused(self.root, False)
        pause.set_paused(self.root, False)  # idempotent
        self.assertFalse(pause.is_paused(self.root))

    def test_it_is_not_the_legacy_marker(self):
        pause.set_paused(self.root, True)
        self.assertFalse((self.root / "tmp/state/direct_stream.paused").exists())

    def test_wait_returns_immediately_when_not_paused_and_polls_until_cleared(self):
        sleeps, logs = [], []
        self.assertFalse(pause.wait_while_paused(self.root, sleep=sleeps.append, announce=logs.append))
        self.assertEqual((sleeps, logs), ([], []))
        pause.set_paused(self.root, True)
        def sleeper(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 3:
                pause.set_paused(self.root, False)
        self.assertTrue(pause.wait_while_paused(self.root, sleep=sleeper, poll_sec=0.5, announce=logs.append))
        self.assertEqual(sleeps, [0.5, 0.5, 0.5])
        self.assertEqual(len(logs), 2)


class RecoveryClassificationTest(unittest.TestCase):
    def test_the_docich_supervisor_is_a_common_process_not_a_game_one(self):
        self.assertIn("docich.stream_supervisor", recovery.COMMON)
        self.assertEqual({Path(a).name for a in ["python3", "-m", "docich.stream_supervisor", "run"]} & recovery.COMMON,
                         {"docich.stream_supervisor"})


class SupervisorIdlesWhilePausedTest(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)

    def launch(self):
        env = {"PATH": os.environ.get("PATH", ""), "SOREN_DIRECT_STREAM_STATE_DIR": str(self.box.state),
               "SOREN_DIRECT_STREAM_LOG_FILE": str(self.box.logs / "s.log"),
               "SOREN_DIRECT_STREAM_FFMPEG_BIN": str(self.box.ffmpeg),
               "SOREN_DIRECT_STREAM_AUDIO_WATCHDOG_ENABLED": "0", "SOREN_DIRECT_STREAM_RECONNECT_ENABLED": "0",
               "DOCICH_STREAM_OWNER": "docich", **self.box.env()}
        spec = {"src": str(ROOT / "src"), "canonical": str(self.box.canonical),
                "argv": ["--root", str(self.box.root), "run"], "env": env}
        process = subprocess.Popen([sys.executable, "-B", "-c", MAIN_DRIVER, json.dumps(spec)], cwd=str(self.box.root),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
                                   env={**os.environ, **self.box.env()})
        self.box.processes.append(process)
        return process

    def test_no_encoder_no_lock_no_files_while_paused_then_it_starts_on_resume(self):
        self.box.precreate_locks()
        pause.set_paused(self.box.root, True, source="test")
        process = self.launch()
        time.sleep(1.5)
        self.assertIsNone(process.poll(), "the paused supervisor must idle, not exit")
        self.assertEqual(self.box.started(), [])
        self.assertEqual(self.box.contend(), {"canonical": "acquired", "legacy": "acquired"}, "an idle supervisor holds no lock")
        self.assertFalse((self.box.logs).exists(), "no log directory is created while paused")
        pause.set_paused(self.box.root, False)
        self.box.wait_for(lambda: self.box.status().get("running") and self.box.status().get("ffmpeg_pid"))
        self.box.wait_for(lambda: len(self.box.started()) == 1)
        process.send_signal(signal.SIGTERM)
        _out, err = process.communicate(timeout=45)
        self.assertEqual(process.returncode, 0, err)
        self.assertEqual(self.box.status()["state"], "stopped")

    def test_an_operator_stop_cycle_leaves_a_resumable_idle_supervisor(self):
        """Marker + clean stop + respawn (what Restart=always does) + marker removal = running again."""
        self.box.precreate_locks()
        first = self.launch()
        self.box.wait_for(lambda: self.box.status().get("running") and self.box.status().get("ffmpeg_pid"))
        pause.set_paused(self.box.root, True)
        first.send_signal(signal.SIGTERM)          # what `stream_supervisor stop` sends
        first.communicate(timeout=45)
        self.assertEqual(first.returncode, 0)
        second = self.launch()                     # systemd respawn
        time.sleep(1.2)
        self.assertIsNone(second.poll())
        self.assertEqual(len(self.box.started()), 1, "paused: no second encoder")
        pause.set_paused(self.box.root, False)
        self.box.wait_for(lambda: len(self.box.started()) == 2)
        second.send_signal(signal.SIGTERM)
        second.communicate(timeout=45)
        self.assertEqual(second.returncode, 0)


if __name__ == "__main__":
    unittest.main()

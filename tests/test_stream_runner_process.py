"""Real-process contracts for the supervisor: locks, stop order, reconnect.

The supervisor runs in its own subprocess (it installs SIGTERM/SIGINT handlers)
against a fake FFmpeg executable. Every child is killed in ``finally``.
"""
import json
import os
from pathlib import Path
import signal
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stream_runner_support import Sandbox, reconnect_policy  # noqa: E402


class SandboxCase(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)

    def wait_running(self, process):
        self.box.wait_for(lambda: self.box.status().get("running") and self.box.status().get("ffmpeg_pid"))
        self.assertIsNone(process.poll())

    def finish(self, process, timeout=20):
        out, err = process.communicate(timeout=timeout)
        return process.returncode, out, err


class LockHoldingTest(SandboxCase):
    def test_both_locks_are_held_while_streaming_and_released_after_exit(self):
        self.box.precreate_locks()
        process = self.box.start(self.box.spec(reconnect=reconnect_policy()))
        self.wait_running(process)
        self.assertEqual(self.box.contend(), {"canonical": "blocked", "legacy": "blocked"})
        process.send_signal(signal.SIGTERM)
        code, _out, err = self.finish(process)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.box.contend(), {"canonical": "acquired", "legacy": "acquired"})

    def test_locks_stay_held_during_the_reconnect_backoff(self):
        self.box.precreate_locks()
        process = self.box.start(
            self.box.spec(reconnect=reconnect_policy(backoff_sec=3, max_consecutive_restarts=5)),
            fake_env={"FAKE_EXIT_AFTER": "0.4", "FAKE_EXIT_CODE": "1"})
        self.box.wait_for(lambda: (self.box.state / "reconnect.json").exists())
        child = self.box.status()["ffmpeg_pid"]
        with self.assertRaises(ProcessLookupError):
            os.kill(child, 0)  # the encoder is gone: this is the backoff window
        self.assertEqual(self.box.contend(), {"canonical": "blocked", "legacy": "blocked"})
        record = json.loads((self.box.state / "reconnect.json").read_text())
        self.assertEqual((record["reason"], record["consecutive_restarts"], record["backoff_sec"]), ("ffmpeg_exit", 1, 3))
        process.send_signal(signal.SIGTERM)
        code, _out, err = self.finish(process)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.box.started()), 1, "SIGTERM during backoff must not start another encoder")
        self.assertEqual(self.box.contend(), {"canonical": "acquired", "legacy": "acquired"})

    def test_a_restart_happens_after_the_backoff_and_locks_are_never_dropped(self):
        self.box.precreate_locks()
        process = self.box.start(
            self.box.spec(reconnect=reconnect_policy(backoff_sec=1, max_consecutive_restarts=5)),
            fake_env={"FAKE_EXIT_AFTER": "0.3", "FAKE_EXIT_CODE": "1"})
        self.box.wait_for(lambda: len(self.box.started()) >= 2)
        self.assertEqual(self.box.contend(), {"canonical": "blocked", "legacy": "blocked"})
        process.send_signal(signal.SIGTERM)
        self.assertEqual(self.finish(process)[0], 0)

    def test_the_reconnect_limit_fails_closed_and_releases_the_locks(self):
        self.box.precreate_locks()
        process = self.box.start(
            self.box.spec(reconnect=reconnect_policy(backoff_sec=1, max_consecutive_restarts=1)),
            fake_env={"FAKE_EXIT_AFTER": "0.2", "FAKE_EXIT_CODE": "1"})
        code, _out, err = self.finish(process, timeout=30)
        self.assertEqual(code, 2)
        self.assertIn("reconnect limit reached (1)", err)
        record = json.loads((self.box.state / "reconnect.json").read_text())
        self.assertEqual((record["consecutive_restarts"], record["backoff_sec"]), (2, 0))
        self.assertEqual(self.box.contend(), {"canonical": "acquired", "legacy": "acquired"})

    def test_the_fake_encoder_does_not_inherit_either_lock_descriptor(self):
        self.box.precreate_locks()
        process = self.box.start(self.box.spec(reconnect=reconnect_policy()))
        self.wait_running(process)
        self.box.wait_for(self.box.fd_report.exists)
        self.assertEqual(json.loads(self.box.fd_report.read_text()), [])
        process.send_signal(signal.SIGTERM)
        self.assertEqual(self.finish(process)[0], 0)


class RefusalTest(SandboxCase):
    def hold(self, path):
        import fcntl
        handle = open(path, "a+")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.addCleanup(handle.close)
        return handle

    def test_a_legacy_runner_holding_only_the_state_dir_lock_blocks_start(self):
        self.box.precreate_locks()
        self.hold(self.box.legacy_lock)  # the legacy runner at flag 0
        process = self.box.start(self.box.spec(reconnect=reconnect_policy()))
        code, _out, err = self.finish(process)
        self.assertEqual(code, 3, err)
        self.assertEqual(self.box.started(), [], "no encoder may start")
        self.assertEqual(self.box.contend()["canonical"], "acquired", "the fixed lease must not stay held after a refusal")

    def test_another_holder_of_the_fixed_lease_blocks_start_and_leaves_the_legacy_lock_free(self):
        self.box.precreate_locks()
        import docich.stream_instance_lock as locks
        locks.CANONICAL_STREAM_ENCODER_LOCK_PATH = str(self.box.canonical)
        try:
            held = locks.open_canonical_stream_encoder_lock()
        finally:
            locks.CANONICAL_STREAM_ENCODER_LOCK_PATH = "/home/ubuntu/docich/run-soren-live/locks/direct_stream_encoder.lock"
        self.addCleanup(held.close)
        process = self.box.start(self.box.spec(reconnect=reconnect_policy()))
        code, _out, err = self.finish(process)
        self.assertEqual(code, 3, err)
        self.assertEqual(self.box.started(), [])
        self.assertEqual(self.box.contend()["legacy"], "acquired")

    def test_an_unprovisioned_fixed_lock_directory_fails_closed(self):
        self.box.canonical_dir.rmdir()
        process = self.box.start(self.box.spec(reconnect=reconnect_policy()))
        code, _out, err = self.finish(process)
        self.assertEqual(code, 2)
        self.assertIn("canonical stream encoder lock is unavailable", err)
        self.assertEqual(self.box.started(), [])

    def test_a_group_writable_lock_directory_is_refused(self):
        os.chmod(self.box.canonical_dir, 0o770)
        process = self.box.start(self.box.spec(reconnect=reconnect_policy()))
        code, _out, _err = self.finish(process)
        self.assertEqual(code, 2)
        self.assertEqual(self.box.started(), [])


class StopOrderTest(SandboxCase):
    def run_and_stop(self, fake_env):
        process = self.box.start(self.box.spec(reconnect=reconnect_policy()), fake_env=fake_env)
        self.wait_running(process)
        process.send_signal(signal.SIGTERM)
        code, _out, err = self.finish(process, timeout=25)
        self.assertEqual(code, 0, err)
        return self.box.status()

    def test_q_on_stdin_is_the_first_and_sufficient_stop(self):
        final = self.run_and_stop({"FAKE_MODE": "normal"})
        self.assertEqual(self.box.event_log(), ["q"])
        self.assertEqual((final["state"], final["exit_code"], final["running"]), ("stopped", 0, False))
        self.assertEqual(final["ffmpeg_exit_code"], 0)

    def test_sigint_follows_when_q_is_ignored(self):
        final = self.run_and_stop({"FAKE_MODE": "ignore_q"})
        self.assertEqual(self.box.event_log(), ["q", "sigint"])
        self.assertEqual((final["state"], final["exit_code"]), ("stopped", 0))
        self.assertEqual(final["ffmpeg_exit_code"], 255)

    def test_sigkill_is_the_last_resort(self):
        final = self.run_and_stop({"FAKE_MODE": "ignore_all"})
        self.assertEqual(self.box.event_log(), ["q", "sigint"])
        self.assertEqual((final["state"], final["exit_code"]), ("stopped", 0))
        self.assertEqual(final["ffmpeg_exit_code"], -signal.SIGKILL)
        child = final["ffmpeg_pid"]
        with self.assertRaises(ProcessLookupError):
            os.kill(child, 0)


class StatusFileTest(SandboxCase):
    def test_status_tracks_the_run_and_never_contains_the_output_url(self):
        process = self.box.start(self.box.spec(reconnect=reconnect_policy()))
        self.wait_running(process)
        self.box.wait_for(lambda: self.box.status().get("frame") == 1)
        status = self.box.status()
        self.assertEqual((status["backend"], status["mode"], status["state"]), ("ffmpeg", "live", "running"))
        self.assertEqual(status["pid"], process.pid)
        self.assertEqual((status["fps"], status["speed"]), (30.0, 1.0))
        self.assertNotIn("rtmp://", json.dumps(status))
        self.assertIn("reconnect", status)
        process.send_signal(signal.SIGTERM)
        self.assertEqual(self.finish(process)[0], 0)

    def test_record_mode_runs_once_and_reports_completion(self):
        process = self.box.start(self.box.spec(mode="record", reconnect=None),
                                 fake_env={"FAKE_EXIT_AFTER": "0.3", "FAKE_EXIT_CODE": "0"})
        code, _out, err = self.finish(process)
        self.assertEqual(code, 0, err)
        final = self.box.status()
        self.assertEqual((final["state"], final["exit_code"], final["mode"]), ("completed", 0, "record"))
        self.assertEqual(len(self.box.started()), 1)

    def test_an_unexpected_exit_without_reconnect_is_reported_as_failed(self):
        process = self.box.start(self.box.spec(mode="record", reconnect=None),
                                 fake_env={"FAKE_EXIT_AFTER": "0.2", "FAKE_EXIT_CODE": "7"})
        code, _out, _err = self.finish(process)
        self.assertEqual(code, 7)
        self.assertEqual(self.box.status()["state"], "failed")


if __name__ == "__main__":
    unittest.main()

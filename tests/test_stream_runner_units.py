"""Monitor threads, graceful-stop escalation and stop ownership (no real encoder)."""
import ast
import io
from pathlib import Path
import signal
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import docich.stream_runner as run  # noqa: E402
from docich.stream_config import load_stream_config  # noqa: E402
from docich.stream_host_checks import RuntimeCheckError  # noqa: E402

SOREN = ROOT / "games" / "soviet_now" / "lib" / "direct_stream.py"


class BrokenStdin(io.StringIO):
    def write(self, text):
        raise BrokenPipeError()


class FakeChild:
    pid = 4242

    def __init__(self, exits_after=None, stdin=None):
        self.stdin = stdin if stdin is not None else io.StringIO()
        self.signals = []
        self.killed = False
        self.exits_after = exits_after  # stage name after which poll() reports exit

    def poll(self):
        stage = "killed" if self.killed else ("sigint" if signal.SIGINT in self.signals else "q" if "q" in self.stdin.getvalue() else "start")
        return 0 if self.exits_after == stage else None

    def send_signal(self, sig):
        self.signals.append(sig)

    def kill(self):
        self.killed = True


class GracefulStopTest(unittest.TestCase):
    def stop(self, exits_after):
        child = FakeChild(exits_after)
        waits = []
        def wait(c, timeout):
            waits.append(timeout)
            return c.poll() is not None
        run.graceful_stop_ffmpeg(child, q_timeout_sec=1.5, sigint_timeout_sec=2.5, wait=wait).join(timeout=5)
        return child, waits

    def test_q_only(self):
        child, waits = self.stop("q")
        self.assertEqual((child.stdin.getvalue(), child.signals, child.killed, waits), ("q\n", [], False, [1.5]))

    def test_then_sigint(self):
        child, waits = self.stop("sigint")
        self.assertEqual((child.signals, child.killed, waits), ([signal.SIGINT], False, [1.5, 2.5]))

    def test_then_kill(self):
        child, waits = self.stop("killed")
        self.assertEqual((child.signals, child.killed, waits), ([signal.SIGINT], True, [1.5, 2.5]))

    def test_a_broken_stdin_still_escalates(self):
        child = FakeChild("sigint", stdin=BrokenStdin())
        run.graceful_stop_ffmpeg(child, wait=lambda c, t: c.poll() is not None).join(timeout=5)
        self.assertEqual(child.signals, [signal.SIGINT])


class MonitorTest(unittest.TestCase):
    def reconnect(self, **o):
        base = {"probe_interval_sec": 1, "offline_threshold": 2, "twitch_channel": "chan"}
        base.update(o)
        return base

    def run_reconnect(self, observations, alive_for=100, **policy):
        child, state, logs = FakeChild(), {"stopping": False}, []
        feed = iter(observations)
        alive_checks = {"n": 0}
        def alive(pid):
            alive_checks["n"] += 1
            return alive_checks["n"] <= alive_for
        def probe(channel):
            self.assertEqual(channel, "chan")
            return next(feed)
        run.start_reconnect_monitor(self.reconnect(**policy), child, state, probe=probe,
                                    sleep=lambda s: None, alive=alive, log=logs.append).join(timeout=5)
        return child, state, logs

    def test_confirmed_offline_streak_requests_a_reconnect_with_sigint(self):
        child, state, logs = self.run_reconnect([False, False])
        self.assertEqual(child.signals, [signal.SIGINT])
        self.assertEqual((state["reconnect"], state["reconnect_reason"], state["offline_streak"]), (True, "twitch_offline", 2))
        self.assertEqual(logs, ["direct_stream: Twitch offline streak=1/2", "direct_stream: Twitch offline streak=2/2"])

    def test_probe_failures_and_recovery_never_restart(self):
        child, state, _logs = self.run_reconnect([False, None, True, False, None, True], alive_for=6)
        self.assertEqual(child.signals, [])
        self.assertFalse(state.get("reconnect"))
        self.assertEqual(state["offline_streak"], 0)
        self.assertIs(state["last_live"], True)

    def test_the_monitor_stops_when_stopping_or_the_child_died(self):
        child, state = FakeChild(), {"stopping": True}
        run.start_reconnect_monitor(self.reconnect(), child, state, probe=lambda c: self.fail("probed"),
                                    sleep=lambda s: None).join(timeout=5)
        self.assertEqual(child.signals, [])
        child2, state2, _ = self.run_reconnect([], alive_for=0)
        self.assertEqual(child2.signals, [])

    def run_audio(self, states, threshold=2, alive_for=100):
        child, state, logs = FakeChild(), {"stopping": False}, []
        feed = iter(states)
        checks = {"n": 0}
        def alive(pid):
            checks["n"] += 1
            return checks["n"] <= alive_for
        config = {"pulse_source": "soren_null.monitor"}
        watchdog = {"probe_interval_sec": 1, "loss_threshold": threshold, "pulse_server": "unix:/run/user/1/pulse/native"}
        run.start_audio_capture_monitor(config, watchdog, child, state, probe=lambda server, pid: next(feed),
                                        sleep=lambda s: None, alive=alive, log=logs.append).join(timeout=5)
        return child, state, logs

    def test_capture_loss_after_a_seen_capture_restarts_at_the_threshold(self):
        child, state, logs = self.run_audio(["ok", "missing", "missing"])
        self.assertEqual(child.signals, [signal.SIGINT])
        self.assertEqual((state["reconnect_reason"], state["audio_capture"]["status"],
                          state["audio_capture"]["consecutive_unhealthy"]), ("pulse_capture_lost", "missing", 2))
        self.assertEqual(state["audio_capture"]["source"], "soren_null.monitor")
        self.assertIn("pulse capture lost (2 checks)", logs[0])

    def test_unreachable_unknown_or_never_seen_capture_never_restarts(self):
        for states in (["missing"] * 6, ["ok", "unreachable", "unknown", "missing"] + ["unknown"] * 2,
                       ["unreachable"] * 6):
            with self.subTest(states=states):
                child, state, _ = self.run_audio(states, alive_for=len(states))
                self.assertEqual(child.signals, [])
                self.assertFalse(state.get("reconnect"))


def config_for(state_dir):
    cfg = load_stream_config({"SOREN_DIRECT_STREAM_STATE_DIR": str(state_dir)}, repo_root=Path("/r"), runtime_uid=1)
    return cfg


class StopOwnershipTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="stream-stop-")
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        self.config = config_for(self.state)

    def write_status(self, pid=777):
        import json
        (self.state / "status.json").write_text(json.dumps({"running": True, "pid": pid}))

    def stop(self, cmdline, alive_seq=(True, False), **kw):
        sent = []
        seq = list(alive_seq)
        def alive(pid):
            return seq.pop(0) if len(seq) > 1 else seq[0]
        result = run.stop_runner(
            self.config, read_cmdline=lambda pid: cmdline if not isinstance(cmdline, Exception) else (_ for _ in ()).throw(cmdline),
            kill=lambda pid, sig: sent.append((pid, sig)), alive=alive, sleep=lambda s: None, **kw)
        return result, sent

    def test_owned_entry_points_are_signalled(self):
        self.write_status()
        for raw in (b"python3\x00-m\x00docich.stream_supervisor\x00run\x00",
                    b"/usr/bin/python3\x00-m\x00docich.stream_supervisor\x00--root\x00/srv/soren\x00run\x00"):
            with self.subTest(raw=raw):
                result, sent = self.stop(raw, alive_seq=(True, True, False))
                self.assertEqual((result, sent), (0, [(777, signal.SIGTERM)]))

    def test_the_legacy_runner_sharing_the_state_dir_is_refused(self):
        self.write_status()
        for raw in (b"python3\x00/home/ubuntu/soren/lib/direct_stream.py\x00run\x00",
                    b"python3\x00lib/direct_stream.py\x00run\x00",
                    b"python3\x00-m\x00docich.stream_supervisor\x00status\x00",
                    b"python3\x00-m\x00docich.stream_supervisor\x00record\x00",
                    b"sleep\x0060\x00", b""):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(RuntimeCheckError, "not owned by docich.stream_supervisor"):
                    self.stop(raw, alive_seq=(True,))

    def test_nothing_is_signalled_when_not_running_or_unreadable(self):
        self.assertEqual(run.stop_runner(self.config, kill=lambda *a: self.fail("kill")), 0)  # no status file
        self.write_status()
        self.assertEqual(self.stop(OSError("gone"), alive_seq=(True,))[0], 0)
        self.assertEqual(self.stop(b"x", alive_seq=(False,))[0], 0)

    def test_a_runner_that_ignores_sigterm_is_reported(self):
        self.write_status()
        clock = iter(range(0, 1000))
        with self.assertRaisesRegex(RuntimeCheckError, "did not stop within 3 seconds"):
            run.stop_runner(self.config, read_cmdline=lambda pid: b"python3\x00-m\x00docich.stream_supervisor\x00run\x00",
                            kill=lambda pid, sig: None, alive=lambda pid: True, sleep=lambda s: None,
                            monotonic=lambda: float(next(clock)), timeout_sec=3.0)


class PathParityTest(unittest.TestCase):
    def test_the_default_legacy_lock_is_the_path_the_legacy_runner_uses(self):
        cfg = load_stream_config({}, repo_root=Path("/srv/soren"), runtime_uid=1001)
        self.assertEqual(Path(str(cfg["state_dir"])) / run.LEGACY_LOCK_FILE,
                         Path("/srv/soren/tmp/state/direct_stream/direct_stream.lock"))

    @unittest.skipUnless(SOREN.is_file(), "soviet_now submodule is not checked out (CI uses submodules: false)")
    def test_the_legacy_runner_source_names_the_same_lock_file_and_status_file(self):
        source = SOREN.read_text()
        self.assertIn('lock_path = config.state_dir / "direct_stream.lock"', source)
        self.assertIn('"tmp/state/direct_stream"', source)
        self.assertEqual(run.LEGACY_LOCK_FILE, "direct_stream.lock")


class PurityTest(unittest.TestCase):
    def test_no_shell_or_unbounded_kill(self):
        tree = ast.parse((ROOT / "src/docich/stream_runner.py").read_text())
        keywords = {k.arg for n in ast.walk(tree) if isinstance(n, ast.keyword) for k in [n]}
        self.assertNotIn("shell", keywords)
        self.assertIn("start_new_session", keywords)
        killers = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr in {"killpg", "system"}]
        self.assertEqual(killers, [])


if __name__ == "__main__":
    unittest.main()

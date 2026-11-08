"""TwiCa substitution and the run/stop actions of the supervisor entry point."""
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
import docich.stream_supervisor as sup  # noqa: E402

HAS_PROC = Path("/proc/self/cmdline").exists()


class PrepareEnvironmentTest(unittest.TestCase):
    def prepare(self, env, enabled=True, executable=True):
        return sup.prepare_environment(env, docich_root=Path("/opt/docich"), pipeline_enabled=lambda: enabled,
                                       is_executable=lambda path: executable and str(path) == "/opt/docich/bin/docich-twica-ffmpeg")

    def test_armed_pipeline_swaps_in_the_wrapper_and_remembers_the_real_ffmpeg(self):
        got = self.prepare({"SOREN_DIRECT_STREAM_FFMPEG_BIN": "/usr/local/bin/ffmpeg", "OTHER": "1"})
        self.assertEqual(got["DOCICH_TWICA_REAL_FFMPEG"], "/usr/local/bin/ffmpeg")
        self.assertEqual(got["SOREN_DIRECT_STREAM_FFMPEG_BIN"], "/opt/docich/bin/docich-twica-ffmpeg")
        self.assertEqual(got["OTHER"], "1")

    def test_unset_or_empty_ffmpeg_means_plain_ffmpeg(self):
        for env in ({}, {"SOREN_DIRECT_STREAM_FFMPEG_BIN": ""}):
            self.assertEqual(self.prepare(env)["DOCICH_TWICA_REAL_FFMPEG"], "ffmpeg")

    def test_nothing_changes_when_disarmed_or_the_wrapper_is_missing(self):
        env = {"SOREN_DIRECT_STREAM_FFMPEG_BIN": "ff"}
        self.assertEqual(self.prepare(env, enabled=False), env)
        self.assertEqual(self.prepare(env, executable=False), env)

    def test_the_input_mapping_is_not_mutated(self):
        env = {"SOREN_DIRECT_STREAM_FFMPEG_BIN": "ff"}
        self.prepare(env)
        self.assertEqual(env, {"SOREN_DIRECT_STREAM_FFMPEG_BIN": "ff"})

    def test_the_armed_state_is_not_even_consulted_without_the_wrapper(self):
        called = []
        sup.prepare_environment({}, docich_root=Path("/opt/docich"), is_executable=lambda p: False,
                                pipeline_enabled=lambda: called.append(1) or True)
        self.assertEqual(called, [])

    def test_an_unreadable_twica_state_means_disarmed(self):
        from unittest import mock
        with mock.patch("docich.twica_operator.main", side_effect=RuntimeError("corrupt")):
            self.assertFalse(sup.twica_pipeline_enabled())
        with mock.patch("docich.twica_operator.main", return_value=1):
            self.assertFalse(sup.twica_pipeline_enabled())
        with mock.patch("docich.twica_operator.main", return_value=0):
            self.assertTrue(sup.twica_pipeline_enabled())


class TwicaVisibleInCommandTest(unittest.TestCase):
    def run_command(self, enabled):
        with tempfile.TemporaryDirectory(prefix="stream-supervisor-") as tmp:
            bin_dir = Path(tmp) / "docich" / "bin"
            bin_dir.mkdir(parents=True)
            wrapper = bin_dir / "docich-twica-ffmpeg"
            wrapper.write_text("#!/bin/sh\n")
            wrapper.chmod(0o755)
            spec = {"src": str(ROOT / "src"), "canonical": "/nonexistent/x", "argv": ["--root", tmp, "command"],
                    "env": {"DOCICH_ROOT": str(Path(tmp) / "docich"), "DISPLAY": ":99", "XDG_RUNTIME_DIR": "/run/user/1",
                            "SOREN_DIRECT_STREAM_FFMPEG_BIN": "real-ffmpeg"}, "twica": enabled}
            proc = subprocess.run([sys.executable, "-B", "-c", MAIN_DRIVER, json.dumps(spec)], capture_output=True,
                                  text=True, timeout=20, check=False, cwd="/")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            return json.loads(proc.stdout)[0], str(wrapper)

    def test_command_uses_the_wrapper_only_when_armed(self):
        argv0, wrapper = self.run_command(True)
        self.assertEqual(argv0, wrapper)
        self.assertEqual(self.run_command(False)[0], "real-ffmpeg")


class RunThroughMainTest(unittest.TestCase):
    def setUp(self):
        self.box = Sandbox()
        self.addCleanup(self.box.cleanup)

    def spec(self, *argv, **env):
        base = {"PATH": os.environ.get("PATH", ""), "SOREN_DIRECT_STREAM_STATE_DIR": str(self.box.state),
                "SOREN_DIRECT_STREAM_LOG_FILE": str(self.box.logs / "s.log"), "SOREN_DIRECT_STREAM_FFMPEG_BIN": str(self.box.ffmpeg),
                "SOREN_DIRECT_STREAM_AUDIO_WATCHDOG_ENABLED": "0", "SOREN_DIRECT_STREAM_RECONNECT_ENABLED": "0",
                "DOCICH_STREAM_OWNER": "docich", **env}
        base.update(self.box.env())
        return {"src": str(ROOT / "src"), "canonical": str(self.box.canonical), "argv": ["--root", str(self.box.root), *argv], "env": base}

    def launch(self, spec, tail=()):
        process = subprocess.Popen([sys.executable, "-B", "-c", MAIN_DRIVER, json.dumps(spec), *tail],
                                   cwd=str(self.box.root), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   start_new_session=True, env={**os.environ, **self.box.env()})
        self.box.processes.append(process)
        return process

    def test_run_streams_holds_the_locks_and_stops_cleanly_on_sigterm(self):
        self.box.precreate_locks()
        process = self.launch(self.spec("run"))
        self.box.wait_for(lambda: self.box.status().get("running") and self.box.status().get("ffmpeg_pid"))
        self.assertEqual(self.box.contend(), {"canonical": "blocked", "legacy": "blocked"})
        process.send_signal(signal.SIGTERM)
        out, err = process.communicate(timeout=45)
        self.assertEqual(process.returncode, 0, err)
        self.assertEqual(self.box.status()["state"], "stopped")
        self.assertEqual(self.box.contend(), {"canonical": "acquired", "legacy": "acquired"})

    def test_record_runs_once_to_completion(self):
        out_dir = self.box.root / "rec"
        process = self.launch(self.spec("record", "--output", str(out_dir / "o.mkv"), "--duration", "5"))
        # the fake exits only on stdin 'q'/EOF/limits; end it via SIGTERM after it started
        self.box.wait_for(lambda: self.box.status().get("running") and self.box.status().get("ffmpeg_pid"))
        process.send_signal(signal.SIGTERM)
        _out, err = process.communicate(timeout=45)
        self.assertEqual(process.returncode, 0, err)
        self.assertEqual(self.box.status()["mode"], "record")
        self.assertTrue(out_dir.is_dir(), "the output directory is created before the encoder starts")

    def test_a_second_run_is_refused_while_the_first_holds_the_locks(self):
        self.box.precreate_locks()
        first = self.launch(self.spec("run"))
        self.box.wait_for(lambda: self.box.status().get("running") and self.box.status().get("ffmpeg_pid"))
        self.box.wait_for(lambda: len(self.box.started()) == 1)
        second = self.launch(self.spec("run"))
        _out, err = second.communicate(timeout=30)
        self.assertEqual(second.returncode, 2)
        self.assertIn("already running", err)
        self.assertEqual(len(self.box.started()), 1)
        first.send_signal(signal.SIGTERM)
        first.communicate(timeout=45)
        self.assertEqual(first.returncode, 0)

    @unittest.skipUnless(HAS_PROC, "stop ownership reads /proc/<pid>/cmdline (Linux)")
    def test_stop_ends_its_own_runner_and_refuses_a_foreign_one(self):
        self.box.precreate_locks()
        runner = self.launch(self.spec("run"), tail=("docich.stream_supervisor", "run"))
        self.box.wait_for(lambda: self.box.status().get("running") and self.box.status().get("ffmpeg_pid"))
        stopped = self.launch(self.spec("stop"))
        # The runner is this test's child: reap it while `stop` waits, otherwise
        # the exited process stays a zombie and still answers kill(pid, 0).
        deadline = time.monotonic() + 60
        while stopped.poll() is None and time.monotonic() < deadline:
            runner.poll()
            time.sleep(0.05)
        _out, err = stopped.communicate(timeout=5)
        self.assertEqual(stopped.returncode, 0, err)
        runner.communicate(timeout=45)
        self.assertEqual(runner.returncode, 0)
        self.assertEqual(self.box.status()["state"], "stopped")

    @unittest.skipUnless(HAS_PROC, "stop ownership reads /proc/<pid>/cmdline (Linux)")
    def test_stop_never_signals_a_legacy_runner_sharing_the_state_dir(self):
        self.box.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        legacy = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(40)", "/home/ubuntu/soren/lib/direct_stream.py", "run"],
                                  start_new_session=True)
        self.addCleanup(lambda: (legacy.kill(), legacy.wait()))
        (self.box.state / "status.json").write_text(json.dumps({"running": True, "pid": legacy.pid}))
        stopped = self.launch(self.spec("stop"))
        _out, err = stopped.communicate(timeout=30)
        self.assertEqual(stopped.returncode, 2)
        self.assertIn("not owned by docich.stream_supervisor", err)
        self.assertIsNone(legacy.poll(), "the legacy runner must not receive SIGTERM")


if __name__ == "__main__":
    unittest.main()


class OwnerFlagTest(unittest.TestCase):
    def run_main(self, env, *argv):
        with tempfile.TemporaryDirectory(prefix="stream-owner-") as tmp:
            spec = {"src": str(ROOT / "src"), "canonical": "/nonexistent/x", "argv": ["--root", tmp, *argv],
                    "env": {"DISPLAY": ":99", **env}}
            proc = subprocess.run([sys.executable, "-B", "-c", MAIN_DRIVER, json.dumps(spec)], capture_output=True,
                                  text=True, timeout=20, check=False, cwd="/")
            self.assertEqual(list(Path(tmp).iterdir()), [], "a refused or skipped start must create nothing")
            return proc

    def test_the_default_owner_is_the_legacy_runner_so_run_does_nothing(self):
        for env in ({}, {"DOCICH_STREAM_OWNER": "soren"}, {"DOCICH_STREAM_OWNER": ""},
                    {"SOREN_DIRECT_STREAM_FPS": "999"}):  # even an invalid config is not even loaded
            with self.subTest(env=env):
                proc = self.run_main(env, "run")
                self.assertEqual(proc.returncode, sup.NOT_OWNER_EXIT, proc.stderr)
                self.assertIn("legacy runner owns the encoder", proc.stderr)
                self.assertEqual(proc.stdout, "")

    def test_an_invalid_owner_value_is_a_failure_not_a_silent_skip(self):
        for bad in ("Docich", "both", "1", "docich,soren", " docich", "soren ", "  "):
            with self.subTest(bad=bad):
                proc = self.run_main({"DOCICH_STREAM_OWNER": bad}, "run")
                self.assertEqual(proc.returncode, 2)
                self.assertIn("must be soren or docich", proc.stderr)

    def test_the_owner_flag_does_not_gate_the_read_only_actions(self):
        proc = self.run_main({}, "config")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('"backend": "ffmpeg"', proc.stdout)


class PulseBeforeValidationTest(unittest.TestCase):
    def test_a_pulse_failure_stops_the_start_before_host_validation_and_locks(self):
        order = []
        def pulse(env, config, watchdog):
            order.append("pulse")
            from docich.stream_prereqs import PrerequisiteError
            raise PrerequisiteError("PulseAudio did not become ready before the runtime deadline")
        def validate(config, mode):
            order.append("validate")
            return False
        with tempfile.TemporaryDirectory(prefix="stream-pulse-") as tmp:
            code = sup.main(["--root", tmp, "run"], environ={"DISPLAY": ":99", "DOCICH_STREAM_OWNER": "docich"},
                            validate=validate, prepare_pulse=pulse, twica_enabled=lambda: False,
                            canonical_opener=lambda: self.fail("the lock must not be taken"))
            self.assertEqual(list(Path(tmp).iterdir()), [])
        self.assertEqual((code, order), (2, ["pulse"]))

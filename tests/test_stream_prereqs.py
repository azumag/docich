"""Pulse readiness: wait, create the sink only if missing, never start Pulse."""
import ast
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import docich.stream_prereqs as pre  # noqa: E402


class Pactl:
    def __init__(self, info_ok_after=0, sinks="", load_rc=0, list_rc=0, raise_on=None):
        self.calls, self.info_calls = [], 0
        self.info_ok_after, self.sinks, self.load_rc, self.list_rc, self.raise_on = info_ok_after, sinks, load_rc, list_rc, raise_on

    def __call__(self, command, *, env, **kw):
        self.calls.append((tuple(command), env.get("PULSE_SERVER")))
        if self.raise_on == command[1]:
            raise OSError("no pactl")
        if command[1] == "info":
            self.info_calls += 1
            return subprocess.CompletedProcess(command, 0 if self.info_calls > self.info_ok_after else 1, "", "")
        if command[1] == "list":
            return subprocess.CompletedProcess(command, self.list_rc, self.sinks, "")
        return subprocess.CompletedProcess(command, self.load_rc, "7\n", "")


class Clock:
    def __init__(self):
        self.now = 0.0
        self.slept = []

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds

    def monotonic(self):
        return self.now


def ensure(pactl, source="soren_null.monitor", wait=90, clock=None):
    clock = clock or Clock()
    pre.ensure_pulse_ready(pulse_server="unix:/run/user/1/pulse/native", pulse_source=source, wait_sec=wait,
                           base_env={"PATH": "/bin"}, run=pactl, sleep=clock.sleep, monotonic=clock.monotonic)
    return clock


class EnsureTest(unittest.TestCase):
    def test_present_sink_needs_no_change(self):
        pactl = Pactl(sinks="0\tsoren_null\tmodule-null-sink.c\ts16le 2ch 44100Hz\tIDLE\n")
        ensure(pactl)
        self.assertEqual([c[0][1] for c in pactl.calls], ["info", "list"])
        self.assertTrue(all(server == "unix:/run/user/1/pulse/native" for _c, server in pactl.calls))

    def test_missing_sink_is_created_with_the_legacy_arguments(self):
        pactl = Pactl(sinks="0\tother\tm\n")
        ensure(pactl)
        self.assertEqual(pactl.calls[-1][0], ("pactl", "load-module", "module-null-sink", "sink_name=soren_null",
                                              "sink_properties=device.description=soren_null"))

    def test_it_waits_for_a_late_server_without_starting_one(self):
        pactl = Pactl(info_ok_after=3, sinks="0\tsoren_null\n")
        clock = ensure(pactl)
        self.assertEqual(clock.slept, [1, 1, 1])
        for command, _server in pactl.calls:
            self.assertEqual(command[0], "pactl")
            self.assertNotIn("systemctl", command)

    def test_an_unreachable_server_fails_at_the_deadline(self):
        clock = Clock()
        with self.assertRaisesRegex(pre.PrerequisiteError, "did not become ready"):
            ensure(Pactl(info_ok_after=10 ** 6), wait=5, clock=clock)
        self.assertEqual(sum(clock.slept), 5)

    def test_other_failures_are_prerequisite_errors(self):
        for label, pactl, text in (
            ("no pactl", Pactl(raise_on="info"), "pactl could not be run"),
            ("list fails", Pactl(list_rc=1), "could not be listed"),
            ("create fails", Pactl(sinks="", load_rc=1), "could not be created: soren_null"),
        ):
            with self.subTest(label), self.assertRaisesRegex(pre.PrerequisiteError, text):
                ensure(pactl)

    def test_a_source_without_a_monitor_suffix_only_checks_reachability(self):
        pactl = Pactl()
        ensure(pactl, source="alsa_input.usb-mic")
        self.assertEqual([c[0][1] for c in pactl.calls], ["info"])


class HelpersTest(unittest.TestCase):
    def test_sink_name_derivation(self):
        self.assertEqual(pre.sink_for_source("soren_null.monitor"), "soren_null")
        self.assertIsNone(pre.sink_for_source("soren_null"))
        self.assertIsNone(pre.sink_for_source("bad name.monitor"))
        self.assertIsNone(pre.sink_for_source("a;b.monitor"))

    def test_wait_seconds_bounds(self):
        self.assertEqual(pre.wait_seconds({}), 90)
        self.assertEqual(pre.wait_seconds({"SOREN_RUNTIME_PREREQ_WAIT_SEC": " 5 "}), 5)
        for bad in ("4", "301", "x", "-1", "1.5", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                pre.wait_seconds({"SOREN_RUNTIME_PREREQ_WAIT_SEC": bad})


class PurityTest(unittest.TestCase):
    def test_pactl_only_and_no_default_changes(self):
        source = (ROOT / "src/docich/stream_prereqs.py").read_text()
        tree = ast.parse(source)
        literals = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        self.assertNotIn("set-default-sink", literals)
        self.assertNotIn("set-default-source", literals)
        self.assertNotIn("systemctl", literals)
        self.assertFalse({"pulseaudio", "start"} & literals)
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        self.assertFalse(attrs & {"kill", "Popen", "system", "environ"})


if __name__ == "__main__":
    unittest.main()

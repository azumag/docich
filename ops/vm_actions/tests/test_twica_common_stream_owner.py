"""arm_stream restarts the encoder through whichever owner is live (ADR 0007 D2)."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
_spec = importlib.util.spec_from_file_location("twica_common_tested", ROOT / "ops/vm_actions/twica_common.py")
common = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(common)
NotReady = common.NotReady


def shown(**values):
    def show(unit, names, **kw):
        assert unit == "docich-stream.service"
        return {name: values.get(name) for name in names}
    return show


class SupervisionTest(unittest.TestCase):
    def test_an_active_auto_restarting_unit_whose_main_pid_is_the_runner_passes(self):
        common.verify_docich_supervision(4242, show=shown(ActiveState="active", MainPID="4242", Restart="always"))

    def test_anything_else_refuses_before_the_encoder_is_interrupted(self):
        for props in (dict(ActiveState="inactive", MainPID="4242", Restart="always"),
                      dict(ActiveState="active", MainPID="4243", Restart="always"),
                      dict(ActiveState="active", MainPID="0", Restart="always"),
                      dict(ActiveState="active", MainPID="4242", Restart="on-failure"),
                      dict(ActiveState="active", MainPID="4242", Restart="no"),
                      dict(ActiveState=None, MainPID=None, Restart=None)):
            with self.subTest(props=props), self.assertRaises(NotReady):
                common.verify_docich_supervision(4242, show=shown(**props))

    def test_systemctl_show_failures_are_unverified_and_never_use_sudo(self):
        calls = []
        def run(command, **kw):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, "ActiveState=active\nMainPID=7\nRestart=always\n", "")
        props = common._unit_properties("docich-stream.service", ("ActiveState", "MainPID", "Restart"), run=run)
        self.assertEqual(props, {"ActiveState": "active", "MainPID": "7", "Restart": "always"})
        self.assertEqual(calls[0][:3], ["systemctl", "show", "docich-stream.service"])
        self.assertNotIn("sudo", calls[0])
        for failure in (OSError("no systemctl"), subprocess.TimeoutExpired("systemctl", 15)):
            with self.subTest(failure=failure), self.assertRaises(NotReady):
                common._unit_properties("u", ("ActiveState",), run=mock.Mock(side_effect=failure))
        with self.assertRaises(NotReady):
            common._unit_properties("u", ("ActiveState",), run=lambda c, **k: subprocess.CompletedProcess(c, 1, "", ""))


class ArmStreamBranchTest(unittest.TestCase):
    """Drive arm_stream far enough to see which owner path and stop command it picks."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="arm-stream-")
        self.addCleanup(self.tmp.cleanup)
        self.soren = Path(self.tmp.name)
        (self.soren / "tmp/state/direct_stream").mkdir(parents=True)
        (self.soren / "tmp/state/direct_stream/status.json").write_text(json.dumps({"pid": 4242}))
        self.stops = []
        patches = [
            mock.patch.object(common, "SOREN_ROOT", self.soren),
            mock.patch.object(common, "state_directory", return_value=self.soren / "twica"),
            mock.patch.object(common, "status", side_effect=[{"pipeline_ready": False}, {"pipeline_ready": True}]),
            mock.patch.object(common, "control", return_value={"pipeline_enabled": True}),
            mock.patch.object(common, "read_json", return_value={"ready": True}),
            mock.patch.object(common, "fresh", return_value=True),
            mock.patch.object(common, "legacy_clients", return_value=[{"role": "game"}, {"role": "shared"}]),
            mock.patch.object(common, "checked", side_effect=lambda argv, **kw: self.stops.append((argv, kw))),
            mock.patch.object(common.time, "sleep", lambda s: None),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_docich_runner_is_restarted_through_its_own_stop_after_the_unit_check(self):
        with mock.patch.object(common, "_docich_runner", return_value=True), \
                mock.patch.object(common, "verify_docich_supervision") as verify:
            common.arm_stream(True)
        verify.assert_called_once_with(4242)
        argv, kw = self.stops[0]
        self.assertEqual(argv[1:], ["-m", "docich.stream_supervisor", "--root", str(self.soren), "stop"])
        self.assertTrue(kw["env"]["PYTHONPATH"].split(os.pathsep)[0].endswith("/src"))
        self.assertFalse(any("direct_stream.sh" in part for part in argv))

    def test_an_unverified_docich_unit_means_the_encoder_is_never_touched(self):
        with mock.patch.object(common, "_docich_runner", return_value=True), \
                mock.patch.object(common, "verify_docich_supervision", side_effect=NotReady("stream_supervisor_unverified")):
            with self.assertRaises(NotReady):
                common.arm_stream(True)
        self.assertEqual(self.stops, [])

    def test_a_legacy_runner_still_needs_the_start_all_supervisor_and_uses_the_legacy_stop(self):
        with mock.patch.object(common, "_docich_runner", return_value=False):
            with self.assertRaises(NotReady) as raised:          # no start_all.pid -> unverified, as before
                common.arm_stream(True)
        self.assertEqual(str(raised.exception), "stream_supervisor_unverified")
        self.assertEqual(self.stops, [])

    def test_either_pause_marker_or_the_stop_marker_refuses_first(self):
        for marker in ("tmp/stop", "tmp/state/direct_stream.paused", "tmp/state/docich_stream.paused"):
            with self.subTest(marker=marker):
                path = self.soren / marker
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("1")
                with mock.patch.object(common, "status", side_effect=[{"pipeline_ready": False}] * 2), \
                        self.assertRaises(NotReady) as raised:
                    common.arm_stream(True)
                self.assertEqual(str(raised.exception), "stream_intentionally_stopped")
                path.unlink()


class DocichRunnerProbeTest(unittest.TestCase):
    def test_the_runner_must_be_this_users_docich_supervisor_in_the_soren_tree(self):
        with tempfile.TemporaryDirectory(prefix="docich-runner-") as tmp:
            soren = Path(tmp).resolve()
            argv = [sys.executable, "-c", "import time; time.sleep(30)"]
            child = subprocess.Popen(argv, cwd=soren)
            self.addCleanup(lambda: (child.kill(), child.wait()))
            with mock.patch.object(common, "SOREN_ROOT", soren):
                self.assertFalse(common._docich_runner(child.pid), "an unrelated process is not the runner")
                self.assertFalse(common._docich_runner(None))
                self.assertFalse(common._docich_runner(1))
                self.assertFalse(common._docich_runner(True))


if __name__ == "__main__":
    unittest.main()

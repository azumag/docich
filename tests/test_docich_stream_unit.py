"""Static contracts of the placed (not installed) docich-stream.service and its wrapper."""
import configparser
import inspect
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import docich.stream_runner as runner  # noqa: E402
import docich.stream_supervisor as supervisor  # noqa: E402

UNIT = ROOT / "deploy/docich-stream/docich-stream.service"
WRAPPER = ROOT / "bin/docich-stream-run"


def load_unit():
    parser = configparser.ConfigParser(strict=False, interpolation=None, delimiters=("=",), allow_no_value=True)
    parser.optionxform = str
    parser.read_string(UNIT.read_text())
    return parser


class UnitTest(unittest.TestCase):
    def setUp(self):
        self.unit = load_unit()
        self.service = self.unit["Service"]
        self.text = UNIT.read_text()

    def test_the_stop_budget_covers_the_graceful_stop_with_margin(self):
        graceful = inspect.signature(runner.graceful_stop_ffmpeg).parameters
        once = inspect.signature(runner.run_ffmpeg_once).parameters["stop_timeouts"].default
        budget = graceful["q_timeout_sec"].default + graceful["sigint_timeout_sec"].default
        self.assertEqual(tuple(once), (graceful["q_timeout_sec"].default, graceful["sigint_timeout_sec"].default))
        timeout = int(self.service["TimeoutStopSec"].rstrip("s"))
        self.assertGreaterEqual(timeout, budget + 10, "systemd must not SIGKILL during the graceful q/SIGINT window")

    def test_kill_semantics_protect_against_orphans_and_hard_kills(self):
        self.assertEqual(self.service["KillMode"], "mixed")
        self.assertEqual(self.service["KillSignal"], "SIGTERM")
        # Restart=always makes the sudo-free operator stop/start cycle work; the
        # "not the owner" exit status must be neither a failure nor restarted.
        self.assertEqual(self.service["Restart"], "always")
        self.assertEqual(self.service["SuccessExitStatus"], str(supervisor.NOT_OWNER_EXIT))
        self.assertEqual(self.service["RestartPreventExitStatus"], str(supervisor.NOT_OWNER_EXIT))
        self.assertEqual(supervisor.NOT_OWNER_EXIT, 75)
        self.assertEqual(self.service["Type"], "simple")

    def test_it_runs_the_wrapper_as_the_runtime_user(self):
        self.assertEqual((self.service["User"], self.service["Group"]), ("ubuntu", "ubuntu"))
        exec_start = self.service["ExecStart"]
        self.assertEqual(exec_start, "/home/ubuntu/docich/bin/docich-stream-run")
        self.assertTrue(WRAPPER.is_file() and os.access(WRAPPER, os.X_OK))
        self.assertEqual(self.service["UMask"], "0027")

    def test_it_neither_owns_pulseaudio_nor_the_game_runtime(self):
        for forbidden in ("EnvironmentFile", "ExecStartPre", "ExecStartPost", "ExecStop", "systemctl",
                          "pulseaudio.service", "pulseaudio.socket", "soren-runtime.service", "Conflicts=",
                          "BindsTo", "PartOf", "OBS", "obs.service"):
            self.assertNotIn(forbidden, self.text, forbidden)
        self.assertIn("PULSE_SERVER=unix:/run/user/1001/pulse/native", self.text)
        self.assertIn("XDG_RUNTIME_DIR=/run/user/1001", self.text)

    def test_ordering_matches_the_legacy_runtime_prerequisites(self):
        unit = self.unit["Unit"]
        self.assertIn("xvfb.service", unit["Requires"])
        for dependency in ("user@1001.service", "xvfb.service", "soren-rtmp-relay.service"):
            self.assertIn(dependency, unit["After"])
        self.assertNotIn("soren-runtime.service", unit["After"] + unit["Wants"] + unit["Requires"])

    def test_it_carries_no_credentials_or_stream_destinations(self):
        for pattern in (r"rtmps?://", r"(?i)stream[_-]?key", r"(?i)token", r"(?i)secret", r"(?i)password"):
            self.assertIsNone(re.search(pattern, self.text), pattern)

    def test_installing_is_not_part_of_this_repository_change(self):
        readme = (ROOT / "deploy/docich-stream/README.md").read_text()
        self.assertIn("placed, not installed", readme)
        self.assertIn("DOCICH_STREAM_OWNER", readme)


class WrapperTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="stream-wrapper-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def run_wrapper(self, env_text, *args, extra=None):
        env_file = self.root / "dot.env"
        env_file.write_text(env_text)
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.root), "SOREN_ENV_FILE": str(env_file),
               "SOREN_RUNTIME_REPO": str(self.root / "repo"), "DISPLAY": ":99", **(extra or {})}
        return subprocess.run([str(WRAPPER), *args], capture_output=True, text=True, timeout=30, check=False,
                              env=env, cwd="/")

    def test_syntax_and_shellcheck(self):
        self.assertEqual(subprocess.run(["bash", "-n", str(WRAPPER)], capture_output=True).returncode, 0)
        if shutil.which("shellcheck"):
            self.assertEqual(subprocess.run(["shellcheck", "-s", "bash", str(WRAPPER)], capture_output=True,
                                            text=True).returncode, 0)

    def test_the_env_file_is_read_with_shell_semantics_not_systemd_semantics(self):
        proc = self.run_wrapper(
            "# comment\n"
            "export SOREN_DIRECT_STREAM_FPS=\"25\"\n"           # `export` prefix + quotes
            "SOREN_DIRECT_STREAM_SIZE='640x360'\n"               # single quotes
            "BASE=1000\nSOREN_DIRECT_STREAM_VIDEO_KBPS=$((BASE+500))\n"   # shell arithmetic / expansion
            "SOREN_DIRECT_STREAM_PULSE_SOURCE=\"soren_null.monitor\"\n",
            "config")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual((data["fps"], data["width"], data["height"], data["video_kbps"]), (25, 640, 360, 1500))

    def test_the_default_action_is_run_and_without_the_owner_flag_it_does_nothing(self):
        proc = self.run_wrapper("X=1\n")
        self.assertEqual(proc.returncode, supervisor.NOT_OWNER_EXIT, proc.stderr)
        self.assertIn("legacy runner owns the encoder", proc.stderr)
        self.assertEqual(list((self.root / "repo").glob("**/*")) if (self.root / "repo").exists() else [], [])

    def test_the_owner_flag_in_the_env_file_is_honoured_and_validated(self):
        proc = self.run_wrapper("DOCICH_STREAM_OWNER=maybe\n")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("must be soren or docich", proc.stderr)

    def test_a_missing_env_file_is_tolerated_like_the_legacy_wrapper(self):
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(self.root), "SOREN_ENV_FILE": str(self.root / "absent"),
               "SOREN_RUNTIME_REPO": str(self.root / "repo"), "DISPLAY": ":99"}
        proc = subprocess.run([str(WRAPPER), "config"], capture_output=True, text=True, timeout=30, check=False,
                              env=env, cwd="/")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_the_supervisor_command_line_matches_what_stop_recognises(self):
        source = WRAPPER.read_text()
        self.assertIn('exec python3 -m docich.stream_supervisor --root', source)
        self.assertTrue(runner.is_owned_runner_cmdline(
            b"python3\x00-m\x00docich.stream_supervisor\x00--root\x00/home/ubuntu/soren\x00run\x00"))


if __name__ == "__main__":
    unittest.main()

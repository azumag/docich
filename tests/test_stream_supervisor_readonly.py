"""The stage-B supervisor entry point is read-only and refuses everything else."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOREN = ROOT / "games" / "soviet_now" / "lib" / "direct_stream.py"
BASE_ENV = {
    "PATH": os.environ.get("PATH", ""), "HOME": "/nonexistent", "DISPLAY": ":99",
    "XDG_RUNTIME_DIR": "/run/user/1001", "SOREN_STREAM_BACKEND": "ffmpeg",
}


def supervise(*args, env=None, root=None):
    full = {**BASE_ENV, "PYTHONPATH": str(ROOT / "src"), **(env or {})}
    return subprocess.run(
        [sys.executable, "-B", "-m", "docich.stream_supervisor", *( ["--root", str(root)] if root else []), *args],
        capture_output=True, text=True, timeout=20, check=False, env=full, cwd="/",
    )


class ReadOnlyCommandsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="stream-supervisor-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_config_is_redacted_and_rooted(self):
        proc = supervise("config", root=self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertNotIn("local_rtmp_url", data)
        self.assertNotIn("rtmp://", proc.stdout)
        self.assertEqual(data["state_dir"], str(self.root / "tmp/state/direct_stream"))
        self.assertEqual(data["backend"], "ffmpeg")

    def test_command_prints_the_live_and_record_argv(self):
        proc = supervise("command", root=self.root)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        argv = json.loads(proc.stdout)
        self.assertEqual(argv[0], "ffmpeg")
        self.assertEqual(argv[-5:], ["-flvflags", "no_duration_filesize", "-f", "flv", "rtmp://127.0.0.1:1935/soren/live"])
        record = json.loads(supervise("command", "--mode", "record", "--output", "/tmp/o.mkv",
                                      "--duration", "7", root=self.root).stdout)
        self.assertEqual(record[-6:], ["-t", "7", "-y", "-f", "matroska", "/tmp/o.mkv"])

    def test_command_errors_exit_2_without_a_traceback(self):
        for args, env, text in (
            (("command", "--mode", "record"), {}, "requires --output"),
            (("config",), {"SOREN_DIRECT_STREAM_FPS": "0"}, "between 1 and 60"),
            (("command",), {"SOREN_DIRECT_STREAM_LOCAL_URL": "rtmp://stream.example/live"}, "must target loopback"),
            (("config",), {"SOREN_STREAM_BACKEND": "obs"}, "must be ffmpeg"),
        ):
            with self.subTest(args=args, env=env):
                proc = supervise(*args, env=env, root=self.root)
                self.assertEqual(proc.returncode, 2)
                self.assertIn(text, proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)
                self.assertEqual(proc.stdout, "")

    def test_status_reads_but_never_writes(self):
        state = self.root / "tmp/state/direct_stream"
        proc = supervise("status", root=self.root)
        self.assertEqual(json.loads(proc.stdout), {"backend": "ffmpeg", "running": False, "state": "not_started"})
        self.assertFalse(state.exists(), "status must not create the state directory")
        state.mkdir(parents=True)
        status_file = state / "status.json"
        status_file.write_text(json.dumps(
            {"running": True, "pid": os.getpid(), "output_url": "rtmp://x/y", "state": "running"}))
        shown = json.loads(supervise("status", root=self.root).stdout)
        self.assertNotIn("output_url", shown)
        self.assertTrue(shown["running"], "the recorded pid (this test process) is alive")
        status_file.write_text(json.dumps({"running": True, "pid": 2 ** 22 + 1, "state": "running"}))
        self.assertFalse(json.loads(supervise("status", root=self.root).stdout)["running"])
        self.assertEqual(sorted(p.name for p in state.iterdir()), ["status.json"])


class RefusedCommandsTest(unittest.TestCase):
    def test_side_effecting_actions_are_refused_without_effects(self):
        with tempfile.TemporaryDirectory(prefix="stream-supervisor-") as tmp:
            root = Path(tmp)
            for action in ("run", "stop", "record", "validate"):
                with self.subTest(action=action):
                    proc = supervise(action, root=root)
                    self.assertEqual(proc.returncode, 2)
                    self.assertIn("not implemented in the read-only stage", proc.stderr)
                    self.assertEqual(proc.stdout, "")
            self.assertEqual(list(root.iterdir()), [], "refused actions must not create files or directories")

    def test_unknown_action_is_a_usage_error(self):
        self.assertEqual(supervise("bogus").returncode, 2)


class PurityTest(unittest.TestCase):
    def test_no_process_signal_lock_or_network_code(self):
        tree = ast.parse((ROOT / "src/docich/stream_supervisor.py").read_text())
        imported, attrs = set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(("." * node.level) + (node.module or ""))
            elif isinstance(node, ast.Attribute):
                attrs.add(node.attr)
        self.assertEqual(imported, {
            "__future__", "argparse", "json", "os", "pathlib", "sys", "typing",
            ".stream_config", ".stream_encoder_command", ".stream_state_files",
        })
        self.assertFalse(attrs & {"Popen", "system", "kill", "send_signal", "flock", "connect",
                                  "open_canonical_stream_encoder_lock", "acquire_nonblocking_encoder_lock",
                                  "atomic_write_json", "ensure_private_directory", "mkdir"})


@unittest.skipUnless(SOREN.is_file(), "soviet_now submodule is not checked out (CI uses submodules: false)")
class SorenParityTest(unittest.TestCase):
    def test_read_only_output_is_identical_to_the_legacy_cli(self):
        with tempfile.TemporaryDirectory(prefix="stream-supervisor-") as tmp:
            root = Path(tmp)
            env = {**BASE_ENV, "SOREN_RUNTIME_REPO": str(root), "SOREN_DIRECT_STREAM_STATE_DIR": str(root / "st"),
                   "SOREN_DIRECT_STREAM_LOG_FILE": str(root / "x.log")}
            for captions in ("0", "1"):
                for args in (("config",), ("command",), ("command", "--mode", "record", "--output", str(root / "o.mkv"), "--duration", "9"), ("status",)):
                    with self.subTest(captions=captions, args=args):
                        e = {**env, "DOCICH_CC_ENABLED": captions}
                        legacy = subprocess.run([sys.executable, str(SOREN), *args], capture_output=True, text=True,
                                                timeout=20, check=False, env=e, cwd=str(SOREN.parents[1]))
                        ours = supervise(*args, env=e, root=root)
                        self.assertEqual((ours.returncode, ours.stdout), (legacy.returncode, legacy.stdout))


if __name__ == "__main__":
    unittest.main()

"""Private-directory, atomic JSON, pid liveness and status contracts."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import docich.stream_state_files as st  # noqa: E402


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="stream-state-files-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)


class PidAliveTest(unittest.TestCase):
    def test_current_process_and_invalid_values(self):
        self.assertEqual(st.STREAM_STATE_FILES_CONTRACT_VERSION, 1)
        self.assertTrue(st.pid_alive(os.getpid()))
        self.assertTrue(st.pid_alive(str(os.getpid())))
        for value in (None, "", "abc", 0, -5, "-1", "x"):
            with self.subTest(value=value):
                self.assertFalse(st.pid_alive(value))

    def test_signal_zero_only_and_error_mapping(self):
        with mock.patch("os.kill", side_effect=ProcessLookupError) as kill:
            self.assertFalse(st.pid_alive(123))
        kill.assert_called_once_with(123, 0)
        with mock.patch("os.kill", side_effect=PermissionError):
            self.assertTrue(st.pid_alive(123))
        with mock.patch("os.kill", return_value=None) as kill:
            self.assertTrue(st.pid_alive(7))
        self.assertEqual(kill.call_args.args[1], 0)


class PrivateDirectoryTest(TempCase):
    def test_every_missing_level_is_0700_regardless_of_umask(self):
        old = os.umask(0)
        try:
            target = self.root / "a" / "b" / "c"
            st.ensure_private_directory(target)
        finally:
            os.umask(old)
        for level in (self.root / "a", self.root / "a" / "b", target):
            self.assertEqual(stat.S_IMODE(level.stat().st_mode), 0o700)

    def test_relaxed_owned_directory_self_heals_but_keeps_identity(self):
        target = self.root / "d"
        target.mkdir()
        os.chmod(target, 0o777)
        inode = target.stat().st_ino
        st.ensure_private_directory(target)
        self.assertEqual(target.stat().st_ino, inode)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode) & 0o022, 0)
        st.ensure_private_directory(target)  # idempotent

    def test_a_file_in_place_of_the_directory_is_refused(self):
        target = self.root / "f"
        target.write_text("x")
        with self.assertRaises(FileExistsError):
            st.ensure_private_directory(target)
        self.assertEqual(target.read_text(), "x")


class AtomicJsonTest(TempCase):
    def test_sorted_utf8_json_and_no_temp_leftover(self):
        path = self.root / "state" / "status.json"
        st.atomic_write_json(path, {"b": 1, "a": "日本語"})
        self.assertEqual(path.read_text(encoding="utf-8"), '{"a": "日本語", "b": 1}\n')
        self.assertEqual([p.name for p in path.parent.iterdir()], ["status.json"])
        st.atomic_write_json(path, {"a": 2})
        self.assertEqual(json.loads(path.read_text()), {"a": 2})

    def test_failed_serialisation_keeps_old_file_and_cleans_temp(self):
        path = self.root / "status.json"
        st.atomic_write_json(path, {"ok": True})
        with self.assertRaises(TypeError):
            st.atomic_write_json(path, {"bad": object()})
        self.assertEqual(json.loads(path.read_text()), {"ok": True})
        self.assertEqual([p.name for p in self.root.iterdir()], ["status.json"])

    def test_rename_failure_cleans_temp(self):
        path = self.root / "status.json"
        with mock.patch("os.replace", side_effect=OSError("boom")), self.assertRaises(OSError):
            st.atomic_write_json(path, {"a": 1})
        self.assertEqual(list(self.root.iterdir()), [])


class ReadStatusTest(TempCase):
    def write(self, payload):
        path = self.root / "status.json"
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
        return path

    def test_missing_corrupt_and_non_object_files(self):
        self.assertEqual(st.read_stream_status(self.root / "none.json"),
                         {"backend": "ffmpeg", "running": False, "state": "not_started"})
        self.assertEqual(st.read_stream_status(self.write("{bad")),
                         {"backend": "ffmpeg", "running": False, "state": "not_started"})
        self.assertEqual(st.read_stream_status(self.write("[1]")),
                         {"backend": "ffmpeg", "running": False, "state": "invalid_status"})

    def test_output_url_is_never_exposed_and_running_needs_a_live_pid(self):
        path = self.write({"running": True, "pid": 11, "ffmpeg_pid": 12, "output_url": "rtmp://x/y", "state": "running"})
        live = st.read_stream_status(path, alive=lambda pid: pid == 12)
        self.assertNotIn("output_url", live)
        self.assertTrue(live["running"])
        dead = st.read_stream_status(path, alive=lambda pid: False)
        self.assertFalse(dead["running"])
        stale = st.read_stream_status(self.write({"running": False, "pid": 11}), alive=lambda pid: True)
        self.assertFalse(stale["running"])


class PurityTest(unittest.TestCase):
    def test_no_network_process_start_or_game_dependencies(self):
        import ast
        tree = ast.parse((ROOT / "src/docich/stream_state_files.py").read_text())
        imported = set()
        attrs = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
            elif isinstance(node, ast.Attribute):
                attrs.add(node.attr)
        self.assertEqual(imported, {"__future__", "json", "os", "pathlib", "stat", "tempfile", "typing"})
        self.assertFalse(attrs & {"Popen", "system", "killpg", "environ", "getenv", "popen"})
        kill_calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                      and isinstance(n.func, ast.Attribute) and n.func.attr == "kill"]
        self.assertEqual(len(kill_calls), 1)
        self.assertEqual(kill_calls[0].args[1].value, 0, "only signal 0 liveness probes are allowed")

    def test_module_loads_by_file_path_without_package(self):
        code = (
            "import importlib.util,sys;s=importlib.util.spec_from_file_location('x',sys.argv[1]);"
            "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);print(m.pid_alive(0))"
        )
        proc = subprocess.run([sys.executable, "-I", "-c", code, str(ROOT / "src/docich/stream_state_files.py")],
                              capture_output=True, text=True, timeout=12, check=False)
        self.assertEqual((proc.returncode, proc.stdout.strip()), (0, "False"), proc.stderr)


if __name__ == "__main__":
    unittest.main()

import importlib.util
from pathlib import Path
import tempfile
import unittest

MODULE_PATH = Path(__file__).resolve().parents[1] / "start_soren91_manual_probe.py"
SPEC = importlib.util.spec_from_file_location("start_soren91_manual_probe", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class FakeProc:
    def __init__(self, returncode=None):
        self.returncode = returncode
    def poll(self):
        return self.returncode


class ManualProbeTests(unittest.TestCase):
    def test_command_is_fixed_five_minute_manual_soren91_runner(self):
        root = Path("/tmp/docich")
        self.assertEqual(MODULE.command(root), [
            "/tmp/docich/bin/docich-soren91-corner-manual",
            "--config", "/tmp/docich/config/docich.soren-live.toml",
            "start", "--duration-minutes", "5",
        ])

    def test_launch_is_detached_and_refuses_immediate_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "logs/probe.log"
            calls = []
            old_cwd = Path.cwd()
            try:
                import os
                os.chdir(root)
                def popen(argv, **kwargs):
                    calls.append((argv, kwargs))
                    return FakeProc()
                MODULE.launch(root=root, log_path=log, popen=popen, sleep=lambda _s: None)
            finally:
                os.chdir(old_cwd)
            self.assertEqual(len(calls), 1)
            self.assertTrue(calls[0][1]["start_new_session"])
            self.assertTrue(calls[0][1]["close_fds"])

    def test_launch_fails_when_runner_exits_immediately(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_cwd = Path.cwd()
            try:
                import os
                os.chdir(root)
                with self.assertRaisesRegex(RuntimeError, "manual_probe_exited_immediately"):
                    MODULE.launch(
                        root=root,
                        log_path=root / "probe.log",
                        popen=lambda *_a, **_k: FakeProc(1),
                        sleep=lambda _s: None,
                    )
            finally:
                os.chdir(old_cwd)


if __name__ == "__main__":
    unittest.main()

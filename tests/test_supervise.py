import io
import signal
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config, supervise  # noqa: E402


class TestLogLine(unittest.TestCase):
    def test_format_and_dual_write(self):
        fh = io.StringIO()
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            supervise.log_line(fh, "display", "hello world")
        line = fh.getvalue()
        self.assertRegex(
            line.strip(),
            r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] \[display\] hello world$",
        )
        self.assertEqual(out.getvalue().strip(), line.strip())


class TestNextBackoff(unittest.TestCase):
    def test_holds_while_short_lived(self):
        self.assertEqual(supervise._next_backoff(4, alive_s=1.0), 4)
        self.assertEqual(supervise._next_backoff(4, alive_s=59.9), 4)

    def test_resets_once_long_lived(self):
        self.assertEqual(supervise._next_backoff(16, alive_s=60.0), supervise.BACKOFF_START_S)
        self.assertEqual(supervise._next_backoff(16, alive_s=120.0), supervise.BACKOFF_START_S)


class SuperviseTestBase(unittest.TestCase):
    """Shared fixture: isolated repo_root/GlobalConfig, no real sleeping, and
    signal handlers restored afterwards (run_loop/run_callable_loop install
    process-wide SIGTERM/SIGHUP/SIGINT handlers as a side effect)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self._tmpdir.name)
        self.g = config.load_global(self.repo_root)

        self._sleep_patch = mock.patch("docich.supervise.time.sleep", lambda s: None)
        self._sleep_patch.start()

        self._old_handlers = {
            sig: signal.getsignal(sig) for sig in supervise.STOP_SIGNALS
        }

    def tearDown(self):
        for sig, handler in self._old_handlers.items():
            signal.signal(sig, handler)
        self._sleep_patch.stop()
        self._tmpdir.cleanup()

    def _log_text(self, component: str) -> str:
        log_path = self.repo_root / "run" / "logs" / f"{component}.log"
        return log_path.read_text(encoding="utf-8")


class TestRunLoop(SuperviseTestBase):
    def test_restarts_subprocess_and_logs(self):
        calls = []

        def build():
            calls.append(1)
            if len(calls) > 3:
                raise SystemExit(0)
            return ([sys.executable, "-c", "pass"], {})

        with self.assertRaises(SystemExit):
            supervise.run_loop("testcomp", self.g, build)

        self.assertEqual(len(calls), 4)
        content = self._log_text("testcomp")
        self.assertEqual(content.count("[testcomp] 起動:"), 3)
        self.assertIn("再起動します", content)

    def test_build_exception_is_logged_and_retried(self):
        calls = []

        def build():
            calls.append(1)
            if len(calls) > 2:
                raise SystemExit(0)
            raise RuntimeError("ROM がありません")

        with self.assertRaises(SystemExit):
            supervise.run_loop("brokencomp", self.g, build)

        self.assertEqual(len(calls), 3)
        content = self._log_text("brokencomp")
        self.assertIn("ROM がありません", content)

    def test_pre_hook_runs_before_every_build(self):
        pre_calls = []
        build_calls = []

        def pre():
            pre_calls.append(1)

        def build():
            build_calls.append(1)
            if len(build_calls) > 2:
                raise SystemExit(0)
            return ([sys.executable, "-c", "pass"], {})

        with self.assertRaises(SystemExit):
            supervise.run_loop("precomp", self.g, build, pre=pre)

        self.assertEqual(len(pre_calls), 3)
        self.assertEqual(len(pre_calls), len(build_calls))

    def test_post_start_receives_the_popen(self):
        received = []

        def build():
            if received:
                raise SystemExit(0)
            return ([sys.executable, "-c", "pass"], {})

        def post_start(p):
            received.append(p)

        with self.assertRaises(SystemExit):
            supervise.run_loop("postcomp", self.g, build, post_start=post_start)

        self.assertEqual(len(received), 1)
        self.assertTrue(hasattr(received[0], "pid"))


class TestRunCallableLoop(SuperviseTestBase):
    def test_retries_after_exception(self):
        calls = []

        def fn():
            calls.append(1)
            if len(calls) > 3:
                raise SystemExit(0)
            raise ValueError("boom")

        with self.assertRaises(SystemExit):
            supervise.run_callable_loop("agentcomp", self.g, fn)

        self.assertEqual(len(calls), 4)
        content = self._log_text("agentcomp")
        self.assertIn("boom", content)
        self.assertIn("再実行します", content)

    def test_normal_return_is_also_retried(self):
        calls = []

        def fn():
            calls.append(1)
            if len(calls) > 2:
                raise SystemExit(0)
            return None

        with self.assertRaises(SystemExit):
            supervise.run_callable_loop("agentcomp2", self.g, fn)

        self.assertEqual(len(calls), 3)
        content = self._log_text("agentcomp2")
        self.assertIn("正常終了しました", content)


if __name__ == "__main__":
    unittest.main()

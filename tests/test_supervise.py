import io
import os
import signal
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config, supervise  # noqa: E402


class _BrokenStream:
    """Simulates a hung-up pty / closed fd: every write (and flush) raises
    OSError [Errno 5] Input/output error, exactly as observed when tmux
    kill-session closes the pane holding a supervised component's stdout."""

    def write(self, _s):
        raise OSError(5, "Input/output error")

    def flush(self):
        raise OSError(5, "Input/output error")


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

    def test_survives_broken_stdout(self):
        # 回帰テスト: tmux が pane を kill すると pty が hang up し、stdout への
        # 書き込みが OSError [Errno 5] Input/output error になりうる (実機で確認済み)。
        # これが signal ハンドラ内の log_line から漏れると、後続の子プロセス
        # terminate/kill が実行されず孤児化する (scripts/smoke_cli.sh で発見)。
        fh = io.StringIO()
        with mock.patch("sys.stdout", _BrokenStream()):
            supervise.log_line(fh, "display", "hello world")  # must not raise

        # stdout 側が壊れていても fh (ログファイル) 側への書き込みは成功していること
        self.assertIn("hello world", fh.getvalue())

    def test_survives_broken_log_file(self):
        # 逆方向 (ログファイル側の fd が壊れている) でも stdout 側の書き込みは
        # 独立して成功し、例外は漏れないこと。
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            supervise.log_line(_BrokenStream(), "display", "hello world")  # must not raise

        self.assertIn("hello world", out.getvalue())

    def test_survives_both_streams_broken(self):
        with mock.patch("sys.stdout", _BrokenStream()):
            supervise.log_line(_BrokenStream(), "display", "hello world")  # must not raise


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


class TestSignalHandlerDoesNotOrphanChild(SuperviseTestBase):
    """End-to-end regression test for the orphaned Xvfb/ffmpeg bug found via
    scripts/smoke_cli.sh: a real OS signal arriving while stdout is broken
    (as happens when tmux kill-session hangs up the pane's pty) must still
    terminate the supervised child process rather than silently failing
    inside log_line and skipping cleanup."""

    def test_child_is_terminated_when_signal_arrives_with_broken_stdout(self):
        received: list = []
        proc_started = threading.Event()
        build_calls: list = []

        def post_start(p):
            received.append(p)
            proc_started.set()

        def build():
            build_calls.append(1)
            if len(build_calls) > 1:
                # 想定どおりなら signal で即座に抜けるので、ここには到達しない。
                # 到達した場合 (= 孤児化バグの再発) でもテストが無限ループせず
                # 失敗して終わるための安全弁。
                raise SystemExit(0)
            # signal が届く前に自然終了しない程度に生きる子プロセス。
            return ([sys.executable, "-c", "import time; time.sleep(3)"], {})

        def send_sigterm_once_started():
            # docich.supervise.time.sleep はこのテストクラス全体で no-op に
            # 差し替わっている (SuperviseTestBase) ため、実時間の同期には
            # (time.sleep ではなく) threading.Event を使う。
            if proc_started.wait(timeout=5):
                os.kill(os.getpid(), signal.SIGTERM)

        sender = threading.Thread(target=send_sigterm_once_started, daemon=True)
        sender.start()
        try:
            with mock.patch("sys.stdout", _BrokenStream()):
                with self.assertRaises(SystemExit):
                    supervise.run_loop("orphancomp", self.g, build, post_start=post_start)
        finally:
            sender.join(timeout=5)

        self.assertEqual(
            len(build_calls), 1,
            "signal で早期終了せず 2 回目の build に到達しました (孤児化バグの再発の兆候)",
        )
        self.assertEqual(len(received), 1)
        p = received[0]
        self.assertIsNotNone(p.poll(), "子プロセスが孤児化しました (poll() が None のまま)")

        content = self._log_text("orphancomp")
        self.assertIn("シグナル", content)  # stdout は壊れていても fh には書けている


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

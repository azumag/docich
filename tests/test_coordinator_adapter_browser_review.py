"""Regression tests for browser coordinator review edge cases."""

import sys
import time
import unittest
from pathlib import Path
from subprocess import TimeoutExpired
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.adapters import browser  # noqa: E402
from docich.adapters.base import AdapterError  # noqa: E402


class TestBrowserReviewRegressions(unittest.TestCase):
    def _adapter(self, raw):
        adapter = object.__new__(browser.BrowserCoordinatorAdapter)
        adapter.game = SimpleNamespace(raw={"browser": raw})
        adapter.xkit = mock.Mock()
        return adapter

    def test_preflight_rejects_present_but_empty_launch_command(self):
        game = SimpleNamespace(raw={"browser": {"launch_command": []}})
        with self.assertRaises(AdapterError):
            browser.validate_browser_preflight(game)

    def test_preflight_validates_exact_argv_head_without_shell_splitting(self):
        game = SimpleNamespace(raw={"browser": {"launch_command": ["echo hi"]}})
        with mock.patch.object(browser, "_binary_exists", return_value=False) as exists:
            with self.assertRaises(AdapterError):
                browser.validate_browser_preflight(game)
        exists.assert_called_once_with("echo hi")

    def test_poll_wait_uses_fresh_deadline_remaining(self):
        adapter = self._adapter({})
        with mock.patch.object(browser.time, "monotonic", return_value=9.95), mock.patch.object(
            browser.time, "sleep"
        ) as sleep:
            adapter._poll_wait(10.0, None, "timeout")
        self.assertAlmostEqual(sleep.call_args.args[0], 0.05, places=6)

    def test_devtools_io_uses_sub_grace_slice(self):
        adapter = self._adapter({"url": "http://127.0.0.1:8080"})
        with mock.patch.object(
            adapter,
            "_devtools_pages",
            return_value=[{"url": "http://127.0.0.1:8080/"}],
        ) as pages:
            adapter._probe_expected_url(time.monotonic() + 5, None)
        self.assertLessEqual(pages.call_args.args[0], browser.READY_IO_TIMEOUT_S)

    def test_argv_probe_timeout_is_retryable_probe_miss(self):
        adapter = self._adapter(
            {"readiness_probe": {"type": "argv", "command": ["true"]}}
        )
        with mock.patch.object(adapter, "_poll_wait", return_value=None), mock.patch.object(
            browser.procs,
            "run",
            side_effect=[TimeoutExpired(["true"], 0.1), mock.Mock(returncode=0)],
        ) as run:
            adapter._probe_launch_command(time.monotonic() + 5, None)
        self.assertEqual(run.call_count, 2)
        self.assertLessEqual(run.call_args.kwargs["timeout"], browser.READY_IO_TIMEOUT_S)

    def test_window_probe_timeout_is_retryable_probe_miss(self):
        adapter = self._adapter(
            {"readiness_probe": {"type": "window", "window_pattern": "Soren"}}
        )
        adapter.xkit.find_window.side_effect = [TimeoutExpired(["xdotool"], 0.1), "0x1"]
        with mock.patch.object(adapter, "_poll_wait", return_value=None):
            adapter._probe_launch_command(time.monotonic() + 5, None)
        self.assertEqual(adapter.xkit.find_window.call_count, 2)
        self.assertLessEqual(
            adapter.xkit.find_window.call_args.kwargs["timeout"], browser.READY_IO_TIMEOUT_S
        )


if __name__ == "__main__":
    unittest.main()

"""presentation.py viewer-wait option: default stays 10s, SRT path can extend."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.presentation import _parser, _record_viewer_failure, main  # noqa: E402


def _args(extra=()):
    base = [
        "--display", ":97", "--title", "t",
        "--x", "0", "--y", "90", "--width", "960", "--height", "540",
    ]
    return _parser().parse_args(base + list(extra) + ["--", "true"])


class ViewerWaitTests(unittest.TestCase):
    def test_default_is_10s(self):
        self.assertEqual(_args().viewer_wait_sec, 10)

    def test_custom_budget_accepted(self):
        self.assertEqual(_args(("--viewer-wait-sec", "120")).viewer_wait_sec, 120)

    def test_non_positive_rejected(self):
        with self.assertRaises(SystemExit):
            _parser().parse_args([
                "--display", ":97", "--title", "t",
                "--x", "0", "--y", "0", "--width", "1", "--height", "1",
                "--viewer-wait-sec", "0", "--", "true",
            ])

    def test_rebind_option_is_opt_in_and_requires_a_named_window(self):
        self.assertFalse(_args().rebind_window)
        self.assertTrue(_args(("--window-pattern", "^viewer$", "--rebind-window")).rebind_window)
        with self.assertRaises(SystemExit):
            main([
                "--display", ":97", "--title", "t",
                "--x", "0", "--y", "0", "--width", "1", "--height", "1",
                "--rebind-window", "--", "true",
            ])

    def test_failure_state_is_optional_and_off_by_default(self):
        self.assertIsNone(_args().failure_state)
        self.assertEqual(_args(("--failure-state", "/tmp/x.json")).failure_state, "/tmp/x.json")

    def test_viewer_timeout_record_names_the_missing_viewer(self):
        # docich#1221: the pane dies with its tmux window, so the reason the
        # presenter gave up must be durable for the owning adapter.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "presentation_failure.json"
            _record_viewer_failure(
                path,
                SimpleNamespace(poll=lambda: None),
                viewer_wait_sec=240,
                elapsed_s=240.012,
            )
            record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "viewer_timeout")
        self.assertEqual(record["viewer_wait_sec"], 240)
        self.assertEqual(record["elapsed_s"], 240.012)
        self.assertIsNone(record["viewer_exit_code"])
        self.assertEqual(record["reason"], "native game viewer did not appear")

    def test_viewer_exit_is_recorded_with_its_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "presentation_failure.json"
            _record_viewer_failure(
                path,
                SimpleNamespace(poll=lambda: 1),
                viewer_wait_sec=30,
                elapsed_s=1.25,
            )
            record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "viewer_exited")
        self.assertEqual(record["viewer_exit_code"], 1)

    def test_recording_a_failure_is_optional_and_never_raises(self):
        _record_viewer_failure(None, SimpleNamespace(poll=lambda: None),
                               viewer_wait_sec=10, elapsed_s=10.0)


if __name__ == "__main__":
    unittest.main()

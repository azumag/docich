import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import tmux as tmux_mod  # noqa: E402
from docich.process_tree import TerminationResult  # noqa: E402


def _ok(stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _fail(stderr: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=stderr)


class TestOwnedCleanupIdempotency(unittest.TestCase):
    def setUp(self):
        self.tmux = tmux_mod.Tmux()
        self.owner = tmux_mod.TmuxOwnership("g1-abcdef", 1, "game")

    @mock.patch("docich.tmux.terminate_process_tree")
    @mock.patch("docich.tmux.procs.run")
    def test_window_disappearing_after_pane_stop_is_success(self, mock_run, mock_terminate):
        mock_run.side_effect = [
            _ok("game-g1\n"),
            _ok("g1-abcdef\n"),
            _ok("1\n"),
            _ok("game\n"),
            _ok("123\n"),
            _fail("can't find window: game-g1"),
        ]
        mock_terminate.return_value = TerminationResult(
            roots=(123,), term_sent=(123,), kill_sent=(), remaining=()
        )

        self.assertTrue(self.tmux.kill_window_owned("docich:game-g1", self.owner))
        mock_terminate.assert_called_once_with([123])

    @mock.patch("docich.tmux.terminate_process_tree")
    @mock.patch("docich.tmux.procs.run")
    def test_session_disappearing_after_pane_stop_is_success(self, mock_run, mock_terminate):
        mock_run.side_effect = [
            _ok(),
            _ok("g1-abcdef\n"),
            _ok("1\n"),
            _ok("game\n"),
            _ok("123\n"),
            _fail("no server running on /tmp/tmux-1000/default"),
        ]
        mock_terminate.return_value = TerminationResult(
            roots=(123,), term_sent=(123,), kill_sent=(), remaining=()
        )

        self.assertTrue(self.tmux.kill_session_owned("docich-game-g1", self.owner))
        mock_terminate.assert_called_once_with([123])

    @mock.patch("docich.tmux.terminate_process_tree")
    @mock.patch("docich.tmux.procs.run")
    def test_post_stop_transport_error_still_fails_closed(self, mock_run, mock_terminate):
        mock_run.side_effect = [
            _ok("game-g1\n"),
            _ok("g1-abcdef\n"),
            _ok("1\n"),
            _ok("game\n"),
            _ok("123\n"),
            _fail("failed to connect to server: Connection refused"),
        ]
        mock_terminate.return_value = TerminationResult(
            roots=(123,), term_sent=(123,), kill_sent=(), remaining=()
        )

        with self.assertRaises(tmux_mod.TmuxError):
            self.tmux.kill_window_owned("docich:game-g1", self.owner)


if __name__ == "__main__":
    unittest.main()

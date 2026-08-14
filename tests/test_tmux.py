import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import tmux as tmux_mod  # noqa: E402


def _ok(stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


class TestTmuxCallsUseStripTmux(unittest.TestCase):
    def setUp(self):
        self.tmux = tmux_mod.Tmux()

    @mock.patch("docich.tmux.procs.run")
    def test_has_session_uses_strip_tmux(self, mock_run):
        mock_run.return_value = _ok()
        self.tmux.has_session()
        _, kwargs = mock_run.call_args
        self.assertTrue(kwargs.get("strip_tmux"))

    @mock.patch("docich.tmux.procs.run")
    def test_new_window_uses_strip_tmux(self, mock_run):
        mock_run.return_value = _ok()
        self.tmux.new_window("game", ["echo", "hi"])
        _, kwargs = mock_run.call_args
        self.assertTrue(kwargs.get("strip_tmux"))

    @mock.patch("docich.tmux.procs.run")
    def test_all_public_methods_pass_strip_tmux(self, mock_run):
        mock_run.return_value = _ok()
        self.tmux.has_session()
        self.tmux.ensure_session()
        self.tmux.kill_session()
        self.tmux.list_windows()
        self.tmux.new_window("w", ["true"])
        self.tmux.kill_window("w")
        self.tmux.new_game_session("docich-game", ["nethack"], 80, 24)
        self.tmux.set_status_off("docich-game")
        self.tmux.capture_pane("docich-game")
        self.tmux.send_keys("docich-game", ["hello"], literal=True)
        self.tmux.set_manual_size("docich-game", 80, 24)

        for call in mock_run.call_args_list:
            _, kwargs = call
            self.assertTrue(kwargs.get("strip_tmux"), f"strip_tmux missing in call: {call}")
            self.assertEqual(call.args[0][0], "tmux")


class TestNewWindowArgs(unittest.TestCase):
    def setUp(self):
        self.tmux = tmux_mod.Tmux()

    @mock.patch("docich.tmux.procs.run")
    def test_new_window_basic_args(self, mock_run):
        mock_run.return_value = _ok()
        self.tmux.new_window("game", ["docich", "run", "game", "nethack"])
        (args,), kwargs = mock_run.call_args
        self.assertEqual(
            args,
            [
                "tmux", "new-window", "-d", "-t", "docich", "-n", "game",
                "docich run game nethack",
            ],
        )

    @mock.patch("docich.tmux.procs.run")
    def test_new_window_with_env(self, mock_run):
        mock_run.return_value = _ok()
        self.tmux.new_window("stream", ["docich", "run", "stream"], env={"DOCICH_STREAM_KEY": "abc"})
        (args,), kwargs = mock_run.call_args
        self.assertIn("-e", args)
        self.assertIn("DOCICH_STREAM_KEY=abc", args)
        # command string is still the last argument
        self.assertEqual(args[-1], "docich run stream")

    @mock.patch("docich.tmux.procs.run")
    def test_new_window_quotes_args_with_spaces(self, mock_run):
        mock_run.return_value = _ok()
        self.tmux.new_window("send", ["docich", "send", "-", '{"type": "wait"}'])
        (args,), kwargs = mock_run.call_args
        # shlex.join should quote the JSON payload as a single shell token
        joined = args[-1]
        self.assertIn("docich send -", joined)
        self.assertIn("wait", joined)


class TestHasWindow(unittest.TestCase):
    def setUp(self):
        self.tmux = tmux_mod.Tmux()

    @mock.patch("docich.tmux.procs.run")
    def test_has_window_true(self, mock_run):
        mock_run.return_value = _ok("display\naudio\ngame\n")
        self.assertTrue(self.tmux.has_window("audio"))
        self.assertFalse(self.tmux.has_window("agent"))

    @mock.patch("docich.tmux.procs.run")
    def test_list_windows_empty_on_failure(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no session")
        self.assertEqual(self.tmux.list_windows(), [])


class TestKillIgnoresErrors(unittest.TestCase):
    def setUp(self):
        self.tmux = tmux_mod.Tmux()

    @mock.patch("docich.tmux.procs.run")
    def test_kill_window_does_not_raise_on_failure(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="not found")
        self.tmux.kill_window("does-not-exist")  # should not raise

    @mock.patch("docich.tmux.procs.run")
    def test_kill_session_named_does_not_raise_on_failure(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="not found")
        self.tmux.kill_session_named("no-such-session")  # should not raise


class TestNewGameSession(unittest.TestCase):
    def setUp(self):
        self.tmux = tmux_mod.Tmux()

    @mock.patch("docich.tmux.procs.run")
    def test_new_game_session_turns_status_off(self, mock_run):
        mock_run.return_value = _ok()
        self.tmux.new_game_session("docich-game", ["nethack"], 80, 24)

        calls = [c.args[0] for c in mock_run.call_args_list]
        self.assertEqual(calls[0][:6], ["tmux", "new-session", "-d", "-s", "docich-game", "-x"])
        self.assertIn(["tmux", "set-option", "-t", "docich-game", "status", "off"], calls)


if __name__ == "__main__":
    unittest.main()

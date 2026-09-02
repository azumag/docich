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
        self.tmux.has_session_named("docich-game")
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


class TestHasSessionNamed(unittest.TestCase):
    def setUp(self):
        self.tmux = tmux_mod.Tmux()

    @mock.patch("docich.tmux.procs.run")
    def test_true_when_session_exists(self, mock_run):
        mock_run.return_value = _ok()
        self.assertTrue(self.tmux.has_session_named("docich-game"))
        (args,), kwargs = mock_run.call_args
        self.assertEqual(args, ["tmux", "has-session", "-t", "docich-game"])

    @mock.patch("docich.tmux.procs.run")
    def test_false_when_session_missing(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no session")
        self.assertFalse(self.tmux.has_session_named("docich-game"))


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


class TestCheckedOperations(unittest.TestCase):
    def setUp(self):
        self.tmux = tmux_mod.Tmux()
        self.owner = tmux_mod.TmuxOwnership("g1-abcdef", 1, "game")

    @mock.patch("docich.tmux.procs.run")
    def test_checked_window_creation_raises_on_failure(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="can't create window\nsecret second line"
        )
        with self.assertRaises(tmux_mod.TmuxError) as ctx:
            self.tmux.new_window_checked("game-g1", ["true"])
        self.assertIn("window作成", str(ctx.exception))

    @mock.patch("docich.tmux.procs.run")
    def test_owned_window_creation_tags_and_verifies_stable_id(self, mock_run):
        mock_run.side_effect = [
            _ok("@7\n"),
            _ok(), _ok(), _ok(),
            _ok("g1-abcdef\n"), _ok("1\n"), _ok("game\n"),
        ]
        window_id = self.tmux.create_window_owned("game-g1", ["true"], self.owner)
        self.assertEqual(window_id, "@7")
        calls = [call.args[0] for call in mock_run.call_args_list]
        self.assertEqual(calls[0][1:6], ["new-window", "-d", "-P", "-F", "#{window_id}"])
        self.assertTrue(all("@7" in call for call in calls[1:]))

    @mock.patch("docich.tmux.procs.run")
    def test_owned_window_creation_rolls_back_exact_id_on_tag_failure(self, mock_run):
        mock_run.side_effect = [
            _ok("@7\n"),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="tag failed"),
            _ok(),
        ]
        with self.assertRaises(tmux_mod.TmuxError):
            self.tmux.create_window_owned("game-g1", ["true"], self.owner)
        self.assertEqual(mock_run.call_args_list[-1].args[0], ["tmux", "kill-window", "-t", "@7"])

    @mock.patch("docich.tmux.procs.run")
    def test_owned_session_creation_rolls_back_exact_id_when_status_off_fails(self, mock_run):
        owner = tmux_mod.TmuxOwnership("g1-abcdef", 1, "adapter")
        mock_run.side_effect = [
            _ok("$4\n"),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="status failed"),
            _ok(),
        ]
        with self.assertRaises(tmux_mod.TmuxError):
            self.tmux.create_game_session_owned("docich-game-g1", ["true"], 80, 24, owner)
        self.assertEqual(mock_run.call_args_list[-1].args[0], ["tmux", "kill-session", "-t", "$4"])

    @mock.patch("docich.tmux.procs.run")
    def test_set_window_ownership_writes_all_tags(self, mock_run):
        mock_run.return_value = _ok()
        self.tmux.set_window_ownership("docich:game-g1", self.owner)
        calls = [call.args[0] for call in mock_run.call_args_list]
        self.assertEqual(len(calls), 3)
        self.assertIn("@docich_runtime_id", calls[0])
        self.assertIn("@docich_generation", calls[1])
        self.assertIn("@docich_role", calls[2])

    @mock.patch("docich.tmux.procs.run")
    def test_capture_checked_distinguishes_empty_success_from_failure(self, mock_run):
        mock_run.return_value = _ok("")
        self.assertEqual(self.tmux.capture_pane_checked("docich-game-g1"), "")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="missing"
        )
        with self.assertRaises(tmux_mod.TmuxError):
            self.tmux.capture_pane_checked("docich-game-g1")

    @mock.patch("docich.tmux.procs.run")
    def test_pane_state_parses_dead_and_pid(self, mock_run):
        mock_run.return_value = _ok("0\t123\n1\t456\n")
        states = self.tmux.pane_states_checked("docich-game-g1")
        self.assertEqual(states, [tmux_mod.PaneState(False, 123), tmux_mod.PaneState(True, 456)])

    @mock.patch("docich.tmux.procs.run")
    def test_owned_kill_refuses_mismatched_tags(self, mock_run):
        mock_run.side_effect = [
            _ok("@1\n"),
            _ok("g2-abcdef\n"),
            _ok("2\n"),
            _ok("game\n"),
        ]
        with self.assertRaises(tmux_mod.OwnershipMismatchError):
            self.tmux.kill_window_owned("docich:game-g1", self.owner)
        calls = [call.args[0] for call in mock_run.call_args_list]
        self.assertFalse(any("kill-window" in call for call in calls))

    @mock.patch("docich.tmux.procs.run")
    def test_owned_kill_treats_missing_target_as_success(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="not found"
        )
        self.assertFalse(self.tmux.kill_window_owned("docich:game-g1", self.owner))
        self.assertEqual(len(mock_run.call_args_list), 1)

    @mock.patch("docich.tmux.procs.run")
    def test_owned_kill_fails_closed_on_unexpected_probe_error(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="permission denied"
        )
        with self.assertRaises(tmux_mod.TmuxError):
            self.tmux.kill_window_owned("docich:game-g1", self.owner)

    def test_checked_target_rejects_tmux_injection(self):
        with self.assertRaises(ValueError):
            self.tmux.capture_pane_checked("docich;kill-server")


if __name__ == "__main__":
    unittest.main()

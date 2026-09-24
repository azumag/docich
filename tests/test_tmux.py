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
        self.tmux.kill_session(allow_shared=True)
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

    @mock.patch("docich.tmux.procs.run_bounded_output")
    def test_bounded_capture_checks_dimensions_then_caps_visible_rows(self, mock_bounded):
        mock_bounded.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout=b"80\t24\n", stderr=b""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout=b"msg\n", stderr=b""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout=b"80\t24\n", stderr=b""),
        ]
        text = self.tmux.capture_pane_bounded_checked(
            "docich-game-g1:nethack-console",
            cols=80,
            rows=24,
            max_bytes=65536,
            timeout_s=0.75,
        )
        self.assertEqual(text, "msg\n")
        self.assertEqual(
            mock_bounded.call_args_list[0].args[0],
            [
                "tmux", "display-message", "-p", "-t",
                "docich-game-g1:nethack-console", "#{pane_width}\t#{pane_height}",
            ],
        )
        cmd = mock_bounded.call_args_list[1].args[0]
        self.assertEqual(
            cmd,
            [
                "tmux", "capture-pane", "-p", "-t",
                "docich-game-g1:nethack-console",
            ],
        )
        self.assertEqual(mock_bounded.call_args_list[0].kwargs["max_output_bytes"], 64)
        self.assertEqual(mock_bounded.call_args_list[1].kwargs["max_output_bytes"], 65536)
        self.assertEqual(mock_bounded.call_args_list[1].kwargs["timeout"], 0.75)
        self.assertTrue(mock_bounded.call_args_list[1].kwargs["strip_tmux"])
        self.assertEqual(mock_bounded.call_args_list[2].args[0][1], "display-message")

    @mock.patch("docich.tmux.procs.run_bounded_output")
    def test_bounded_capture_refuses_resized_pane_before_reading(self, mock_bounded):
        mock_bounded.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"120\t40\n", stderr=b""
        )
        with self.assertRaises(tmux_mod.TmuxError):
            self.tmux.capture_pane_bounded_checked(
                "docich-game-g1:nethack-console", cols=80, rows=24
            )
        self.assertEqual(mock_bounded.call_count, 1)

    def test_bounded_capture_rejects_bool_dimensions(self):
        with self.assertRaises(ValueError):
            self.tmux.capture_pane_bounded_checked("docich-game-g1", cols=True, rows=24)

    @mock.patch("docich.tmux.procs.run")
    def test_pane_state_parses_dead_and_pid(self, mock_run):
        mock_run.return_value = _ok("0\t123\n1\t456\n")
        states = self.tmux.pane_states_checked("docich-game-g1")
        self.assertEqual(states, [tmux_mod.PaneState(False, 123), tmux_mod.PaneState(True, 456)])

    @mock.patch("docich.tmux.procs.run")
    def test_owned_kill_refuses_mismatched_tags(self, mock_run):
        mock_run.side_effect = [
            _ok("game-g1\n"),
            _ok("g2-abcdef\n"),
            _ok("2\n"),
            _ok("game\n"),
        ]
        with self.assertRaises(tmux_mod.OwnershipMismatchError):
            self.tmux.kill_window_owned("docich:game-g1", self.owner)
        calls = [call.args[0] for call in mock_run.call_args_list]
        self.assertFalse(any("kill-window" in call for call in calls))

    @mock.patch("docich.tmux.terminate_process_tree")
    @mock.patch("docich.tmux.procs.run")
    def test_owned_kill_stops_pane_descendants_before_window(self, mock_run, mock_terminate):
        mock_run.side_effect = [
            _ok("game-g1\n"),
            _ok("g1-abcdef\n"),
            _ok("1\n"),
            _ok("game\n"),
            _ok("123\n"),
            _ok(),
        ]
        mock_terminate.return_value = TerminationResult(
            roots=(123,), term_sent=(123,), kill_sent=(), remaining=()
        )

        self.assertTrue(self.tmux.kill_window_owned("docich:game-g1", self.owner))
        mock_terminate.assert_called_once_with([123])
        self.assertEqual(mock_run.call_args_list[-1].args[0], ["tmux", "kill-window", "-t", "docich:game-g1"])

    def test_legacy_game_cleanup_rejects_shared_session(self):
        with self.assertRaises(ValueError):
            self.tmux.stop_game_session_named("docich")

    @mock.patch("docich.tmux.terminate_process_tree")
    @mock.patch("docich.tmux.procs.run")
    def test_legacy_game_cleanup_stops_generation_session_descendants(self, mock_run, mock_terminate):
        mock_run.side_effect = [_ok("123\n"), _ok()]
        mock_terminate.return_value = TerminationResult(
            roots=(123,), term_sent=(123,), kill_sent=(), remaining=()
        )

        self.tmux.stop_game_session_named("docich-game-g7")
        mock_terminate.assert_called_once_with([123])
        self.assertEqual(
            mock_run.call_args_list[-1].args[0],
            ["tmux", "kill-session", "-t", "docich-game-g7"],
        )

    @mock.patch("docich.tmux.procs.run")
    def test_window_exists_lists_and_matches_by_name(self, mock_run):
        # display-message succeeds even for nonexistent names on real tmux,
        # so existence must come from list-windows name matching.
        mock_run.return_value = _ok("display\n")
        self.assertTrue(self.tmux.window_target_exists("docich:display"))
        self.assertFalse(self.tmux.window_target_exists("docich:game-g9"))
        calls = [call.args[0] for call in mock_run.call_args_list]
        self.assertTrue(all("list-windows" in call for call in calls))

    @mock.patch("docich.tmux.procs.run")
    def test_owned_kill_treats_missing_target_as_success(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="not found"
        )
        self.assertFalse(self.tmux.kill_window_owned("docich:game-g1", self.owner))
        self.assertEqual(len(mock_run.call_args_list), 1)

    @mock.patch("docich.tmux.procs.run_bounded_output")
    def test_bounded_session_ownership_uses_small_timed_reads(self, mock_bounded):
        mock_bounded.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout=b"g1-abcdef\n", stderr=b""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout=b"1\n", stderr=b""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout=b"adapter\n", stderr=b""),
        ]
        self.assertEqual(
            self.tmux.read_session_ownership_bounded("docich-game-g1"),
            tmux_mod.TmuxOwnership("g1-abcdef", 1, "adapter"),
        )
        for call in mock_bounded.call_args_list:
            self.assertLessEqual(call.kwargs["max_output_bytes"], 256)
            self.assertLessEqual(call.kwargs["timeout"], 0.5)
            self.assertTrue(call.kwargs["strip_tmux"])
            self.assertIn("show-options", call.args[0])

    @mock.patch("docich.tmux.procs.run_bounded_output")
    def test_bounded_window_listing_fails_closed_and_caps_output(self, mock_bounded):
        mock_bounded.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"console\ngame-g1\nagent-g1\n", stderr=b""
        )
        self.assertEqual(
            self.tmux.list_windows_bounded("docich-game-g1"),
            ["console", "game-g1", "agent-g1"],
        )
        args, kwargs = mock_bounded.call_args
        self.assertEqual(args[0], ["tmux", "list-windows", "-t", "docich-game-g1", "-F", "#{window_name}"])
        self.assertEqual(kwargs["max_output_bytes"], 4096)
        self.assertEqual(kwargs["timeout"], 0.5)
        self.assertTrue(kwargs["strip_tmux"])

        mock_bounded.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout=b"", stderr=b"private"
        )
        with self.assertRaises(tmux_mod.TmuxError):
            self.tmux.list_windows_bounded("docich-game-g1")

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

    @mock.patch("docich.tmux.procs.run")
    def test_window_exists_treats_connect_failure_as_absent_by_default(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="",
            stderr="failed to connect to server: Connection refused",
        )
        self.assertFalse(self.tmux.window_target_exists("docich:game"))

    @mock.patch("docich.tmux.procs.run")
    def test_window_exists_strict_fails_closed_on_connect_failure(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="",
            stderr="failed to connect to server: Connection refused",
        )
        with self.assertRaises(tmux_mod.TmuxError):
            self.tmux.window_target_exists("docich:game", strict=True)

    @mock.patch("docich.tmux.procs.run")
    def test_session_exists_strict_fails_closed_on_connect_failure(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="",
            stderr="failed to connect to server: Connection refused",
        )
        with self.assertRaises(tmux_mod.TmuxError):
            self.tmux.session_target_exists("docich-game", strict=True)

    @mock.patch("docich.tmux.procs.run")
    def test_session_exists_strict_reads_absent_sessions(self, mock_run):
        mock_run.side_effect = [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="can't find session"),
        ]
        self.assertTrue(self.tmux.session_target_exists("docich-game", strict=True))
        self.assertFalse(self.tmux.session_target_exists("docich-game", strict=True))


class TestSharedSessionKillGuard(unittest.TestCase):
    """Issue #219: 共有 session の kill は明示的な全体停止経路でのみ許可する。"""

    def setUp(self):
        self.tmux = tmux_mod.Tmux()

    @mock.patch("docich.tmux.procs.run")
    def test_kill_session_named_refuses_shared_session(self, mock_run):
        with self.assertRaises(tmux_mod.OwnershipMismatchError):
            self.tmux.kill_session_named("docich")
        mock_run.assert_not_called()

    @mock.patch("docich.tmux.procs.run")
    def test_kill_session_refuses_default_shared_session(self, mock_run):
        with self.assertRaises(tmux_mod.OwnershipMismatchError):
            self.tmux.kill_session()
        mock_run.assert_not_called()

    @mock.patch("docich.tmux.procs.run")
    def test_kill_session_named_allows_shared_with_explicit_flag(self, mock_run):
        mock_run.return_value = _ok()
        self.tmux.kill_session_named("docich", allow_shared=True)
        mock_run.assert_called_once()

    @mock.patch("docich.tmux.procs.run")
    def test_kill_session_named_allows_game_session(self, mock_run):
        mock_run.return_value = _ok()
        self.tmux.kill_session_named("docich-game")
        mock_run.assert_called_once()

    def test_stop_game_session_named_rejects_shared_session(self):
        with self.assertRaises(ValueError):
            self.tmux.stop_game_session_named("docich")


if __name__ == "__main__":
    unittest.main()

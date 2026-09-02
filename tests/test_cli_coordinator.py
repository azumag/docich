"""P2 tests: CLI lifecycle commands wired through the GameSwitchCoordinator."""

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import cli, config  # noqa: E402
from docich.game_switch import GameSwitchStore  # noqa: E402
from docich.tmux import OwnershipMismatchError, PaneState  # noqa: E402


class FakeTmux:
    """Checked tmux subset with ownership tracking."""

    def __init__(self):
        self.sessions = {}
        self.windows = {}
        self.pane_states = [PaneState(dead=False, pid=1234)]
        self.capture = "screen"

    def _expected(self, ownership):
        return (ownership.runtime_id, ownership.generation, ownership.role)

    def session_target_exists(self, session):
        return session in self.sessions

    def window_target_exists(self, target):
        return target in self.windows

    def create_game_session_owned(self, session, cmd, cols, rows, ownership):
        if session in self.sessions:
            raise RuntimeError(f"duplicate session {session}")
        self.sessions[session] = self._expected(ownership)

    def create_window_owned(self, name, cmd, ownership, env=None):
        target = f"docich:{name}"
        if target in self.windows:
            raise RuntimeError(f"duplicate window {target}")
        self.windows[target] = self._expected(ownership)

    def kill_session_owned(self, session, expected):
        if session not in self.sessions:
            return False
        if self.sessions[session] != self._expected(expected):
            raise OwnershipMismatchError("session ownership mismatch")
        del self.sessions[session]
        return True

    def kill_window_owned(self, target, expected):
        if target not in self.windows:
            return False
        if self.windows[target] != self._expected(expected):
            raise OwnershipMismatchError("window ownership mismatch")
        del self.windows[target]
        return True

    def read_session_ownership(self, session):
        from docich.tmux import TmuxOwnership

        runtime_id, generation, role = self.sessions[session]
        return TmuxOwnership(runtime_id=runtime_id, generation=generation, role=role)

    def read_window_ownership(self, target):
        from docich.tmux import TmuxOwnership

        runtime_id, generation, role = self.windows[target]
        return TmuxOwnership(runtime_id=runtime_id, generation=generation, role=role)

    def pane_states_checked(self, target):
        return list(self.pane_states)

    def capture_pane_checked(self, target):
        return self.capture


class CliCoordinatorTestBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.g = config.load_global(self.root)
        games = self.root / "config" / "games"
        games.mkdir(parents=True, exist_ok=True)
        self.game_path = games / "nethack.toml"
        self.game_path.write_text(
            '[game]\nname = "nethack"\nadapter = "cli"\ntitle = "NetHack"\n\n'
            '[cli]\ncommand = "nethack"\n',
            encoding="utf-8",
        )
        self.tmux = FakeTmux()
        self.which = mock.patch(
            "docich.adapters.cli_game.procs.which", return_value="/usr/games/nethack"
        )
        self.which.start()

    def tearDown(self):
        self.which.stop()
        self._tmpdir.cleanup()

    def _coordinator_adapter_tmux(self):
        return mock.patch("docich.adapters.cli_game.Tmux", return_value=self.tmux)

    def _display_tmux(self):
        tmux = mock.Mock()
        tmux.has_window.side_effect = lambda name: name == "display"
        return mock.patch("docich.cli.Tmux", return_value=tmux)

    def _call(self, func, *args, **kwargs):
        out = io.StringIO()
        err = io.StringIO()
        with self._coordinator_adapter_tmux(), self._display_tmux():
            with redirect_stdout(out), redirect_stderr(err):
                rc = func(self.g, *args, **kwargs)
        return rc, out.getvalue(), err.getvalue()

    def _canonical(self):
        state, _ = GameSwitchStore(self.g.state_dir).canonical.load()
        return state


class TestCmdStart(CliCoordinatorTestBase):
    def test_start_launches_game_and_mirrors_current_game(self):
        rc, out, _err = self._call(cli.cmd_start, "nethack")
        self.assertEqual(rc, 0)
        state = self._canonical()
        self.assertEqual(state["phase"], "ready")
        self.assertEqual(state["active"]["game"], "nethack")
        self.assertEqual((self.g.state_dir / "current_game").read_text(encoding="utf-8").strip(), "nethack")
        self.assertIn("request_id=", out)

    def test_start_same_game_is_noop(self):
        self._call(cli.cmd_start, "nethack")
        rc, _out, _err = self._call(cli.cmd_start, "nethack")
        self.assertEqual(rc, 0)
        self.assertEqual(self._canonical()["active"]["generation"], 1)

    def test_start_other_game_requests_switch(self):
        self._call(cli.cmd_start, "nethack")
        (self.root / "config" / "games" / "robots.toml").write_text(
            '[game]\nname = "robots"\nadapter = "cli"\n\n[cli]\ncommand = "robots"\n',
            encoding="utf-8",
        )
        with mock.patch(
            "docich.adapters.cli_game.procs.which", return_value="/usr/games/robots"
        ):
            rc, _out, _err = self._call(cli.cmd_start, "robots")
        self.assertEqual(rc, 0)
        state = self._canonical()
        self.assertEqual(state["active"]["game"], "robots")
        self.assertEqual(state["active"]["generation"], 2)

    def test_start_unknown_game_returns_2(self):
        rc, _out, err = self._call(cli.cmd_start, "nosuchgame")
        self.assertEqual(rc, 2)
        self.assertIn("nosuchgame", err)

    def test_start_bad_request_id_raises_cli_error(self):
        with self.assertRaises(cli.CliError):
            self._call(cli.cmd_start, "nethack", request_id="not-a-uuid")

    def test_start_bad_timeout_raises_cli_error(self):
        with self.assertRaises(cli.CliError):
            self._call(cli.cmd_start, "nethack", timeout_s=0)


class TestCmdSwitch(CliCoordinatorTestBase):
    def test_switch_requires_existing_game_definition(self):
        rc, _out, _err = self._call(cli.cmd_switch, "nosuchgame")
        self.assertEqual(rc, 2)

    def test_switch_same_game_restarts_with_new_generation(self):
        self._call(cli.cmd_start, "nethack")
        rc, _out, _err = self._call(cli.cmd_switch, "nethack")
        self.assertEqual(rc, 0)
        self.assertEqual(self._canonical()["active"]["generation"], 2)

    def test_switch_respects_request_id(self):
        self._call(cli.cmd_start, "nethack")
        request_id = "12345678-1234-5678-1234-567812345678"
        rc, out, _err = self._call(cli.cmd_switch, "nethack", request_id=request_id)
        self.assertEqual(rc, 0)
        self.assertIn(request_id, out)


class TestCmdStop(CliCoordinatorTestBase):
    def test_stop_returns_to_idle_and_clears_mirror(self):
        self._call(cli.cmd_start, "nethack")
        rc, _out, _err = self._call(cli.cmd_stop)
        self.assertEqual(rc, 0)
        state = self._canonical()
        self.assertEqual(state["phase"], "idle")
        self.assertFalse((self.g.state_dir / "current_game").exists())

    def test_stop_when_idle_is_noop(self):
        rc, _out, _err = self._call(cli.cmd_stop)
        self.assertEqual(rc, 0)


class TestCmdRotate(CliCoordinatorTestBase):
    def _write_rotation(self, games):
        literal = ", ".join(f'"{name}"' for name in games)
        path = self.root / "docich.toml"
        path.write_text(f"[rotation]\ngames = [{literal}]\n", encoding="utf-8")
        return config.load_global(self.root, config_path=path)

    def test_rotate_executes_through_coordinator(self):
        g = self._write_rotation(["nethack"])
        out = io.StringIO()
        with self._coordinator_adapter_tmux(), self._display_tmux():
            with redirect_stdout(out):
                rc = cli.cmd_rotate(g, False)
        self.assertEqual(rc, 0)
        state, _ = GameSwitchStore(g.state_dir).canonical.load()
        self.assertEqual(state["active"]["game"], "nethack")


if __name__ == "__main__":
    unittest.main()
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
        tmux.has_session_named.return_value = False
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

    def test_start_switch_fallback_reuses_resolved_request_id(self):
        request_id = "12345678-1234-5678-1234-567812345678"
        start_result = cli.SwitchResult(
            request_id=request_id, operation="start", status="failed",
            target="robots", from_game="nethack", to_game="robots",
            generation=1, error_code=cli.ERROR_ALREADY_ACTIVE, detail=None,
            warnings=(), cleanup_pending=False, receipt=None,
        )
        with mock.patch("docich.cli._coordinator") as coordinator_mock, \
                mock.patch("docich.cli.Tmux"), \
                mock.patch("docich.cli._require_no_legacy_runtime"):
            coordinator_mock.return_value.start.return_value = start_result
            coordinator_mock.return_value.switch.return_value = cli.SwitchResult(
                request_id=request_id, operation="switch", status="succeeded",
                target="robots", from_game="nethack", to_game="robots",
                generation=2, error_code=None, detail=None,
                warnings=(), cleanup_pending=False, receipt=None,
            )
            rc = cli.cmd_start(self.g, "robots", request_id=request_id)
        self.assertEqual(rc, 0)
        switch_kwargs = coordinator_mock.return_value.switch.call_args
        self.assertEqual(switch_kwargs.kwargs["request_id"], request_id)

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


class TestCmdDown(CliCoordinatorTestBase):
    def test_down_does_not_kill_session_when_stop_is_busy(self):
        busy = cli.SwitchResult(
            request_id="r", operation="stop", status="busy",
            target=None, from_game=None, to_game=None,
            generation=None, error_code="busy", detail=None,
            warnings=(), cleanup_pending=False, receipt=None,
        )
        tmux = mock.Mock()
        with mock.patch("docich.cli._coordinator") as coordinator_mock, \
                mock.patch("docich.cli.Tmux", return_value=tmux), \
                mock.patch("docich.cli._require_no_legacy_runtime"):
            coordinator_mock.return_value.stop.return_value = busy
            rc = cli.cmd_down(self.g)
        self.assertEqual(rc, 1)
        tmux.kill_session.assert_not_called()

    def test_down_runs_stop_then_kills_session(self):
        self._call(cli.cmd_start, "nethack")
        tmux = mock.Mock()
        with self._coordinator_adapter_tmux(), \
                mock.patch("docich.cli.Tmux") as tmux_class:
            tmux_class.return_value = tmux
            out = io.StringIO()
            with redirect_stdout(out):
                rc = cli.cmd_down(self.g)
        self.assertEqual(rc, 0)
        tmux.kill_session.assert_called_once()
        self.assertEqual(self._canonical()["phase"], "idle")


class TestCompatLayer(CliCoordinatorTestBase):
    def test_obs_binds_active_generation_session(self):
        self._call(cli.cmd_start, "nethack")
        seen = {}
        real_env = dict(__import__("os").environ)

        def fake_make_adapter(g, game, **kwargs):
            seen["env"] = __import__("os").environ.get("DOCICH_GAME_SESSION")
            from docich.adapters import base

            class FakeAdapter:
                name = "cli"

                def observe(self):
                    return base.Observation(
                        game=game.name, title="", adapter="cli", ts=0.0, kind="text", text="screen"
                    )

            return FakeAdapter()

        try:
            with mock.patch("docich.cli.make_adapter", side_effect=fake_make_adapter):
                rc = cli.cmd_obs(self.g, "nethack")
            self.assertEqual(rc, 0)
            self.assertEqual(seen["env"], "docich-game-g1")
        finally:
            __import__("os").environ.clear()
            __import__("os").environ.update(real_env)

    def test_ra_cmd_uses_derived_port_for_retroarch_active(self):
        import uuid

        from docich.naming import runtime_names

        names = runtime_names(3)
        store = GameSwitchStore(self.g.state_dir)
        state, _ = store.canonical.load()
        state.update(
            {
                "phase": "ready",
                "active": {
                    "game": "hanjuku-hero",
                    "adapter": "retroarch",
                    "generation": 3,
                    "runtime_id": "g3-abcdef",
                    "lease_id": str(uuid.uuid4()),
                    "game_window": names.game_window,
                    "agent_window": names.agent_window,
                    "adapter_session": names.adapter_session,
                    "started_at": "2026-09-03T00:00:00Z",
                },
                "next_generation": 4,
            }
        )
        store.canonical.save(state)
        with mock.patch("docich.cli.send_ra_cmd", return_value="OK") as ra_mock:
            rc = cli.cmd_ra_cmd(self.g, ["GET_STATUS"])
        self.assertEqual(rc, 0)
        # 55355 + 3 (generation-derived)
        self.assertEqual(ra_mock.call_args.kwargs["port"], 55358)


class TestStartResendWhileSwitchInProgress(CliCoordinatorTestBase):
    def test_start_resend_converges_to_in_progress_switch(self):
        import uuid

        from docich.game_switch import GameSwitchLock

        self._call(cli.cmd_start, "nethack")
        request_id = "12345678-1234-5678-1234-567812345678"
        robots = self.root / "config" / "games" / "robots.toml"
        robots.write_text(
            '[game]\nname = "robots"\nadapter = "cli"\n\n[cli]\ncommand = "robots"\n',
            encoding="utf-8",
        )
        store = GameSwitchStore(self.g.state_dir)
        store.accept_request(request_id, "switch", "robots")
        held = GameSwitchLock(self.g.state_dir).acquire(exclusive=True)
        try:
            with self._coordinator_adapter_tmux(), self._display_tmux():
                rc = cli.cmd_start(self.g, "robots", request_id=request_id)
        finally:
            held.release()
        # in_progress (1), not request_conflict (2)
        self.assertEqual(rc, 1)

    def test_start_resend_converges_to_terminal_switch(self):
        import uuid

        from docich.game_switch import GameSwitchLock

        self._call(cli.cmd_start, "nethack")
        request_id = "12345678-1234-5678-1234-567812345678"
        robots = self.root / "config" / "games" / "robots.toml"
        robots.write_text(
            '[game]\nname = "robots"\nadapter = "cli"\n\n[cli]\ncommand = "robots"\n',
            encoding="utf-8",
        )
        with mock.patch(
            "docich.adapters.cli_game.procs.which", return_value="/usr/games/robots"
        ):
            with self._coordinator_adapter_tmux(), self._display_tmux():
                rc = cli.cmd_start(self.g, "robots", request_id=request_id)
        self.assertEqual(rc, 0)
        held = GameSwitchLock(self.g.state_dir).acquire(exclusive=True)
        try:
            with self._coordinator_adapter_tmux(), self._display_tmux():
                rc = cli.cmd_start(self.g, "robots", request_id=request_id)
        finally:
            held.release()
        self.assertEqual(rc, 0)


class TestNeedsWriteReads(CliCoordinatorTestBase):
    def _runtime(self, generation, game):
        from docich.naming import runtime_names

        names = runtime_names(generation)
        return {
            "game": game,
            "adapter": "cli",
            "generation": generation,
            "runtime_id": f"g{generation}-abcdef",
            "lease_id": "12345678-1234-5678-1234-567812345678",
            "game_window": names.game_window,
            "agent_window": names.agent_window,
            "adapter_session": names.adapter_session,
            "started_at": "2026-09-03T00:00:00Z",
        }

    def test_omitted_game_resolution_prefers_canonical_active(self):
        self._call(cli.cmd_start, "nethack")
        # stale mirror pointing elsewhere must not win over canonical active
        (self.g.state_dir / "current_game").write_text("robots\n", encoding="utf-8")
        seen = {}

        def fake_make_adapter(g, game, **kwargs):
            seen["game"] = game.name
            from docich.adapters import base

            class FakeAdapter:
                name = "cli"

                def observe(self):
                    return base.Observation(
                        game=game.name, title="", adapter="cli", ts=0.0, kind="text", text="s"
                    )

            return FakeAdapter()

        with mock.patch("docich.cli.make_adapter", side_effect=fake_make_adapter):
            rc = cli.cmd_obs(self.g, None)
        self.assertEqual(rc, 0)
        self.assertEqual(seen["game"], "nethack")

    def test_omitted_game_falls_back_to_mirror_without_canonical(self):
        state = cli.State(self.g)
        state.set_current_game("nethack")
        seen = {}

        def fake_make_adapter(g, game, **kwargs):
            seen["game"] = game.name
            from docich.adapters import base

            class FakeAdapter:
                name = "cli"

                def observe(self):
                    return base.Observation(
                        game=game.name, title="", adapter="cli", ts=0.0, kind="text", text="s"
                    )

            return FakeAdapter()

        with mock.patch("docich.cli.make_adapter", side_effect=fake_make_adapter):
            rc = cli.cmd_obs(self.g, None)
        self.assertEqual(rc, 0)
        self.assertEqual(seen["game"], "nethack")

    def test_rotate_dry_run_ignores_stale_mirror_when_canonical_idle(self):
        path = self.root / "docich.toml"
        path.write_text('[rotation]\ngames = ["nethack", "hanjuku-hero"]\n', encoding="utf-8")
        g = config.load_global(self.root, config_path=path)
        self._call(cli.cmd_start, "nethack")
        self._call(cli.cmd_stop)
        # canonical file exists (idle) but mirror is stale
        (self.g.state_dir / "current_game").write_text("hanjuku-hero\n", encoding="utf-8")
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cli.cmd_rotate(g, True)
        self.assertEqual(rc, 0)
        self.assertIn("nethack", out.getvalue())  # current=None -> games[0]

    def test_corrupt_canonical_is_cli_error(self):
        (self.g.state_dir).mkdir(parents=True, exist_ok=True)
        (self.g.state_dir / "game_switch.json").write_text("{broken", encoding="utf-8")
        with self.assertRaises(cli.CliError):
            cli.cmd_obs(self.g, None)

    def test_send_mismatched_game_is_rejected_when_canonical_exists(self):
        self._call(cli.cmd_start, "nethack")
        (self.root / "config" / "games" / "robots.toml").write_text(
            '[game]\nname = "robots"\nadapter = "cli"\n\n[cli]\ncommand = "robots"\n',
            encoding="utf-8",
        )
        with mock.patch("docich.cli.make_adapter") as make_mock:
            with self.assertRaises(cli.CliError):
                cli.cmd_send(self.g, "robots", '{"type":"wait","ms":1}')
            make_mock.assert_not_called()

    def test_stale_session_binding_is_cleared_on_mismatch(self):
        import os

        self._call(cli.cmd_start, "nethack")
        real_env = dict(os.environ)
        os.environ["DOCICH_GAME_SESSION"] = "docich-game-g1"
        try:
            cli._bind_active_cli_session(self.g, "something-else")
            self.assertNotIn("DOCICH_GAME_SESSION", os.environ)
        finally:
            os.environ.clear()
            os.environ.update(real_env)

    def test_obs_with_stale_mirror_and_idle_canonical_is_rejected(self):
        self._call(cli.cmd_start, "nethack")
        self._call(cli.cmd_stop)
        (self.g.state_dir / "current_game").write_text("nethack\n", encoding="utf-8")
        with self.assertRaises(cli.CliError):
            cli.cmd_obs(self.g, None)

    def test_ra_cmd_without_retroarch_active_is_rejected(self):
        self._call(cli.cmd_start, "nethack")
        with self.assertRaises(cli.CliError):
            cli.cmd_ra_cmd(self.g, ["GET_STATUS"])

    def test_ra_cmd_without_canonical_uses_fixed_port(self):
        with mock.patch("docich.cli.send_ra_cmd", return_value="OK") as ra_mock:
            rc = cli.cmd_ra_cmd(self.g, ["GET_STATUS"])
        self.assertEqual(rc, 0)
        self.assertEqual(ra_mock.call_args.kwargs["port"], 55355)


class TestLegacyMigration(CliCoordinatorTestBase):
    def _legacy_tmux(self, *, game_window=True, agent_window=True, game_session=True):
        tmux = mock.Mock()
        windows = set()
        if game_window:
            windows.add("game")
        if agent_window:
            windows.add("agent")
        sessions = {"docich-game"} if game_session else set()
        tmux.has_window.side_effect = lambda name: name in windows or name == "display"
        tmux.has_session_named.side_effect = lambda name: name in sessions
        tmux.kill_window.side_effect = lambda name: windows.discard(name)
        tmux.kill_session_named.side_effect = lambda name: sessions.discard(name)
        tmux.window_target_exists.side_effect = lambda target: target.split(":", 1)[-1] in windows
        tmux.session_target_exists.side_effect = lambda name: name in sessions
        return tmux

    def test_start_refuses_with_legacy_runtime_present(self):
        cli.State(self.g).set_current_game("nethack")
        tmux = self._legacy_tmux()
        with mock.patch("docich.cli.Tmux", return_value=tmux):
            with self.assertRaises(cli.CliError) as ctx:
                cli.cmd_start(self.g, "nethack")
        self.assertIn("migrate-legacy", str(ctx.exception))
        self.assertFalse((self.g.state_dir / "game_switch.json").exists())

    def test_stop_refuses_with_legacy_runtime_present(self):
        cli.State(self.g).set_current_game("nethack")
        tmux = self._legacy_tmux()
        with mock.patch("docich.cli.Tmux", return_value=tmux):
            with self.assertRaises(cli.CliError):
                cli.cmd_stop(self.g)

    def test_switch_refuses_with_legacy_runtime_present(self):
        cli.State(self.g).set_current_game("nethack")
        tmux = self._legacy_tmux()
        with mock.patch("docich.cli.Tmux", return_value=tmux):
            with self.assertRaises(cli.CliError):
                cli.cmd_switch(self.g, "nethack")

    def test_migrate_legacy_refuses_when_kill_does_not_take_effect(self):
        cli.State(self.g).set_current_game("nethack")
        tmux = self._legacy_tmux()
        # kill が記録だけされて実際は止めない tmux: target が残留する
        tmux.kill_window.side_effect = None
        tmux.kill_session_named.side_effect = None
        with mock.patch("docich.cli.Tmux", return_value=tmux):
            with self.assertRaises(cli.CliError):
                cli.cmd_migrate_legacy(self.g)
        # mirror は保持され、canonical は作成されない (fail-closed)
        self.assertEqual(cli.State(self.g).current_game(), "nethack")
        self.assertFalse((self.g.state_dir / "game_switch.json").exists())

    def test_migrate_legacy_refuses_when_verification_itself_fails(self):
        from docich.tmux import TmuxError

        cli.State(self.g).set_current_game("nethack")
        tmux = self._legacy_tmux()
        # 停止後の存在確認そのものが tmux error になる: 不在とみなさない
        tmux.window_target_exists.side_effect = TmuxError("socket error")
        with mock.patch("docich.cli.Tmux", return_value=tmux):
            with self.assertRaises(cli.CliError):
                cli.cmd_migrate_legacy(self.g)
        # mirror は保持され、canonical は作成されない (fail-closed)
        self.assertEqual(cli.State(self.g).current_game(), "nethack")
        self.assertFalse((self.g.state_dir / "game_switch.json").exists())

    def test_migrate_legacy_stops_fixed_runtime_and_clears_mirror(self):
        cli.State(self.g).set_current_game("nethack")
        tmux = self._legacy_tmux()
        with mock.patch("docich.cli.Tmux", return_value=tmux):
            rc = cli.cmd_migrate_legacy(self.g)
        self.assertEqual(rc, 0)
        tmux.kill_window.assert_any_call("agent")
        tmux.kill_window.assert_any_call("game")
        tmux.kill_session_named.assert_any_call("docich-game")
        self.assertIsNone(cli.State(self.g).current_game())
        state, _ = GameSwitchStore(self.g.state_dir).canonical.load()
        self.assertEqual(state["phase"], "idle")

    def test_migrate_legacy_is_noop_without_legacy(self):
        tmux = self._legacy_tmux(game_window=False, agent_window=False, game_session=False)
        with mock.patch("docich.cli.Tmux", return_value=tmux):
            rc = cli.cmd_migrate_legacy(self.g)
        self.assertEqual(rc, 0)
        tmux.kill_window.assert_not_called()
        tmux.kill_session_named.assert_not_called()

    def test_start_succeeds_after_migrate_legacy(self):
        cli.State(self.g).set_current_game("nethack")
        with mock.patch("docich.cli.Tmux", return_value=self._legacy_tmux()):
            cli.cmd_migrate_legacy(self.g)
        rc, _out, _err = self._call(cli.cmd_start, "nethack")
        self.assertEqual(rc, 0)
        self.assertEqual(self._canonical()["active"]["game"], "nethack")


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
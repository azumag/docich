import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config, state as state_mod  # noqa: E402
from docich.actions import Action  # noqa: E402
from docich.adapters import base  # noqa: E402
from docich.adapters import cli_game  # noqa: E402


class FakeTmux:
    def __init__(self, *, existing_sessions: set | None = None, capture_return: str = "screen text"):
        self.calls: list[tuple] = []
        self.sessions = set(existing_sessions or ())
        self.capture_return = capture_return

    def has_session_named(self, session):
        self.calls.append(("has_session_named", session))
        return session in self.sessions

    def new_game_session(self, session, cmd, cols, rows):
        self.calls.append(("new_game_session", session, list(cmd), cols, rows))
        self.sessions.add(session)

    def set_manual_size(self, session, cols, rows):
        self.calls.append(("set_manual_size", session, cols, rows))

    def capture_pane(self, session):
        self.calls.append(("capture_pane", session))
        return self.capture_return

    def send_keys(self, session, keys, literal=False):
        self.calls.append(("send_keys", session, list(keys), literal))

    def stop_game_session_named(self, session):
        # 実 Tmux.stop_game_session_named の game-only 制約を模す。
        if session != "docich-game" and not (
            session.startswith("docich-game-g") and session[len("docich-game-g"):].isdigit()
        ):
            raise ValueError("legacy game cleanup only accepts docich-game")
        self.calls.append(("stop_game_session_named", session))


class CliAdapterTestBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self._tmpdir.name)
        self.g = config.load_global(self.repo_root)
        # 別のテスト (cli.cmd_start 等) が世代別 session を os.environ へ
        # 束縛したままにし得るため、この file の期待値 (既定 docich-game) を
        # 固定する。同一起動プロセスで file をまたいで実行しても順序に依らない。
        self._env_patch = mock.patch.dict(
            "os.environ", {cli_game.RUNTIME_GAME_SESSION_ENV: cli_game.GAME_SESSION}
        )
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()
        self._tmpdir.cleanup()

    def _make_ctx(self, *, cli_raw: dict, tmux=None) -> base.AdapterContext:
        game = config.GameConfig(
            name="nethack",
            title="NetHack",
            adapter="cli",
            raw={"cli": cli_raw},
            agent=config.GameAgentConfig(),
            path=self.repo_root / "config" / "games" / "nethack.toml",
        )
        state = state_mod.State(self.g)
        return base.AdapterContext(g=self.g, game=game, state=state, tmux=tmux or FakeTmux(), xkit=None)


class TestPrepare(CliAdapterTestBase):
    def test_moon_buggy_launch_passes_only_whitelisted_ab_runtime_values(self):
        game = config.GameConfig(
            name="moon-buggy",
            title="Moon Buggy",
            adapter="cli",
            raw={},
            path=self.repo_root / "config" / "games" / "moon-buggy.toml",
        )
        values = {
            "DOCICH_TARGET_MATCHES": "2",
            "DOCICH_MOON_BUGGY_AB_STATE": "/runtime/moon_buggy_ab.json",
            "DOCICH_MOON_BUGGY_AB_ACTIVE": "/runtime/moon_buggy_ab_active.json",
            "DOCICH_MOON_BUGGY_AB_REQUEST": "12345678-1234-5678-1234-567812345678",
            "DOCICH_UNLISTED_SECRET": "must-not-pass",
        }
        with mock.patch.dict("os.environ", values), mock.patch(
            "docich.adapters.cli_game.procs.which", return_value="/usr/bin/env"
        ):
            command = cli_game._game_launch_command(
                self.g, game, ["/bin/sh", "games/cli-wrappers/moon-buggy_docich.sh"]
            )

        assignments = set(command[1:])
        self.assertIn(f"DOCICH_STATE_DIR={Path(self.g.state_dir).resolve()}", assignments)
        for name in values:
            if name == "DOCICH_UNLISTED_SECRET":
                self.assertNotIn(f"{name}=must-not-pass", assignments)
            else:
                self.assertIn(f"{name}={values[name]}", assignments)

    def test_which_resolves_command_head_to_absolute_path(self):
        tmux = FakeTmux()
        ctx = self._make_ctx(cli_raw={"command": "nethack"}, tmux=tmux)
        adapter = cli_game.CliGameAdapter(ctx)
        with mock.patch("docich.adapters.cli_game.procs.which", return_value="/usr/games/nethack"):
            adapter.prepare()

        new_session_calls = [c for c in tmux.calls if c[0] == "new_game_session"]
        self.assertEqual(len(new_session_calls), 1)
        _, session, cmd, cols, rows = new_session_calls[0]
        self.assertEqual(session, cli_game.GAME_SESSION)
        self.assertEqual(cmd, ["/usr/games/nethack"])
        self.assertEqual((cols, rows), (80, 24))

    def test_which_missing_raises_adapter_error(self):
        ctx = self._make_ctx(cli_raw={"command": "no-such-binary"})
        adapter = cli_game.CliGameAdapter(ctx)
        with mock.patch("docich.adapters.cli_game.procs.which", return_value=None):
            with self.assertRaises(base.AdapterError):
                adapter.prepare()

    def test_prepare_is_idempotent_when_session_exists(self):
        tmux = FakeTmux(existing_sessions={cli_game.GAME_SESSION})
        ctx = self._make_ctx(cli_raw={"command": "nethack"}, tmux=tmux)
        adapter = cli_game.CliGameAdapter(ctx)
        with mock.patch("docich.adapters.cli_game.procs.which", return_value="/usr/games/nethack"):
            adapter.prepare()

        self.assertFalse(any(c[0] == "new_game_session" for c in tmux.calls))
        self.assertFalse(any(c[0] == "set_manual_size" for c in tmux.calls))

    def test_command_list_form_is_used_as_is(self):
        tmux = FakeTmux()
        ctx = self._make_ctx(cli_raw={"command": ["python3", "-m", "game"]}, tmux=tmux)
        adapter = cli_game.CliGameAdapter(ctx)
        with mock.patch("docich.adapters.cli_game.procs.which", return_value="/usr/bin/python3"):
            adapter.prepare()
        _, _session, cmd, _cols, _rows = [c for c in tmux.calls if c[0] == "new_game_session"][0]
        self.assertEqual(cmd, ["/usr/bin/python3", "-m", "game"])

    def test_missing_command_raises(self):
        ctx = self._make_ctx(cli_raw={})
        adapter = cli_game.CliGameAdapter(ctx)
        with self.assertRaises(base.AdapterError):
            adapter.prepare()


class TestCommand(CliAdapterTestBase):
    def test_xterm_args(self):
        ctx = self._make_ctx(cli_raw={"command": "nethack", "cols": 80, "rows": 24, "font_size": 18})
        adapter = cli_game.CliGameAdapter(ctx)
        cmd = adapter.command()
        self.assertEqual(
            cmd,
            [
                "xterm", "-fa", "monospace", "-fs", "18",
                "-bg", "black", "-fg", "grey90",
                "-geometry", "80x24+0+0",
                "-T", "docich-nethack",
                "-e", "tmux", "attach-session", "-r", "-t", "docich-game",
            ],
        )

    def test_xterm_args_use_custom_font_and_size(self):
        ctx = self._make_ctx(cli_raw={"command": "nethack", "cols": 100, "rows": 30, "font": "Courier", "font_size": 14})
        adapter = cli_game.CliGameAdapter(ctx)
        cmd = adapter.command()
        self.assertIn("Courier", cmd)
        self.assertIn("14", cmd)
        self.assertIn("100x30+0+0", cmd)

class TestObserve(CliAdapterTestBase):
    def test_observe_returns_capture_pane_text(self):
        tmux = FakeTmux(capture_return="--More--")
        ctx = self._make_ctx(cli_raw={"command": "nethack"}, tmux=tmux)
        adapter = cli_game.CliGameAdapter(ctx)
        obs = adapter.observe()
        self.assertEqual(obs.kind, "text")
        self.assertEqual(obs.text, "--More--")
        self.assertEqual(obs.adapter, "cli")
        self.assertEqual(obs.meta, {})

    def test_observe_empty_capture_sets_warning_meta(self):
        tmux = FakeTmux(capture_return="")
        ctx = self._make_ctx(cli_raw={"command": "nethack"}, tmux=tmux)
        adapter = cli_game.CliGameAdapter(ctx)
        obs = adapter.observe()
        self.assertEqual(obs.text, "")
        self.assertIn("warning", obs.meta)
        self.assertIn("capture が空です", obs.meta["warning"])


class TestAct(CliAdapterTestBase):
    def test_text_action_sends_literal_keys(self):
        tmux = FakeTmux()
        ctx = self._make_ctx(cli_raw={"command": "nethack"}, tmux=tmux)
        adapter = cli_game.CliGameAdapter(ctx)
        adapter.act(Action(type="text", text="hjkl"))
        self.assertIn(("send_keys", "docich-game", ["hjkl"], True), tmux.calls)

    def test_special_action_sends_non_literal_key(self):
        tmux = FakeTmux()
        ctx = self._make_ctx(cli_raw={"command": "nethack"}, tmux=tmux)
        adapter = cli_game.CliGameAdapter(ctx)
        adapter.act(Action(type="special", key="Escape"))
        self.assertIn(("send_keys", "docich-game", ["Escape"], False), tmux.calls)

    def test_key_action_sends_non_literal_keys(self):
        tmux = FakeTmux()
        ctx = self._make_ctx(cli_raw={"command": "nethack"}, tmux=tmux)
        adapter = cli_game.CliGameAdapter(ctx)
        adapter.act(Action(type="key", keys=["C-c", "Enter"]))
        self.assertIn(("send_keys", "docich-game", ["C-c", "Enter"], False), tmux.calls)

    def test_wait_action_is_noop(self):
        tmux = FakeTmux()
        ctx = self._make_ctx(cli_raw={"command": "nethack"}, tmux=tmux)
        adapter = cli_game.CliGameAdapter(ctx)
        adapter.act(Action(type="wait", ms=100))
        self.assertEqual(tmux.calls, [])

    def test_pad_and_mouse_raise(self):
        ctx = self._make_ctx(cli_raw={"command": "nethack"})
        adapter = cli_game.CliGameAdapter(ctx)
        with self.assertRaises(base.AdapterError):
            adapter.act(Action(type="pad", buttons=["a"]))
        with self.assertRaises(base.AdapterError):
            adapter.act(Action(type="mouse", x=1, y=2))


class TestCleanup(CliAdapterTestBase):
    def test_cleanup_kills_game_session(self):
        tmux = FakeTmux()
        ctx = self._make_ctx(cli_raw={"command": "nethack"}, tmux=tmux)
        adapter = cli_game.CliGameAdapter(ctx)
        adapter.cleanup()
        self.assertIn(("stop_game_session_named", "docich-game"), tmux.calls)

    def test_cleanup_refuses_shared_session(self):
        # Issue #219: DOCICH_GAME_SESSION=docich の混入があっても共有 session は殺さない。
        tmux = FakeTmux()
        ctx = self._make_ctx(cli_raw={"command": "nethack"}, tmux=tmux)
        adapter = cli_game.CliGameAdapter(ctx)
        with mock.patch.dict("os.environ", {"DOCICH_GAME_SESSION": "docich"}):
            with self.assertRaises(ValueError):
                adapter.cleanup()
        self.assertNotIn(("stop_game_session_named", "docich"), tmux.calls)


class TestCellAspect(CliAdapterTestBase):
    def _game(self, value):
        return config.GameConfig(
            name="pacman4console",
            title="Pac-Man",
            adapter="cli",
            raw={"cli": {"command": "pacman4console", "cell_aspect": value}},
            agent=config.GameAgentConfig(),
            path=self.repo_root / "config" / "games" / "pacman4console.toml",
        )

    def test_unset_keeps_native_window(self):
        game = config.GameConfig(
            name="nethack", title="NetHack", adapter="cli",
            raw={"cli": {"command": "nethack"}},
            agent=config.GameAgentConfig(),
            path=self.repo_root / "config" / "games" / "nethack.toml",
        )
        self.assertIsNone(cli_game.cli_cell_aspect(game))

    def test_valid_value_is_normalized(self):
        self.assertEqual(cli_game.cli_cell_aspect(self._game(" 1:2 ")), "1:2")

    def test_identity_ratio_disables_correction(self):
        self.assertIsNone(cli_game.cli_cell_aspect(self._game("1:1")))

    def test_invalid_values_fail_closed(self):
        for value in ("", "1", "1:0", "1:5", 2, "abc"):
            with self.subTest(value=value):
                with self.assertRaises(base.AdapterError):
                    cli_game.cli_cell_aspect(self._game(value))


class TestRobotsCatalog(unittest.TestCase):
    def test_robots_uses_cli_adapter_and_bsdgames_command(self):
        repo_root = Path(__file__).resolve().parents[1]
        g = config.load_global(repo_root)
        game = config.load_game(g, "robots")
        self.assertEqual(game.title, "Robots")
        self.assertEqual(game.adapter, "cli")
        self.assertEqual(game.raw["cli"]["command"], "robots")
        self.assertTrue(game.agent.enabled)
        self.assertEqual(game.agent.brain, "resolver")
        self.assertEqual(game.agent.interval_ms, 800)

    def test_setup_installs_bsdgames(self):
        repo_root = Path(__file__).resolve().parents[1]
        setup = (repo_root / "scripts" / "setup_ubuntu_arm.sh").read_text(encoding="utf-8")
        self.assertIn("  bsdgames", setup)


if __name__ == "__main__":
    unittest.main()

"""Focused regressions for P2 CLI runtime/agent binding hardening."""

import os
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402
from docich.actions import Action  # noqa: E402
from docich.adapters import AdapterError  # noqa: E402
from docich.adapters import cli_game  # noqa: E402
from docich.game_switch import DeadlineExceededError, RuntimeSpec  # noqa: E402
from docich.naming import runtime_names  # noqa: E402


def _spec(runtime_dir: Path) -> RuntimeSpec:
    names = runtime_names(1)
    return RuntimeSpec(
        game="nethack",
        adapter="cli",
        generation=1,
        runtime_id="g1-abcdef",
        lease_id=str(uuid.uuid4()),
        runtime_dir=runtime_dir,
        game_window=names.game_window,
        agent_window=names.agent_window,
        adapter_session=names.adapter_session,
    )


def _coordinator_adapter(root: Path, *, agent_enabled: bool = True):
    g = config.load_global(root)
    game_dir = root / "config" / "games"
    game_dir.mkdir(parents=True, exist_ok=True)
    agent = (
        '[agent]\nenabled = true\nbrain = "command"\ncommand = "true"\n\n'
        if agent_enabled
        else ""
    )
    (game_dir / "nethack.toml").write_text(
        '[game]\nname = "nethack"\nadapter = "cli"\ntitle = "NetHack"\n\n'
        f"{agent}"
        '[cli]\ncommand = "nethack"\n',
        encoding="utf-8",
    )
    spec = _spec(root / "run" / "runtimes" / "g1-abcdef")
    adapter = cli_game.CliCoordinatorAdapter(g, config.load_game(g, "nethack"), spec)
    return g, spec, adapter


class TestCliRuntimeSession(unittest.TestCase):
    def test_runtime_session_override_is_used(self):
        with mock.patch.dict(
            os.environ,
            {cli_game.RUNTIME_GAME_SESSION_ENV: "docich-game-g7"},
            clear=False,
        ):
            self.assertEqual(cli_game.cli_game_session(), "docich-game-g7")

    def test_invalid_runtime_session_override_fails_closed(self):
        with mock.patch.dict(
            os.environ,
            {cli_game.RUNTIME_GAME_SESSION_ENV: "docich-game;kill-server"},
            clear=False,
        ):
            with self.assertRaises(AdapterError):
                cli_game.cli_game_session()

    def test_legacy_cli_adapter_observe_and_act_use_runtime_override(self):
        game = SimpleNamespace(name="nethack", title="NetHack")
        tmux = mock.Mock()
        tmux.capture_pane.return_value = "screen"
        adapter = cli_game.CliGameAdapter(SimpleNamespace(game=game, tmux=tmux))
        with mock.patch.dict(
            os.environ,
            {cli_game.RUNTIME_GAME_SESSION_ENV: "docich-game-g7"},
            clear=False,
        ):
            observation = adapter.observe()
            adapter.act(Action(type="text", text="h"))
        self.assertEqual(observation.text, "screen")
        tmux.capture_pane.assert_called_once_with("docich-game-g7")
        tmux.send_keys.assert_called_once_with("docich-game-g7", ["h"], literal=True)


class TestCoordinatorAgentBinding(unittest.TestCase):
    def test_start_agent_exports_generation_specific_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _g, spec, adapter = _coordinator_adapter(root)
            tmux = mock.Mock()
            tmux.window_target_exists.return_value = False
            adapter.tmux = tmux

            adapter.start_agent(time.monotonic() + 60.0, None)

            tmux.create_window_owned.assert_called_once()
            _args, kwargs = tmux.create_window_owned.call_args
            self.assertEqual(
                kwargs["env"],
                {cli_game.RUNTIME_GAME_SESSION_ENV: spec.adapter_session},
            )

    def test_start_agent_cancelled_after_probe_does_not_create(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _g, _spec_value, adapter = _coordinator_adapter(root)
            cancel = threading.Event()
            tmux = mock.Mock()

            def probe(_target):
                cancel.set()
                return False

            tmux.window_target_exists.side_effect = probe
            adapter.tmux = tmux
            with self.assertRaises(DeadlineExceededError):
                adapter.start_agent(time.monotonic() + 60.0, cancel)
            tmux.create_window_owned.assert_not_called()

    def test_materialize_cancelled_after_session_probe_does_not_create(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _g, _spec_value, adapter = _coordinator_adapter(root, agent_enabled=False)
            cancel = threading.Event()
            tmux = mock.Mock()

            def probe(_target):
                cancel.set()
                return False

            tmux.session_target_exists.side_effect = probe
            adapter.tmux = tmux
            with self.assertRaises(DeadlineExceededError):
                adapter.materialize_runtime(time.monotonic() + 60.0, cancel)
            tmux.create_game_session_owned.assert_not_called()
            tmux.create_window_owned.assert_not_called()

    def test_preflight_rejects_missing_xterm_before_quiesce(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _g, _spec_value, adapter = _coordinator_adapter(root, agent_enabled=False)

            def which(name):
                return "/usr/games/nethack" if name == "nethack" else None

            with mock.patch("docich.adapters.cli_game.procs.which", side_effect=which):
                with self.assertRaises(AdapterError):
                    adapter.preflight(time.monotonic() + 60.0, None)


if __name__ == "__main__":
    unittest.main()

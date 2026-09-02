"""Focused regressions for P2 CLI runtime/agent binding hardening."""

import os
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402
from docich.adapters import AdapterError  # noqa: E402
from docich.adapters import cli_game  # noqa: E402
from docich.game_switch import RuntimeSpec  # noqa: E402
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


class TestCoordinatorAgentBinding(unittest.TestCase):
    def test_start_agent_exports_generation_specific_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            g = config.load_global(root)
            game_dir = root / "config" / "games"
            game_dir.mkdir(parents=True)
            (game_dir / "nethack.toml").write_text(
                '[game]\nname = "nethack"\nadapter = "cli"\n\n'
                '[agent]\nenabled = true\nbrain = "command"\ncommand = "true"\n\n'
                '[cli]\ncommand = "nethack"\n',
                encoding="utf-8",
            )
            spec = _spec(root / "run" / "runtimes" / "g1-abcdef")
            adapter = cli_game.CliCoordinatorAdapter(
                g, config.load_game(g, "nethack"), spec
            )
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


if __name__ == "__main__":
    unittest.main()

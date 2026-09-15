import json
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402
from docich.adapters import AdapterError, make_coordinator_adapter  # noqa: E402
from docich.adapters import cli_game, nethack as nethack_adapter  # noqa: E402
from docich.game_switch import ReadinessTimeoutError, RuntimeSpec  # noqa: E402
from docich.naming import runtime_names  # noqa: E402
from docich.tmux import TmuxOwnership  # noqa: E402


def _spec(root: Path) -> RuntimeSpec:
    names = runtime_names(1)
    return RuntimeSpec(
        game="nethack",
        adapter="cli",
        generation=1,
        runtime_id="g1-abcdef",
        lease_id=str(uuid.uuid4()),
        runtime_dir=root / "run" / "runtimes" / "g1-abcdef",
        game_window=names.game_window,
        agent_window=names.agent_window,
        adapter_session=names.adapter_session,
    )


def _root(
    save_dir: Path,
    *,
    player_name: str = "docich",
    command: str = "nethack",
    persistent_run: bool = True,
) -> Path:
    root = save_dir.parent / "repo"
    (root / "config" / "games").mkdir(parents=True, exist_ok=True)
    (root / "config" / "docich.toml").write_text(
        '[paths]\nstate_dir = "run"\ngames_dir = "config/games"\n',
        encoding="utf-8",
    )
    persistent = "true" if persistent_run else "false"
    (root / "config" / "games" / "nethack.toml").write_text(
        '[game]\nname = "nethack"\ntitle = "NetHack"\nadapter = "cli"\n\n'
        f'[cli]\ncommand = "{command}"\ncols = 80\nrows = 24\n\n'
        '[nethack]\n'
        f'persistent_run = {persistent}\n'
        f'player_name = "{player_name}"\n'
        f'save_dir = "{save_dir}"\n',
        encoding="utf-8",
    )
    return root


class FakeTmux:
    def __init__(self, spec: RuntimeSpec, save_dir: Path, *, create_save: bool = True):
        self.spec = spec
        self.save_dir = save_dir
        self.process_alive = True
        self.create_save = create_save
        self.calls = []
        self.process_name = "nethack"

    def session_target_exists(self, session):
        self.calls.append(("session_target_exists", session))
        return True

    def read_session_ownership(self, session):
        self.calls.append(("read_session_ownership", session))
        return TmuxOwnership(
            runtime_id=self.spec.runtime_id,
            generation=self.spec.generation,
            role="adapter",
        )

    def list_windows(self):
        names = [self.spec.game_window]
        if self.process_alive:
            names.insert(0, self.process_name)
        return names

    def send_keys(self, target, keys, literal=False):
        self.calls.append(("send_keys", target, list(keys), literal))
        if keys == ["S"]:
            if self.create_save:
                (self.save_dir / "1000docich").write_bytes(b"save")
            self.process_alive = False

    def window_target_exists(self, target, *, strict=False):
        self.calls.append(("window_target_exists", target, strict))
        return self.process_alive


class TestNethackCoordinatorAdapter(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.base = Path(self.tempdir.name)
        self.save_dir = self.base / "save"
        self.save_dir.mkdir()
        self.root = _root(self.save_dir)
        self.g = config.load_global(self.root)
        self.spec = _spec(self.root)
        self.game = config.load_game(self.g, "nethack")

    def tearDown(self):
        self.tempdir.cleanup()

    def adapter(self):
        return nethack_adapter.NethackCoordinatorAdapter(self.g, self.game, self.spec)

    def test_factory_specializes_only_opted_in_cli_nethack(self):
        adapter = make_coordinator_adapter(self.g, self.spec)
        self.assertIsInstance(adapter, nethack_adapter.NethackCoordinatorAdapter)
        self.assertTrue(adapter.requires_round_boundary)
        self.assertTrue(callable(adapter.request_round_boundary))

        root = _root(self.save_dir, persistent_run=False)
        g = config.load_global(root)
        generic = make_coordinator_adapter(g, _spec(root))
        self.assertIsInstance(generic, cli_game.CliCoordinatorAdapter)
        self.assertNotIsInstance(generic, nethack_adapter.NethackCoordinatorAdapter)

    def test_non_boolean_persistent_run_is_rejected(self):
        path = self.root / "config" / "games" / "nethack.toml"
        path.write_text(
            '[game]\nname = "nethack"\ntitle = "NetHack"\nadapter = "cli"\n\n'
            '[cli]\ncommand = "nethack"\n\n'
            '[nethack]\npersistent_run = "yes"\n',
            encoding="utf-8",
        )
        g = config.load_global(self.root)
        with self.assertRaises(AdapterError):
            make_coordinator_adapter(g, self.spec)

    def test_game_command_pins_player_name(self):
        adapter = self.adapter()
        with mock.patch("docich.adapters.cli_game.procs.which", return_value="/usr/games/nethack"):
            self.assertEqual(
                adapter._game_command(),
                ["/usr/games/nethack", "-u", "docich"],
            )

    def test_ambiguous_command_line_player_name_is_rejected(self):
        root = _root(self.save_dir, command="nethack -u someone")
        g = config.load_global(root)
        game = config.load_game(g, "nethack")
        adapter = nethack_adapter.NethackCoordinatorAdapter(g, game, _spec(root))
        with mock.patch("docich.adapters.cli_game.procs.which", return_value="/usr/games/nethack"):
            with self.assertRaises(AdapterError):
                adapter._game_command()

    def test_wizard_and_explore_modes_are_rejected(self):
        for flag in ("-D", "-X"):
            with self.subTest(flag=flag):
                root = _root(self.save_dir, command=f"nethack {flag}")
                g = config.load_global(root)
                game = config.load_game(g, "nethack")
                adapter = nethack_adapter.NethackCoordinatorAdapter(g, game, _spec(root))
                with mock.patch("docich.adapters.cli_game.procs.which", return_value="/usr/games/nethack"):
                    with self.assertRaises(AdapterError):
                        adapter._game_command()

    def test_invalid_player_name_and_relative_save_dir_are_rejected(self):
        root = _root(self.save_dir, player_name="bad-name")
        g = config.load_global(root)
        game = config.load_game(g, "nethack")
        with self.assertRaises(AdapterError):
            nethack_adapter.NethackCoordinatorAdapter(g, game, _spec(root))

        path = root / "config" / "games" / "nethack.toml"
        path.write_text(
            '[game]\nname = "nethack"\ntitle = "NetHack"\nadapter = "cli"\n\n'
            '[cli]\ncommand = "nethack"\n\n'
            '[nethack]\npersistent_run = true\nplayer_name = "docich"\n'
            'save_dir = "relative/save"\n',
            encoding="utf-8",
        )
        g = config.load_global(root)
        with self.assertRaises(AdapterError):
            nethack_adapter.NethackCoordinatorAdapter(
                g, config.load_game(g, "nethack"), _spec(root)
            )

    def test_save_boundary_sends_normal_save_and_requires_durable_file(self):
        adapter = self.adapter()
        tmux = FakeTmux(self.spec, self.save_dir)
        adapter.tmux = tmux
        request_id = str(uuid.uuid4())

        adapter.request_round_boundary(request_id, time.monotonic() + 1.0, None)

        target = f"{self.spec.adapter_session}:nethack"
        self.assertIn(("send_keys", target, ["Escape"], False), tmux.calls)
        self.assertIn(("send_keys", target, ["S"], True), tmux.calls)
        marker = json.loads(
            (self.spec.runtime_dir / nethack_adapter.BOUNDARY_RESULT_FILENAME).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(marker["outcome"], "suspended")
        self.assertEqual(marker["player_name"], "docich")
        self.assertEqual(marker["save_file"], "1000docich")
        self.assertEqual(marker["request_id"], request_id)

    def test_game_already_ended_is_boundary_but_not_fake_suspension(self):
        adapter = self.adapter()
        tmux = FakeTmux(self.spec, self.save_dir)
        tmux.process_alive = False
        adapter.tmux = tmux
        request_id = str(uuid.uuid4())

        adapter.request_round_boundary(request_id, time.monotonic() + 1.0, None)

        self.assertFalse(any(call[0] == "send_keys" for call in tmux.calls))
        marker = json.loads(
            (self.spec.runtime_dir / nethack_adapter.BOUNDARY_RESULT_FILENAME).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(marker["outcome"], "ended")
        self.assertNotIn("save_file", marker)

    def test_game_already_saved_is_recognized_as_suspended(self):
        adapter = self.adapter()
        (self.save_dir / "1000docich").write_bytes(b"existing-save")
        tmux = FakeTmux(self.spec, self.save_dir)
        tmux.process_alive = False
        adapter.tmux = tmux

        adapter.request_round_boundary(str(uuid.uuid4()), time.monotonic() + 1.0, None)

        marker = json.loads(
            (self.spec.runtime_dir / nethack_adapter.BOUNDARY_RESULT_FILENAME).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(marker["outcome"], "suspended")
        self.assertEqual(marker["save_file"], "1000docich")

    def test_process_exit_without_fresh_save_fails_closed(self):
        adapter = self.adapter()
        (self.save_dir / "1000docich").write_bytes(b"stale-save")
        tmux = FakeTmux(self.spec, self.save_dir, create_save=False)
        adapter.tmux = tmux
        with mock.patch("docich.adapters.nethack.time.sleep", return_value=None):
            with self.assertRaises(ReadinessTimeoutError):
                adapter.request_round_boundary(
                    str(uuid.uuid4()), time.monotonic() + 0.02, None
                )
        self.assertFalse(
            (self.spec.runtime_dir / nethack_adapter.BOUNDARY_RESULT_FILENAME).exists()
        )

    def test_empty_or_ambiguous_window_listing_fails_closed(self):
        adapter = self.adapter()
        tmux = FakeTmux(self.spec, self.save_dir)
        tmux.list_windows = lambda: []
        adapter.tmux = tmux
        with self.assertRaises(AdapterError):
            adapter.request_round_boundary(
                str(uuid.uuid4()), time.monotonic() + 1.0, None
            )

        tmux.list_windows = lambda: ["nethack", "unexpected", self.spec.game_window]
        with self.assertRaises(AdapterError):
            adapter.request_round_boundary(
                str(uuid.uuid4()), time.monotonic() + 1.0, None
            )


if __name__ == "__main__":
    unittest.main()

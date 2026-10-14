import json
import os
import subprocess
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
from docich.nethack_tiles_supervisor import (  # noqa: E402
    BROWSER_WINDOW_WAIT_S,
    PRESENTATION_WINDOW_WAIT_S,
    presentation_window_pattern,
)
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
    presentation_mode: str | None = None,
) -> Path:
    root = save_dir.parent / "repo"
    (root / "config" / "games").mkdir(parents=True, exist_ok=True)
    (root / "config" / "docich.toml").write_text(
        '[paths]\nstate_dir = "run"\ngames_dir = "config/games"\n',
        encoding="utf-8",
    )
    persistent = "true" if persistent_run else "false"
    presentation = (
        f'\n[nethack.presentation]\nmode = "{presentation_mode}"\n'
        if presentation_mode is not None
        else ""
    )
    (root / "config" / "games" / "nethack.toml").write_text(
        '[game]\nname = "nethack"\ntitle = "NetHack"\nadapter = "cli"\n\n'
        f'[cli]\ncommand = "{command}"\ncols = 80\nrows = 24\n\n'
        '[nethack]\n'
        f'persistent_run = {persistent}\n'
        f'player_name = "{player_name}"\n'
        f'save_dir = "{save_dir}"\n'
        f'{presentation}',
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
        self.pane_text = ""

    def session_target_exists(self, session):
        self.calls.append(("session_target_exists", session))
        return True

    def capture_pane(self, target):
        self.calls.append(("capture_pane", target))
        return self.pane_text

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

    def test_presentation_defaults_to_native_tty(self):
        adapter = self.adapter()
        self.assertEqual(adapter.presentation_mode, "tty")
        with mock.patch("docich.adapters.cli_game.procs.which", return_value="/usr/bin/xterm"):
            command = adapter._xterm_command()
        self.assertEqual(command[0], "/usr/bin/xterm")
        self.assertNotIn("nethack_tiles_supervisor.py", " ".join(command))

    def test_tiles_mode_connects_generation_scoped_supervisor(self):
        root = _root(self.save_dir, presentation_mode="tiles")
        g = config.load_global(root)
        g.display.viewport_x = 0
        g.display.viewport_y = 90
        g.display.viewport_width = 960
        g.display.viewport_height = 540
        adapter = nethack_adapter.NethackCoordinatorAdapter(
            g, config.load_game(g, "nethack"), _spec(root)
        )
        command = adapter._xterm_command()
        joined = " ".join(command)
        self.assertIn("nethack_tiles_supervisor.py", joined)
        self.assertIn("--rebind-window", command)
        self.assertIn("--runtime-id", command)
        self.assertIn(adapter.spec.runtime_id, command)
        self.assertIn("--adapter-session", command)
        self.assertIn(adapter.spec.adapter_session, command)
        self.assertEqual(command[command.index("--title") + 1],
                         f"docich-present-{adapter.spec.runtime_id}")
        self.assertEqual(
            command[command.index("--window-pattern") + 1],
            presentation_window_pattern(f"docich-present-{adapter.spec.runtime_id}"),
        )
        self.assertGreater(PRESENTATION_WINDOW_WAIT_S, BROWSER_WINDOW_WAIT_S)
        self.assertEqual(
            command[command.index("--viewer-wait-sec") + 1],
            str(int(PRESENTATION_WINDOW_WAIT_S)),
        )

    def test_tiles_mode_requires_configured_viewport(self):
        root = _root(self.save_dir, presentation_mode="tiles")
        g = config.load_global(root)
        adapter = nethack_adapter.NethackCoordinatorAdapter(
            g, config.load_game(g, "nethack"), _spec(root)
        )
        with self.assertRaises(AdapterError):
            adapter._xterm_command()

    def test_missing_browser_reaches_tty_fallback_readiness(self):
        root = _root(self.save_dir, presentation_mode="tiles")
        g = config.load_global(root)
        g.display.viewport_width = 960
        g.display.viewport_height = 540
        spec = _spec(root)
        adapter = nethack_adapter.NethackCoordinatorAdapter(
            g, config.load_game(g, "nethack"), spec
        )
        with (
            mock.patch.object(cli_game.CliCoordinatorAdapter, "preflight"),
            mock.patch.object(adapter, "_check_active"),
            mock.patch("docich.nethack_tiles_supervisor.browser_binary", return_value=None),
        ):
            adapter.preflight(time.monotonic() + 5, None)

        spec.runtime_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": 1,
            "status": "fallback_tty",
            "mode": "tty",
            "runtime_id": spec.runtime_id,
            "generation": spec.generation,
            "adapter_session": spec.adapter_session,
            "game_window": spec.game_window,
            "presentation_epoch": "p-" + "a" * 32,
            "tty_pid": 12345,
        }
        adapter._tiles_manifest_path().write_text(json.dumps(manifest), encoding="utf-8")
        tmux = mock.Mock()
        tmux.session_target_exists.return_value = True
        tmux.window_target_exists.return_value = True
        tmux.pane_states_checked.return_value = []
        adapter.tmux = tmux
        adapter._game_window_target = mock.Mock(return_value="docich-game-g1:game-g1")
        adapter._verify_session_ownership = mock.Mock()
        adapter._verify_window_ownership = mock.Mock()
        with (
            mock.patch.object(adapter, "_check_active"),
            mock.patch("docich.adapters.nethack.XKit") as xkit,
        ):
            xkit.return_value.find_window.return_value = "0x123"
            adapter.readiness(time.monotonic() + 5, None)
        xkit.return_value.find_window.assert_called_once()

    def test_cleanup_resumes_dead_supervisor_from_owned_manifest(self):
        if not Path("/proc").is_dir():
            self.skipTest("manifest process-group recovery requires procfs")
        root = _root(self.save_dir, presentation_mode="tiles")
        g = config.load_global(root)
        spec = _spec(root)
        adapter = nethack_adapter.NethackCoordinatorAdapter(
            g, config.load_game(g, "nethack"), spec
        )
        spec.runtime_dir.mkdir(parents=True, exist_ok=True)
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            start_ticks = nethack_adapter._proc_start_ticks(process.pid)
            owner_ticks = nethack_adapter._proc_start_ticks(os.getpid())
            self.assertIsNotNone(start_ticks)
            self.assertIsNotNone(owner_ticks)
            manifest = {
                "schema_version": 1,
                "status": "cleanup_failed",
                "mode": "tiles",
                "runtime_id": spec.runtime_id,
                "generation": spec.generation,
                "adapter_session": spec.adapter_session,
                "game_window": spec.game_window,
                "presentation_epoch": "p-" + "b" * 32,
                "owner_pid": os.getpid(),
                "owner_start_ticks": owner_ticks + 1,
                "frame_port": 43210,
                "browser_pid": process.pid,
                "browser_start_ticks": start_ticks,
                "browser_pgid": process.pid,
                "tty_pid": None,
                "tty_start_ticks": None,
                "tty_pgid": None,
                "cleanup_complete": False,
            }
            adapter._tiles_manifest_path().write_text(json.dumps(manifest), encoding="utf-8")
            adapter._presentation_path().write_text(
                json.dumps({"status": "stopped"}), encoding="utf-8"
            )
            with (
                mock.patch.object(cli_game.CliCoordinatorAdapter, "cleanup_runtime"),
                mock.patch.object(adapter, "_check_active"),
            ):
                adapter.cleanup_runtime(time.monotonic() + 5, None)
            process.wait(timeout=1)
            recovered = json.loads(
                adapter._tiles_manifest_path().read_text(encoding="utf-8")
            )
            self.assertEqual(recovered["status"], "stopped")
            self.assertTrue(recovered["cleanup_complete"])
            self.assertIsNone(recovered["browser_pid"])
            self.assertIsNone(recovered["frame_port"])
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=3)

    def test_adapter_recovery_uses_recorded_process_identity(self):
        root = _root(self.save_dir, presentation_mode="tiles")
        g = config.load_global(root)
        spec = _spec(root)
        adapter = nethack_adapter.NethackCoordinatorAdapter(
            g, config.load_game(g, "nethack"), spec
        )
        spec.runtime_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": 1,
            "status": "cleanup_failed",
            "mode": "tiles",
            "runtime_id": spec.runtime_id,
            "generation": spec.generation,
            "adapter_session": spec.adapter_session,
            "game_window": spec.game_window,
            "presentation_epoch": "p-" + "d" * 32,
            "owner_pid": 987654321,
            "owner_start_ticks": 1,
            "browser_pid": 12345,
            "browser_start_ticks": 9876,
            "browser_pgid": 12345,
            "browser_members": [
                {"pid": 12345, "start_ticks": 9876, "pgid": 12345, "sid": 12345}
            ],
            "tty_pid": None,
            "tty_start_ticks": None,
            "tty_pgid": None,
            "cleanup_complete": False,
        }
        adapter._tiles_manifest_path().write_text(json.dumps(manifest), encoding="utf-8")
        adapter._presentation_path().write_text(
            json.dumps({"status": "stopped"}), encoding="utf-8"
        )
        with (
            mock.patch.object(cli_game.CliCoordinatorAdapter, "cleanup_runtime"),
            mock.patch.object(adapter, "_check_active"),
            mock.patch(
                "docich.adapters.nethack.stop_manifest_process_group", return_value=True
            ) as stop_group,
        ):
            adapter.cleanup_runtime(time.monotonic() + 5, None)
        stop_group.assert_called_once_with(
            12345,
            9876,
            12345,
            deadline=mock.ANY,
            saved_members=[
                {"pid": 12345, "start_ticks": 9876, "pgid": 12345, "sid": 12345}
            ],
            cancel=None,
        )
        recovered = json.loads(adapter._tiles_manifest_path().read_text(encoding="utf-8"))
        self.assertEqual(recovered["status"], "stopped")
        self.assertTrue(recovered["cleanup_complete"])
        self.assertIsNone(recovered["browser_pid"])
        self.assertIsNone(recovered["browser_start_ticks"])
        self.assertIsNone(recovered["browser_pgid"])

    def test_cleanup_refuses_recycled_manifest_pid(self):
        if not Path("/proc").is_dir():
            self.skipTest("manifest process-group recovery requires procfs")
        root = _root(self.save_dir, presentation_mode="tiles")
        g = config.load_global(root)
        spec = _spec(root)
        adapter = nethack_adapter.NethackCoordinatorAdapter(
            g, config.load_game(g, "nethack"), spec
        )
        spec.runtime_dir.mkdir(parents=True, exist_ok=True)
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            start_ticks = nethack_adapter._proc_start_ticks(process.pid)
            owner_ticks = nethack_adapter._proc_start_ticks(os.getpid())
            self.assertIsNotNone(start_ticks)
            self.assertIsNotNone(owner_ticks)
            manifest = {
                "schema_version": 1,
                "status": "cleanup_failed",
                "mode": "tiles",
                "runtime_id": spec.runtime_id,
                "generation": spec.generation,
                "adapter_session": spec.adapter_session,
                "game_window": spec.game_window,
                "presentation_epoch": "p-" + "c" * 32,
                "owner_pid": os.getpid(),
                "owner_start_ticks": owner_ticks + 1,
                "browser_pid": process.pid,
                "browser_start_ticks": start_ticks + 1,
                "browser_pgid": process.pid,
                "tty_pid": None,
                "tty_start_ticks": None,
                "tty_pgid": None,
                "cleanup_complete": False,
            }
            adapter._tiles_manifest_path().write_text(json.dumps(manifest), encoding="utf-8")
            adapter._presentation_path().write_text(
                json.dumps({"status": "stopped"}), encoding="utf-8"
            )
            with (
                mock.patch.object(cli_game.CliCoordinatorAdapter, "cleanup_runtime"),
                mock.patch.object(adapter, "_check_active"),
                mock.patch("docich.nethack_tiles_supervisor.os.killpg") as killpg,
            ):
                with self.assertRaises(AdapterError):
                    adapter.cleanup_runtime(time.monotonic() + 5, None)
            killpg.assert_not_called()
            self.assertIsNone(process.poll())
        finally:
            process.terminate()
            process.wait(timeout=3)

    def test_unknown_presentation_mode_is_rejected(self):
        root = _root(self.save_dir)
        path = root / "config" / "games" / "nethack.toml"
        path.write_text(path.read_text(encoding="utf-8") +
                        '\n[nethack.presentation]\nmode = "autostart"\n',
                        encoding="utf-8")
        g = config.load_global(root)
        with self.assertRaises(AdapterError):
            nethack_adapter.NethackCoordinatorAdapter(
                g, config.load_game(g, "nethack"), _spec(root)
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

    def test_other_players_save_is_not_accepted_as_suspension_evidence(self):
        adapter = self.adapter()
        # The old substring match treated this as docich's save even though
        # Unix NetHack save files are exactly <uid><player> (+ compression suffix).
        (self.save_dir / "1000otherdocich").write_bytes(b"other-player-save")
        tmux = FakeTmux(self.spec, self.save_dir)
        tmux.process_alive = False
        adapter.tmux = tmux

        adapter.request_round_boundary(str(uuid.uuid4()), time.monotonic() + 1.0, None)

        marker = json.loads(
            (self.spec.runtime_dir / nethack_adapter.BOUNDARY_RESULT_FILENAME).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(marker["outcome"], "ended")
        self.assertNotIn("save_file", marker)

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

    def test_character_creation_screen_is_terminal_boundary_without_save(self):
        adapter = self.adapter()
        tmux = FakeTmux(self.spec, self.save_dir)
        tmux.pane_text = "Shall I pick a character for you? [ynq]"
        adapter.tmux = tmux
        request_id = str(uuid.uuid4())

        adapter.request_round_boundary(request_id, time.monotonic() + 1.0, None)

        # No `S` save is attempted: there is no run to suspend yet.
        self.assertFalse(any(call[0] == "send_keys" for call in tmux.calls))
        marker = json.loads(
            (self.spec.runtime_dir / nethack_adapter.BOUNDARY_RESULT_FILENAME).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(marker["outcome"], "ended")
        self.assertNotIn("save_file", marker)
        self.assertEqual(marker["request_id"], request_id)

    def test_gameplay_screen_still_uses_save_boundary(self):
        adapter = self.adapter()
        tmux = FakeTmux(self.spec, self.save_dir)
        tmux.pane_text = "  -----\n  |...|\n  |.@.|\n  -----\n\nDlvl:1 HP:18(18)\n"
        adapter.tmux = tmux

        adapter.request_round_boundary(str(uuid.uuid4()), time.monotonic() + 1.0, None)

        target = f"{self.spec.adapter_session}:nethack"
        self.assertIn(("send_keys", target, ["Escape"], False), tmux.calls)
        self.assertIn(("send_keys", target, ["S"], True), tmux.calls)
        marker = json.loads(
            (self.spec.runtime_dir / nethack_adapter.BOUNDARY_RESULT_FILENAME).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(marker["outcome"], "suspended")

    def test_death_disclosure_exits_normally_without_save(self):
        adapter = self.adapter()
        tmux = FakeTmux(self.spec, self.save_dir, create_save=False)
        tmux.pane_text = ("Do you want your possessions identified? [ynq] (n)\n"
                          "Dlvl:1 $:0 HP:0(15) Pw:2(2) AC:4 Xp:1 Starved Deaf\n")
        original = tmux.send_keys

        def send(target, keys, literal=False):
            original(target, keys, literal)
            if keys == ["q"]:
                tmux.process_alive = False

        tmux.send_keys = send
        adapter.tmux = tmux
        request_id = str(uuid.uuid4())
        adapter.request_round_boundary(request_id, time.monotonic() + 1, None)
        sent = [call[2] for call in tmux.calls if call[0] == "send_keys"]
        self.assertEqual(sent, [["q"]])
        marker = json.loads((self.spec.runtime_dir / nethack_adapter.BOUNDARY_RESULT_FILENAME).read_text())
        self.assertEqual(marker["outcome"], "ended")
        self.assertEqual(marker["request_id"], request_id)
        self.assertFalse(list(self.save_dir.iterdir()))

    def test_death_disclosure_must_really_exit_before_boundary_is_accepted(self):
        adapter = self.adapter()
        tmux = FakeTmux(self.spec, self.save_dir)
        tmux.pane_text = ("Do you want your possessions identified? [ynq] (n)\n"
                          "Dlvl:1 $:0 HP:0(15)\n")
        adapter.tmux = tmux
        with self.assertRaises(ReadinessTimeoutError):
            adapter.request_round_boundary(str(uuid.uuid4()), time.monotonic() + 0.03, None)
        self.assertTrue(tmux.process_alive)
        self.assertFalse((self.spec.runtime_dir / nethack_adapter.BOUNDARY_RESULT_FILENAME).exists())
        self.assertEqual([c[2] for c in tmux.calls if c[0] == "send_keys"], [["q"]])

    def test_death_disclosure_never_answers_for_living_or_ambiguous_screens(self):
        prompt = "Do you want your possessions identified? [ynq] (n)"
        for screen in [prompt, prompt + "\nDlvl:1 HP:1(15)",
                       "Really save? [yn] (n)\nDlvl:1 HP:0(15)",
                       "old message\n" + prompt + "\nDlvl:1 HP:0(15)",
                       "You die...\nDlvl:1 HP:0(15)"]:
            with self.subTest(screen=screen):
                adapter = self.adapter()
                tmux = FakeTmux(self.spec, self.save_dir)
                tmux.pane_text = screen
                adapter.tmux = tmux
                adapter.request_round_boundary(str(uuid.uuid4()), time.monotonic() + 1, None)
                self.assertNotIn(["q"], [c[2] for c in tmux.calls if c[0] == "send_keys"])

    def test_death_disclosure_checks_session_ownership_before_any_input(self):
        adapter = self.adapter()
        tmux = FakeTmux(self.spec, self.save_dir)
        tmux.pane_text = ("Do you want your possessions identified? [ynq] (n)\n"
                          "Dlvl:1 $:0 HP:0(15)\n")
        adapter.tmux = tmux
        with mock.patch.object(adapter, "_verify_session_ownership", side_effect=AdapterError("unowned")):
            with self.assertRaises(AdapterError):
                adapter.request_round_boundary(str(uuid.uuid4()), time.monotonic() + 1, None)
        self.assertFalse(any(c[0] == "send_keys" for c in tmux.calls))

    def test_character_creation_markers_are_specific(self):
        for marker in (
            "Do you want a tutorial?",
            "Shall I pick a character for you?",
            "Pick a role or press ? for more info.",
            "Pick a race",
            "Pick an alignment",
            "Pick a gender",
            "Is this ok? [ynq]",
        ):
            with self.subTest(marker=marker):
                self.assertTrue(nethack_adapter._is_character_creation_screen(marker))
        # Post-creation banners and normal gameplay must not be mistaken for
        # character creation; both require the durable save boundary.
        self.assertFalse(
            nethack_adapter._is_character_creation_screen("Welcome to NetHack!")
        )
        self.assertFalse(
            nethack_adapter._is_character_creation_screen(
                "Dlvl:3 HP:18(18) Pw:5(5) AC:6 Exp:1 T:120\n--More--"
            )
        )

    def test_capture_failure_falls_back_to_save_boundary(self):
        adapter = self.adapter()
        tmux = FakeTmux(self.spec, self.save_dir)

        def _boom(target):
            raise RuntimeError("capture failed")

        tmux.capture_pane = _boom
        adapter.tmux = tmux

        adapter.request_round_boundary(str(uuid.uuid4()), time.monotonic() + 1.0, None)

        target = f"{self.spec.adapter_session}:nethack"
        self.assertIn(("send_keys", target, ["S"], True), tmux.calls)
        marker = json.loads(
            (self.spec.runtime_dir / nethack_adapter.BOUNDARY_RESULT_FILENAME).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(marker["outcome"], "suspended")


if __name__ == "__main__":
    unittest.main()

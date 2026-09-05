"""P2 tests: runtime-aware CLI adapter (CliCoordinatorAdapter) and factory."""

import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402
from docich.adapters import AdapterError, make_coordinator_adapter  # noqa: E402
from docich.adapters import cli_game  # noqa: E402
from docich.game_switch import (  # noqa: E402
    DeadlineExceededError,
    ReadinessTimeoutError,
    RuntimeSpec,
)
from docich.tmux import OwnershipMismatchError, PaneState, TmuxOwnership  # noqa: E402
from docich.naming import runtime_names  # noqa: E402


def _spec(generation: int = 1, game: str = "nethack") -> RuntimeSpec:
    names = runtime_names(generation)
    runtime_id = f"g{generation}-abcdef"
    return RuntimeSpec(
        game=game,
        adapter="cli",
        generation=generation,
        runtime_id=runtime_id,
        lease_id=str(uuid.uuid4()),
        runtime_dir=Path("/run") / "runtimes" / runtime_id,
        game_window=names.game_window,
        agent_window=names.agent_window,
        adapter_session=names.adapter_session,
    )


class FakeTmux:
    """Checked tmux subset with ownership verification for the coordinator
    adapter."""

    def __init__(self):
        self.sessions = {}
        self.windows = {}
        self.window_envs = {}
        self.calls = []
        self.capture = "game screen"
        self.pane_states = [PaneState(dead=False, pid=1234)]
        self.pane_states_by_target = {}
        self.window_exists = None  # None = use self.windows

    def _expected(self, ownership):
        return (
            ownership.runtime_id,
            ownership.generation,
            ownership.role,
        )

    @staticmethod
    def _ownership(value):
        return TmuxOwnership(runtime_id=value[0], generation=value[1], role=value[2])

    def session_target_exists(self, session):
        self.calls.append(("session_target_exists", session))
        return session in self.sessions

    def window_target_exists(self, target):
        self.calls.append(("window_target_exists", target))
        if self.window_exists is not None:
            return self.window_exists
        return target in self.windows

    def read_session_ownership(self, session):
        self.calls.append(("read_session_ownership", session))
        if session not in self.sessions:
            raise OwnershipMismatchError("missing session")
        return self._ownership(self.sessions[session])

    def read_window_ownership(self, target):
        self.calls.append(("read_window_ownership", target))
        if target not in self.windows:
            raise OwnershipMismatchError("missing window")
        return self._ownership(self.windows[target])

    def create_game_session_owned(self, session, cmd, cols, rows, ownership):
        self.calls.append(("create_game_session_owned", session, list(cmd), cols, rows, self._expected(ownership)))
        if session in self.sessions:
            raise RuntimeError(f"duplicate session {session}")
        self.sessions[session] = self._expected(ownership)

    def create_window_owned(self, name, cmd, ownership, env=None):
        self.calls.append(("create_window_owned", name, list(cmd), self._expected(ownership)))
        target = f"docich:{name}"
        if target in self.windows:
            raise RuntimeError(f"duplicate window {target}")
        self.windows[target] = self._expected(ownership)
        self.window_envs[target] = dict(env or {})

    def kill_session_owned(self, session, expected):
        self.calls.append(("kill_session_owned", session, self._expected(expected)))
        if session not in self.sessions:
            return False
        if self.sessions[session] != self._expected(expected):
            raise OwnershipMismatchError("session ownership mismatch")
        del self.sessions[session]
        return True

    def kill_window_owned(self, target, expected):
        self.calls.append(("kill_window_owned", target, self._expected(expected)))
        if target not in self.windows:
            return False
        if self.windows[target] != self._expected(expected):
            raise OwnershipMismatchError("window ownership mismatch")
        del self.windows[target]
        return True

    def pane_states_checked(self, target):
        self.calls.append(("pane_states_checked", target))
        return list(self.pane_states_by_target.get(target, self.pane_states))

    def capture_pane_checked(self, target):
        self.calls.append(("capture_pane_checked", target))
        return self.capture


class CoordinatorAdapterTestBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self._tmpdir.name)
        self.g = config.load_global(self.repo_root)
        game_dir = self.repo_root / "config" / "games"
        game_dir.mkdir(parents=True)
        self.game_path = game_dir / "nethack.toml"
        self.game_path.write_text(
            '[game]\nname = "nethack"\nadapter = "cli"\ntitle = "NetHack"\n\n'
            '[cli]\ncommand = "nethack"\n',
            encoding="utf-8",
        )
        self.tmux = FakeTmux()
        self.deadline = time.monotonic() + 60.0
        self.spec = _spec()
        self.spec = RuntimeSpec(
            game=self.spec.game,
            adapter=self.spec.adapter,
            generation=self.spec.generation,
            runtime_id=self.spec.runtime_id,
            lease_id=self.spec.lease_id,
            runtime_dir=Path(self._tmpdir.name) / "runtimes" / self.spec.runtime_id,
            game_window=self.spec.game_window,
            agent_window=self.spec.agent_window,
            adapter_session=self.spec.adapter_session,
        )
        adapter = cli_game.CliCoordinatorAdapter(self.g, config.load_game(self.g, "nethack"), self.spec)
        adapter.tmux = self.tmux
        self.adapter = adapter

    def tearDown(self):
        self._tmpdir.cleanup()


class TestPreflight(CoordinatorAdapterTestBase):
    def test_preflight_resolves_command(self):
        with mock.patch(
            "docich.adapters.cli_game.procs.which", return_value="/usr/games/nethack"
        ):
            self.adapter.preflight(self.deadline, None)  # 副作用なし

    def test_preflight_missing_command_raises(self):
        with mock.patch("docich.adapters.cli_game.procs.which", return_value=None):
            with self.assertRaises(AdapterError):
                self.adapter.preflight(self.deadline, None)

    def test_preflight_honors_cancel(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(DeadlineExceededError):
            self.adapter.preflight(self.deadline, cancel)

    def test_docich_bin_points_to_repo_root(self):
        expected = Path(cli_game.__file__).resolve().parents[3] / "bin" / "docich"
        self.assertEqual(Path(cli_game._docich_bin()), expected)


class TestMaterialize(CoordinatorAdapterTestBase):
    def test_materialize_creates_generation_session_and_window(self):
        with mock.patch(
            "docich.adapters.cli_game.procs.which", return_value="/usr/games/nethack"
        ):
            self.adapter.materialize_runtime(self.deadline, None)
        expected = (
            "g1-abcdef",
            1,
            "adapter",
        )
        session_calls = [c for c in self.tmux.calls if c[0] == "create_game_session_owned"]
        self.assertEqual(len(session_calls), 1)
        _, session, cmd, cols, rows, ownership = session_calls[0]
        self.assertEqual(session, "docich-game-g1")
        self.assertEqual(cmd, ["/usr/games/nethack"])
        self.assertEqual((cols, rows), (80, 24))
        self.assertEqual(ownership, expected)
        window_calls = [c for c in self.tmux.calls if c[0] == "create_window_owned"]
        self.assertEqual(len(window_calls), 1)
        _, name, cmd, ownership = window_calls[0]
        self.assertEqual(name, "game-g1")
        self.assertIn("attach-session", cmd)
        self.assertIn("-t", cmd)
        self.assertIn("docich-game-g1", cmd)
        self.assertEqual(ownership, ("g1-abcdef", 1, "game"))
        self.assertEqual(self.tmux.window_envs["docich:game-g1"], {"DISPLAY": self.g.display.name})

    def test_materialize_is_idempotent_and_verifies_ownership(self):
        self.tmux.sessions["docich-game-g1"] = ("g1-abcdef", 1, "adapter")
        self.tmux.windows["docich:game-g1"] = ("g1-abcdef", 1, "game")
        with mock.patch(
            "docich.adapters.cli_game.procs.which", return_value="/usr/games/nethack"
        ):
            self.adapter.materialize_runtime(self.deadline, None)
        self.assertFalse(any(c[0] == "create_game_session_owned" for c in self.tmux.calls))
        self.assertFalse(any(c[0] == "create_window_owned" for c in self.tmux.calls))
        self.assertTrue(any(c[0] == "read_session_ownership" for c in self.tmux.calls))
        self.assertTrue(any(c[0] == "read_window_ownership" for c in self.tmux.calls))

    def test_materialize_rejects_foreign_existing_session(self):
        self.tmux.sessions["docich-game-g1"] = ("g1-zzzzzz", 1, "adapter")
        with self.assertRaises(OwnershipMismatchError):
            self.adapter.materialize_runtime(self.deadline, None)

    def test_materialize_rejects_foreign_existing_window(self):
        self.tmux.sessions["docich-game-g1"] = ("g1-abcdef", 1, "adapter")
        self.tmux.windows["docich:game-g1"] = ("g1-zzzzzz", 1, "game")
        with self.assertRaises(OwnershipMismatchError):
            self.adapter.materialize_runtime(self.deadline, None)

    def test_materialize_creates_runtime_dir(self):
        with mock.patch(
            "docich.adapters.cli_game.procs.which", return_value="/usr/games/nethack"
        ):
            self.adapter.materialize_runtime(self.deadline, None)
        self.assertTrue(self.spec.runtime_dir.is_dir())

    def test_materialize_cancelled_before_side_effects(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(DeadlineExceededError):
            self.adapter.materialize_runtime(self.deadline, cancel)
        self.assertFalse(self.spec.runtime_dir.exists())
        self.assertEqual(self.tmux.sessions, {})
        self.assertEqual(self.tmux.windows, {})


class TestReadiness(CoordinatorAdapterTestBase):
    def _ready(self):
        self.tmux.sessions["docich-game-g1"] = ("g1-abcdef", 1, "adapter")
        self.tmux.windows["docich:game-g1"] = ("g1-abcdef", 1, "game")

    def test_readiness_ok_when_session_window_pane_capture_ok(self):
        self._ready()
        self.adapter.readiness(self.deadline, None)
        self.assertTrue(any(c[0] == "capture_pane_checked" for c in self.tmux.calls))

    def test_readiness_empty_capture_is_not_failure(self):
        self._ready()
        self.tmux.capture = ""
        self.adapter.readiness(self.deadline, None)  # 空画面は失敗にしない

    def test_readiness_missing_session_fails(self):
        with self.assertRaises(ReadinessTimeoutError):
            self.adapter.readiness(self.deadline, None)

    def test_readiness_missing_window_fails(self):
        self.tmux.sessions["docich-game-g1"] = ("g1-abcdef", 1, "adapter")
        with self.assertRaises(ReadinessTimeoutError):
            self.adapter.readiness(self.deadline, None)

    def test_readiness_dead_pane_fails(self):
        self._ready()
        self.tmux.pane_states = [PaneState(dead=True, pid=0)]
        with self.assertRaises(ReadinessTimeoutError):
            self.adapter.readiness(self.deadline, None)

    def test_readiness_cancelled_fails(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(DeadlineExceededError):
            self.adapter.readiness(self.deadline, cancel)

    def test_readiness_rejects_foreign_window(self):
        self.tmux.sessions["docich-game-g1"] = ("g1-abcdef", 1, "adapter")
        self.tmux.windows["docich:game-g1"] = ("g1-zzzzzz", 1, "game")
        with self.assertRaises(OwnershipMismatchError):
            self.adapter.readiness(self.deadline, None)

    def test_readiness_checks_presenter_in_short_slices(self):
        self._ready()
        self.g.display.viewport_x = 0
        self.g.display.viewport_y = 0
        self.g.display.viewport_width = 960
        self.g.display.viewport_height = 540
        with mock.patch("docich.adapters.cli_game.XKit") as xkit_class:
            xkit_class.return_value.find_window.side_effect = [None, "123"]
            self.adapter.readiness(time.monotonic() + 2, None)

        calls = xkit_class.return_value.find_window.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call.kwargs["timeout"] <= 0.25 for call in calls))
        game_pane_probes = [
            call for call in self.tmux.calls
            if call == ("pane_states_checked", "docich:game-g1")
        ]
        self.assertGreaterEqual(len(game_pane_probes), 2)

    def test_readiness_fails_when_presenter_pane_dies_before_search(self):
        self._ready()
        self.g.display.viewport_width = 960
        self.g.display.viewport_height = 540
        self.tmux.pane_states_by_target["docich:game-g1"] = [PaneState(dead=True, pid=1234)]
        with mock.patch("docich.adapters.cli_game.XKit") as xkit_class:
            with self.assertRaises(ReadinessTimeoutError):
                self.adapter.readiness(time.monotonic() + 2, None)
        xkit_class.return_value.find_window.assert_not_called()


class TestAlive(CoordinatorAdapterTestBase):
    def test_alive_reflects_session_existence(self):
        self.assertFalse(self.adapter.alive(self.deadline, None))
        self.tmux.sessions["docich-game-g1"] = ("g1-abcdef", 1, "adapter")
        self.assertTrue(self.adapter.alive(self.deadline, None))

    def test_alive_rejects_foreign_session(self):
        self.tmux.sessions["docich-game-g1"] = ("g1-zzzzzz", 1, "adapter")
        with self.assertRaises(OwnershipMismatchError):
            self.adapter.alive(self.deadline, None)


class TestCleanup(CoordinatorAdapterTestBase):
    def test_cleanup_kills_owned_objects(self):
        self.tmux.sessions["docich-game-g1"] = ("g1-abcdef", 1, "adapter")
        self.tmux.windows["docich:game-g1"] = ("g1-abcdef", 1, "game")
        self.tmux.windows["docich:agent-g1"] = ("g1-abcdef", 1, "agent")
        self.adapter.cleanup_runtime(self.deadline, None)
        self.assertEqual(self.tmux.sessions, {})
        self.assertEqual(self.tmux.windows, {})
        kills = [c[0] for c in self.tmux.calls if c[0].startswith("kill_")]
        self.assertEqual(kills, ["kill_window_owned", "kill_window_owned", "kill_session_owned"])

    def test_cleanup_missing_objects_is_success(self):
        self.adapter.cleanup_runtime(self.deadline, None)  # 何も無くても成功

    def test_cleanup_ownership_mismatch_raises(self):
        self.tmux.windows["docich:game-g1"] = ("g1-zzzzzz", 1, "game")
        self.tmux.sessions["docich-game-g1"] = ("g1-abcdef", 1, "adapter")
        with self.assertRaises(OwnershipMismatchError):
            self.adapter.cleanup_runtime(self.deadline, None)
        # mismatch の object は残っている
        self.assertIn("docich:game-g1", self.tmux.windows)


class TestAgent(CoordinatorAdapterTestBase):
    def test_start_agent_creates_generation_window_with_repo_docich_bin(self):
        self.adapter.start_agent(self.deadline, None)
        window_calls = [c for c in self.tmux.calls if c[0] == "create_window_owned"]
        self.assertEqual(len(window_calls), 1)
        _, name, cmd, ownership = window_calls[0]
        self.assertEqual(name, "agent-g1")
        self.assertEqual(ownership, ("g1-abcdef", 1, "agent"))
        expected_bin = str(Path(cli_game.__file__).resolve().parents[3] / "bin" / "docich")
        self.assertEqual(cmd[0], expected_bin)
        self.assertIn("run", cmd)
        self.assertIn("agent", cmd)
        self.assertIn("nethack", cmd)

    def test_start_agent_is_idempotent_and_verifies_ownership(self):
        self.tmux.windows["docich:agent-g1"] = ("g1-abcdef", 1, "agent")
        self.adapter.start_agent(self.deadline, None)
        self.assertFalse(any(c[0] == "create_window_owned" for c in self.tmux.calls))
        self.assertTrue(any(c[0] == "read_window_ownership" for c in self.tmux.calls))

    def test_start_agent_rejects_foreign_existing_window(self):
        self.tmux.windows["docich:agent-g1"] = ("g1-zzzzzz", 1, "agent")
        with self.assertRaises(OwnershipMismatchError):
            self.adapter.start_agent(self.deadline, None)

    def test_stop_agent_kills_owned_window(self):
        self.tmux.windows["docich:agent-g1"] = ("g1-abcdef", 1, "agent")
        self.adapter.stop_agent(self.deadline, None)
        self.assertEqual(self.tmux.windows, {})
        self.assertTrue(any(c[0] == "kill_window_owned" for c in self.tmux.calls))

    def test_agent_enabled_reflects_config(self):
        game_path = self.repo_root / "config" / "games" / "nethack.toml"
        game_path.write_text(
            '[game]\nname = "nethack"\nadapter = "cli"\n\n'
            '[agent]\nenabled = true\nbrain = "command"\ncommand = "true"\n\n'
            '[cli]\ncommand = "nethack"\n',
            encoding="utf-8",
        )
        adapter = cli_game.CliCoordinatorAdapter(self.g, config.load_game(self.g, "nethack"), self.spec)
        adapter.tmux = self.tmux
        self.assertTrue(adapter.agent_enabled)

    def test_start_agent_binds_fence_args(self):
        self.adapter.start_agent(time.monotonic() + 5, None)
        window_calls = [c for c in self.tmux.calls if c[0] == "create_window_owned"]
        self.assertEqual(len(window_calls), 1)
        _, name, cmd, _ownership = window_calls[0]
        self.assertEqual(name, "agent-g1")
        self.assertIn("--runtime-id", cmd)
        self.assertIn("g1-abcdef", cmd)
        self.assertIn("--generation", cmd)
        self.assertIn("1", cmd)
        self.assertIn("--lease-id", cmd)
        self.assertIn(self.spec.lease_id, cmd)


class TestFactory(CoordinatorAdapterTestBase):
    def test_factory_resolves_cli_game(self):
        adapter = make_coordinator_adapter(self.g, self.spec)
        self.assertIsInstance(adapter, cli_game.CliCoordinatorAdapter)
        self.assertEqual(adapter.name, "cli")
        self.assertEqual(adapter.spec, self.spec)

    def test_factory_unknown_game_fails_closed(self):
        spec = _spec(game="nosuchgame")
        with self.assertRaises(config.ConfigError):
            make_coordinator_adapter(self.g, spec)

    def test_factory_unknown_adapter_kind_fails_closed(self):
        game_path = self.repo_root / "config" / "games" / "nethack.toml"
        game_path.write_text(
            '[game]\nname = "nethack"\nadapter = "unknown"\n', encoding="utf-8"
        )
        with self.assertRaises(AdapterError):
            make_coordinator_adapter(self.g, self.spec)


if __name__ == "__main__":
    unittest.main()

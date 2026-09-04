"""P2 tests: runtime-aware RetroArch adapter (RetroArchCoordinatorAdapter)."""

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
from docich.adapters import retroarch  # noqa: E402
from docich.game_switch import ReadinessTimeoutError, RuntimeSpec  # noqa: E402
from docich.naming import runtime_names  # noqa: E402
from docich.tmux import PaneState  # noqa: E402


def _spec(generation: int = 1, game: str = "hanjuku-hero") -> RuntimeSpec:
    names = runtime_names(generation)
    runtime_id = f"g{generation}-abcdef"
    return RuntimeSpec(
        game=game,
        adapter="retroarch",
        generation=generation,
        runtime_id=runtime_id,
        lease_id=str(uuid.uuid4()),
        runtime_dir=Path("/run") / "runtimes" / runtime_id,
        game_window=names.game_window,
        agent_window=names.agent_window,
        adapter_session=names.adapter_session,
    )


class FakeTmux:
    def __init__(self):
        self.windows = {}
        self.calls = []
        self.created_env = []
        self.pane_states = [PaneState(dead=False, pid=1234)]

    def _expected(self, ownership):
        return (ownership.runtime_id, ownership.generation, ownership.role)

    def window_target_exists(self, target):
        self.calls.append(("window_target_exists", target))
        return target in self.windows

    def create_window_owned(self, name, cmd, ownership, env=None):
        self.calls.append(("create_window_owned", name, list(cmd), self._expected(ownership)))
        self.created_env.append(("create_window_owned", name, dict(env or {})))
        target = f"docich:{name}"
        if target in self.windows:
            raise RuntimeError(f"duplicate window {target}")
        self.windows[target] = self._expected(ownership)

    def kill_window_owned(self, target, expected):
        self.calls.append(("kill_window_owned", target, self._expected(expected)))
        if target not in self.windows:
            return False
        del self.windows[target]
        return True

    def read_window_ownership(self, target):
        self.calls.append(("read_window_ownership", target))
        runtime_id, generation, role = self.windows[target]
        from docich.tmux import TmuxOwnership

        return TmuxOwnership(runtime_id=runtime_id, generation=generation, role=role)

    def pane_states_checked(self, target):
        self.calls.append(("pane_states_checked", target))
        return list(self.pane_states)


class RetroArchCoordinatorTestBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self._tmpdir.name)
        self.g = config.load_global(self.repo_root)
        game_dir = self.repo_root / "config" / "games"
        game_dir.mkdir(parents=True)
        self.rom = self.repo_root / "games" / "roms" / "hanjuku.sfc"
        self.rom.parent.mkdir(parents=True)
        self.rom.write_bytes(b"rom")
        self.core = self.repo_root / "libretro" / "snes9x_libretro.so"
        self.core.parent.mkdir(parents=True)
        self.core.write_bytes(b"core")
        self.game_path = game_dir / "hanjuku-hero.toml"
        self.game_path.write_text(
            '[game]\nname = "hanjuku-hero"\nadapter = "retroarch"\ntitle = "半熟英雄"\n\n'
            "[retroarch]\n"
            f'rom = "{self.rom}"\n'
            f'core = "{self.core}"\n',
            encoding="utf-8",
        )
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
        self.tmux = FakeTmux()
        adapter = retroarch.RetroArchCoordinatorAdapter(
            self.g, config.load_game(self.g, "hanjuku-hero"), self.spec
        )
        adapter.tmux = self.tmux
        self.adapter = adapter

    def tearDown(self):
        self._tmpdir.cleanup()


class TestPreflight(RetroArchCoordinatorTestBase):
    def test_preflight_validates_rom_core_and_binaries(self):
        with mock.patch("docich.adapters.retroarch.procs.which", return_value="/usr/bin/retroarch"):
            self.adapter.preflight(time.monotonic() + 5, None)

    def test_preflight_missing_retroarch_raises(self):
        with mock.patch("docich.adapters.retroarch.procs.which", return_value=None):
            with self.assertRaises(AdapterError):
                self.adapter.preflight(time.monotonic() + 5, None)

    def test_preflight_missing_rom_raises(self):
        self.rom.unlink()
        with self.assertRaises(AdapterError):
            self.adapter.preflight(time.monotonic() + 5, None)


class TestMaterialize(RetroArchCoordinatorTestBase):
    def test_materialize_writes_generation_cfg_with_runtime_port(self):
        self.adapter.materialize_runtime(time.monotonic() + 5, None)
        cfg = (self.spec.runtime_dir / "retroarch.cfg").read_text(encoding="utf-8")
        self.assertIn(f'network_cmd_port = "{retroarch.NETWORK_CMD_PORT + 1}"', cfg)
        self.assertIn('network_cmd_enable = "true"', cfg)
        self.assertIn(f'savestate_directory = "{self.spec.runtime_dir / "states"}"', cfg)
        window_calls = [c for c in self.tmux.calls if c[0] == "create_window_owned"]
        self.assertEqual(len(window_calls), 1)
        _, name, cmd, ownership = window_calls[0]
        self.assertEqual(name, "game-g1")
        self.assertEqual(ownership, ("g1-abcdef", 1, "game"))
        self.assertEqual(cmd[0], "dbus-run-session")
        self.assertIn(str(self.core), cmd)
        self.assertIn(str(self.rom), cmd)
        # 実機 smoke (2026-09-05) で発見: game window に DISPLAY が無いと retroarch も
        # X なしで即死する (browser と同じ経路)。実機 regression を環境依存で固定する。
        self.assertEqual(
            [e[2] for e in self.tmux.created_env if e[0] == "create_window_owned"],
            [{"DISPLAY": ":98"}],
        )

    def test_materialize_is_idempotent(self):
        self.tmux.windows["docich:game-g1"] = ("g1-abcdef", 1, "game")
        self.adapter.materialize_runtime(time.monotonic() + 5, None)
        self.assertFalse(any(c[0] == "create_window_owned" for c in self.tmux.calls))
        self.assertTrue(any(c[0] == "read_window_ownership" for c in self.tmux.calls))

    def test_network_port_is_generation_derived_and_unique(self):
        ports = {retroarch.retroarch_network_port(g) for g in range(1, 501)}
        self.assertEqual(len(ports), 500)


class TestReadiness(RetroArchCoordinatorTestBase):
    def _ready_window(self):
        self.tmux.windows["docich:game-g1"] = ("g1-abcdef", 1, "game")

    def test_readiness_ok_when_window_pane_and_port_ok(self):
        self._ready_window()
        with mock.patch("docich.adapters.retroarch.send_ra_cmd", return_value="GET_STATUS OK"):
            self.adapter.readiness(time.monotonic() + 5, None)
        self.assertTrue(any(c[0] == "pane_states_checked" for c in self.tmux.calls))

    def test_readiness_missing_window_fails(self):
        with mock.patch("docich.adapters.retroarch.send_ra_cmd", return_value="GET_STATUS OK"):
            with self.assertRaises(ReadinessTimeoutError):
                self.adapter.readiness(time.monotonic() + 5, None)

    def test_readiness_dead_pane_fails(self):
        self._ready_window()
        self.tmux.pane_states = [PaneState(dead=True, pid=0)]
        with self.assertRaises(ReadinessTimeoutError):
            self.adapter.readiness(time.monotonic() + 5, None)

    def test_readiness_no_port_reply_within_deadline_fails(self):
        self._ready_window()
        with mock.patch("docich.adapters.retroarch.send_ra_cmd", return_value=None):
            with self.assertRaises(ReadinessTimeoutError):
                self.adapter.readiness(time.monotonic() + 0.1, None)

    def test_readiness_late_port_reply_succeeds(self):
        self._ready_window()
        replies = iter([None, None, "GET_STATUS OK"])

        def fake_send(cmd, **kwargs):
            return next(replies)

        with mock.patch("docich.adapters.retroarch.send_ra_cmd", side_effect=fake_send):
            self.adapter.readiness(time.monotonic() + 5, None)

    def test_readiness_uses_runtime_port(self):
        self._ready_window()
        with mock.patch("docich.adapters.retroarch.send_ra_cmd", return_value="OK") as ra_send:
            self.adapter.readiness(time.monotonic() + 5, None)
            self.assertEqual(ra_send.call_args.kwargs["port"], retroarch.NETWORK_CMD_PORT + 1)

    def test_readiness_udp_wait_is_bounded_by_remaining_time(self):
        self._ready_window()
        with mock.patch("docich.adapters.retroarch.send_ra_cmd", return_value=None) as ra_send:
            with self.assertRaises(ReadinessTimeoutError):
                self.adapter.readiness(time.monotonic() + 0.1, None)
            # 最後の probe は残り時間に束縛された short wait で呼ばれる
            self.assertLessEqual(
                ra_send.call_args.kwargs["wait_reply_s"],
                retroarch.RA_READY_POLL_S,
            )

    def test_network_probe_cancel_during_udp_wait_converges_within_grace(self):
        cancel = threading.Event()
        started = time.monotonic()

        def slow_reply(cmd, **kwargs):
            # UDP wait 中に cancel される状況を再現: cancel が set された後も
            # socket wait は束縛されているため worker は grace 内に終わる。
            cancel.set()
            return None

        with mock.patch("docich.adapters.retroarch.send_ra_cmd", side_effect=slow_reply):
            with self.assertRaises(ReadinessTimeoutError):
                self.adapter._probe_network_status(time.monotonic() + 5, cancel)
        self.assertLess(time.monotonic() - started, 0.8, "cancel grace (0.5s) 内に収束すること")


class TestAlive(RetroArchCoordinatorTestBase):
    def test_alive_reflects_window_and_pane(self):
        self.assertFalse(self.adapter.alive(time.monotonic() + 5, None))
        self.tmux.windows["docich:game-g1"] = ("g1-abcdef", 1, "game")
        self.assertTrue(self.adapter.alive(time.monotonic() + 5, None))
        self.tmux.pane_states = [PaneState(dead=True, pid=0)]
        self.assertFalse(self.adapter.alive(time.monotonic() + 5, None))


class TestCleanup(RetroArchCoordinatorTestBase):
    def test_cleanup_kills_owned_windows(self):
        self.tmux.windows["docich:game-g1"] = ("g1-abcdef", 1, "game")
        self.tmux.windows["docich:agent-g1"] = ("g1-abcdef", 1, "agent")
        self.adapter.cleanup_runtime(time.monotonic() + 5, None)
        self.assertEqual(self.tmux.windows, {})

    def test_cleanup_missing_is_success(self):
        self.adapter.cleanup_runtime(time.monotonic() + 5, None)


class TestAgent(RetroArchCoordinatorTestBase):
    def test_start_agent_creates_generation_window(self):
        self.adapter.start_agent(time.monotonic() + 5, None)
        window_calls = [c for c in self.tmux.calls if c[0] == "create_window_owned"]
        self.assertEqual(len(window_calls), 1)
        _, name, cmd, ownership = window_calls[0]
        self.assertEqual(name, "agent-g1")
        self.assertEqual(ownership, ("g1-abcdef", 1, "agent"))
        self.assertIn("hanjuku-hero", cmd)

    def test_stop_agent_kills_owned_window(self):
        self.tmux.windows["docich:agent-g1"] = ("g1-abcdef", 1, "agent")
        self.adapter.stop_agent(time.monotonic() + 5, None)
        self.assertEqual(self.tmux.windows, {})


class TestFactory(RetroArchCoordinatorTestBase):
    def test_factory_resolves_retroarch_game(self):
        adapter = make_coordinator_adapter(self.g, self.spec)
        self.assertIsInstance(adapter, retroarch.RetroArchCoordinatorAdapter)
        self.assertEqual(adapter.name, "retroarch")


if __name__ == "__main__":
    unittest.main()
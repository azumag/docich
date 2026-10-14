"""P2 tests: runtime-aware browser adapter (BrowserCoordinatorAdapter)."""

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
from docich.adapters import browser  # noqa: E402
from docich.game_switch import ReadinessTimeoutError, RuntimeSpec  # noqa: E402
from docich.naming import runtime_names  # noqa: E402
from docich.tmux import PaneState  # noqa: E402


def _spec(generation: int = 1, game: str = "sorengame") -> RuntimeSpec:
    names = runtime_names(generation)
    runtime_id = f"g{generation}-abcdef"
    return RuntimeSpec(
        game=game,
        adapter="browser",
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


class BrowserCoordinatorTestBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self._tmpdir.name)
        self.g = config.load_global(self.repo_root)
        game_dir = self.repo_root / "config" / "games"
        game_dir.mkdir(parents=True)
        self.game_path = game_dir / "sorengame.toml"
        self.game_path.write_text(
            '[game]\nname = "sorengame"\nadapter = "browser"\n\n'
            '[browser]\nurl = "http://127.0.0.1:8080"\nkiosk = true\n',
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
        self.xkit = mock.Mock()
        adapter = browser.BrowserCoordinatorAdapter(
            self.g, config.load_game(self.g, "sorengame"), self.spec, xkit=self.xkit
        )
        adapter.tmux = self.tmux
        self.adapter = adapter

    def tearDown(self):
        self._tmpdir.cleanup()

    def _ready_window(self):
        self.tmux.windows["docich:game-g1"] = ("g1-abcdef", 1, "game")


class TestPreflight(BrowserCoordinatorTestBase):
    def test_preflight_resolves_binary_and_url(self):
        with mock.patch(
            "docich.adapters.browser.procs.which", return_value="/usr/bin/chromium"
        ):
            self.adapter.preflight(time.monotonic() + 5, None)

    def test_preflight_missing_binary_raises(self):
        with mock.patch("docich.adapters.browser.procs.which", return_value=None):
            with self.assertRaises(AdapterError):
                self.adapter.preflight(time.monotonic() + 5, None)

    def test_preflight_missing_url_raises(self):
        self.game_path.write_text(
            '[game]\nname = "sorengame"\nadapter = "browser"\n\n[browser]\n',
            encoding="utf-8",
        )
        adapter = browser.BrowserCoordinatorAdapter(
            self.g, config.load_game(self.g, "sorengame"), self.spec, xkit=self.xkit
        )
        adapter.tmux = self.tmux
        with mock.patch(
            "docich.adapters.browser.procs.which", return_value="/usr/bin/chromium"
        ):
            with self.assertRaises(AdapterError):
                adapter.preflight(time.monotonic() + 5, None)

    def test_preflight_rejects_explicit_missing_binary(self):
        self.game_path.write_text(
            '[game]\nname = "sorengame"\nadapter = "browser"\n\n'
            '[browser]\nurl = "http://127.0.0.1:8080"\nbinary = "/usr/bin/no-such-chromium"\n',
            encoding="utf-8",
        )
        adapter = browser.BrowserCoordinatorAdapter(
            self.g, config.load_game(self.g, "sorengame"), self.spec, xkit=self.xkit
        )
        adapter.tmux = self.tmux
        with self.assertRaises(AdapterError):
            adapter.preflight(time.monotonic() + 5, None)

    def test_preflight_rejects_string_launch_command(self):
        self.game_path.write_text(
            '[game]\nname = "sorengame"\nadapter = "browser"\n\n'
            '[browser]\nlaunch_command = "echo hi"\n',
            encoding="utf-8",
        )
        adapter = browser.BrowserCoordinatorAdapter(
            self.g, config.load_game(self.g, "sorengame"), self.spec, xkit=self.xkit
        )
        adapter.tmux = self.tmux
        with self.assertRaises(AdapterError):
            adapter.preflight(time.monotonic() + 5, None)

    def test_preflight_rejects_invalid_probe_type(self):
        self.game_path.write_text(
            '[game]\nname = "sorengame"\nadapter = "browser"\n\n'
            '[browser]\nlaunch_command = ["echo", "hi"]\n'
            'readiness_probe = {type = "nonsense"}\n',
            encoding="utf-8",
        )
        adapter = browser.BrowserCoordinatorAdapter(
            self.g, config.load_game(self.g, "sorengame"), self.spec, xkit=self.xkit
        )
        adapter.tmux = self.tmux
        with self.assertRaises(AdapterError):
            adapter.preflight(time.monotonic() + 5, None)

    def test_preflight_rejects_http_probe_without_url(self):
        self.game_path.write_text(
            '[game]\nname = "sorengame"\nadapter = "browser"\n\n'
            '[browser]\nlaunch_command = ["echo", "hi"]\n'
            'readiness_probe = {type = "http"}\n',
            encoding="utf-8",
        )
        adapter = browser.BrowserCoordinatorAdapter(
            self.g, config.load_game(self.g, "sorengame"), self.spec, xkit=self.xkit
        )
        adapter.tmux = self.tmux
        with self.assertRaises(AdapterError):
            adapter.preflight(time.monotonic() + 5, None)

    def test_preflight_rejects_runtime_owned_extra_arg(self):
        self.game_path.write_text(
            '[game]\nname = "sorengame"\nadapter = "browser"\n\n'
            '[browser]\nurl = "http://127.0.0.1:8080"\n'
            'extra_args = ["--user-data-dir=/tmp/hijack"]\n',
            encoding="utf-8",
        )
        adapter = browser.BrowserCoordinatorAdapter(
            self.g, config.load_game(self.g, "sorengame"), self.spec, xkit=self.xkit
        )
        adapter.tmux = self.tmux
        with mock.patch(
            "docich.adapters.browser.procs.which", return_value="/usr/bin/chromium"
        ):
            with self.assertRaises(AdapterError):
                adapter.preflight(time.monotonic() + 5, None)
            with self.assertRaises(AdapterError):
                adapter.materialize_runtime(time.monotonic() + 5, None)


class TestMaterialize(BrowserCoordinatorTestBase):
    def test_materialize_launches_with_runtime_profile_and_debug_port(self):
        with mock.patch(
            "docich.adapters.browser.procs.which", return_value="/usr/bin/chromium"
        ):
            self.adapter.materialize_runtime(time.monotonic() + 5, None)
        window_calls = [c for c in self.tmux.calls if c[0] == "create_window_owned"]
        self.assertEqual(len(window_calls), 1)
        _, name, cmd, ownership = window_calls[0]
        self.assertEqual(name, "game-g1")
        self.assertEqual(ownership, ("g1-abcdef", 1, "game"))
        self.assertIn(f"--user-data-dir={self.spec.runtime_dir / 'browser-profile'}", cmd)
        self.assertIn(f"--remote-debugging-port={browser.DEVTOOLS_BASE_PORT + 1}", cmd)
        self.assertIn("http://127.0.0.1:8080", cmd)
        # 実機 smoke (2026-09-05) で発見: game window に DISPLAY が無いと chromium が
        # "Missing X server or $DISPLAY" で即死し readiness がタイムアウトする。
        self.assertEqual(
            [e[2] for e in self.tmux.created_env if e[0] == "create_window_owned"],
            [{"DISPLAY": ":98"}],
        )

    def test_materialize_is_idempotent(self):
        self._ready_window()
        with mock.patch(
            "docich.adapters.browser.procs.which", return_value="/usr/bin/chromium"
        ):
            self.adapter.materialize_runtime(time.monotonic() + 5, None)
        self.assertFalse(any(c[0] == "create_window_owned" for c in self.tmux.calls))

    def test_devtools_port_is_generation_derived(self):
        self.assertEqual(browser.browser_devtools_port(1), browser.DEVTOOLS_BASE_PORT + 1)
        self.assertNotEqual(
            browser.browser_devtools_port(1), browser.browser_devtools_port(2)
        )


class TestReadiness(BrowserCoordinatorTestBase):
    def test_readiness_ok_when_devtools_shows_expected_url(self):
        self._ready_window()
        with mock.patch.object(
            self.adapter, "_devtools_pages",
            return_value=[{"url": "http://127.0.0.1:8080/"}],
        ):
            self.adapter.readiness(time.monotonic() + 5, None)

    def test_readiness_missing_window_fails(self):
        with mock.patch.object(self.adapter, "_devtools_pages", return_value=[]):
            with self.assertRaises(ReadinessTimeoutError):
                self.adapter.readiness(time.monotonic() + 5, None)

    def test_readiness_dead_pane_fails(self):
        self._ready_window()
        self.tmux.pane_states = [PaneState(dead=True, pid=0)]
        with self.assertRaises(ReadinessTimeoutError):
            self.adapter.readiness(time.monotonic() + 5, None)

    def test_readiness_devtools_unreachable_within_deadline_fails(self):
        self._ready_window()
        with mock.patch.object(self.adapter, "_devtools_pages", side_effect=OSError("no devtools")):
            with self.assertRaises(ReadinessTimeoutError):
                self.adapter.readiness(time.monotonic() + 0.1, None)

    def test_readiness_waits_for_late_expected_url(self):
        self._ready_window()
        pages = iter([[], [], [{"url": "http://127.0.0.1:8080/"}]])
        with mock.patch.object(
            self.adapter, "_devtools_pages", side_effect=lambda wait_s: next(pages)
        ):
            self.adapter.readiness(time.monotonic() + 5, None)

    def test_launch_command_process_probe_is_pane_check_only(self):
        self.game_path.write_text(
            '[game]\nname = "sorengame"\nadapter = "browser"\n\n'
            '[browser]\nlaunch_command = ["echo", "hi"]\n',
            encoding="utf-8",
        )
        adapter = browser.BrowserCoordinatorAdapter(
            self.g, config.load_game(self.g, "sorengame"), self.spec, xkit=self.xkit
        )
        adapter.tmux = self.tmux
        self._ready_window()
        adapter.readiness(time.monotonic() + 5, None)

    def test_launch_command_window_probe_uses_xkit(self):
        self.game_path.write_text(
            '[game]\nname = "sorengame"\nadapter = "browser"\n\n'
            '[browser]\nlaunch_command = ["echo", "hi"]\n'
            'readiness_probe = {type = "window", window_pattern = "Soren"}\n',
            encoding="utf-8",
        )
        adapter = browser.BrowserCoordinatorAdapter(
            self.g, config.load_game(self.g, "sorengame"), self.spec, xkit=self.xkit
        )
        adapter.tmux = self.tmux
        self._ready_window()
        self.xkit.find_window.return_value = "0x1"
        adapter.readiness(time.monotonic() + 5, None)
        self.xkit.find_window.assert_called_once()
        self.assertEqual(self.xkit.find_window.call_args.args[0], "Soren")
        self.assertIn("timeout", self.xkit.find_window.call_args.kwargs)

    def test_launch_command_http_probe_ok(self):
        self.game_path.write_text(
            '[game]\nname = "sorengame"\nadapter = "browser"\n\n'
            '[browser]\nlaunch_command = ["echo", "hi"]\n'
            'readiness_probe = {type = "http", url = "http://127.0.0.1:9/"}\n',
            encoding="utf-8",
        )
        adapter = browser.BrowserCoordinatorAdapter(
            self.g, config.load_game(self.g, "sorengame"), self.spec, xkit=self.xkit
        )
        adapter.tmux = self.tmux
        self._ready_window()
        with mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value.status = 200
            adapter.readiness(time.monotonic() + 5, None)

    def test_launch_command_argv_probe_ok(self):
        self.game_path.write_text(
            '[game]\nname = "sorengame"\nadapter = "browser"\n\n'
            '[browser]\nlaunch_command = ["echo", "hi"]\n'
            'readiness_probe = {type = "argv", command = ["true"]}\n',
            encoding="utf-8",
        )
        adapter = browser.BrowserCoordinatorAdapter(
            self.g, config.load_game(self.g, "sorengame"), self.spec, xkit=self.xkit
        )
        adapter.tmux = self.tmux
        self._ready_window()
        with mock.patch("docich.adapters.browser.procs.run") as run:
            run.return_value.returncode = 0
            adapter.readiness(time.monotonic() + 5, None)

    def test_invalid_probe_type_fails_closed(self):
        self.game_path.write_text(
            '[game]\nname = "sorengame"\nadapter = "browser"\n\n'
            '[browser]\nlaunch_command = ["echo", "hi"]\n'
            'readiness_probe = {type = "nonsense"}\n',
            encoding="utf-8",
        )
        adapter = browser.BrowserCoordinatorAdapter(
            self.g, config.load_game(self.g, "sorengame"), self.spec, xkit=self.xkit
        )
        adapter.tmux = self.tmux
        self._ready_window()
        with self.assertRaises(AdapterError):
            adapter.readiness(time.monotonic() + 5, None)

    def test_argv_probe_bounds_subprocess_timeout(self):
        self.game_path.write_text(
            '[game]\nname = "sorengame"\nadapter = "browser"\n\n'
            '[browser]\nlaunch_command = ["echo", "hi"]\n'
            'readiness_probe = {type = "argv", command = ["true"]}\n',
            encoding="utf-8",
        )
        adapter = browser.BrowserCoordinatorAdapter(
            self.g, config.load_game(self.g, "sorengame"), self.spec, xkit=self.xkit
        )
        adapter.tmux = self.tmux
        self._ready_window()
        with mock.patch("docich.adapters.browser.procs.run") as run:
            run.return_value.returncode = 0
            adapter.readiness(time.monotonic() + 5, None)
            self.assertLessEqual(
                run.call_args.kwargs["timeout"], browser.READY_POLL_S
            )

    def test_devtools_probe_cancel_converges_within_grace(self):
        self._ready_window()
        cancel = threading.Event()
        started = time.monotonic()

        def slow_pages(wait_s):
            cancel.set()
            raise OSError("no devtools")

        with mock.patch.object(self.adapter, "_devtools_pages", side_effect=slow_pages):
            with self.assertRaises(ReadinessTimeoutError):
                self.adapter._probe_expected_url(time.monotonic() + 5, cancel)
        self.assertLess(time.monotonic() - started, 0.8, "cancel grace (0.5s) 内に収束すること")

    def test_argv_probe_cancel_converges_within_grace(self):
        self.game_path.write_text(
            '[game]\nname = "sorengame"\nadapter = "browser"\n\n'
            '[browser]\nlaunch_command = ["echo", "hi"]\n'
            'readiness_probe = {type = "argv", command = ["true"]}\n',
            encoding="utf-8",
        )
        adapter = browser.BrowserCoordinatorAdapter(
            self.g, config.load_game(self.g, "sorengame"), self.spec, xkit=self.xkit
        )
        adapter.tmux = self.tmux
        self._ready_window()
        cancel = threading.Event()
        started = time.monotonic()

        def slow_run(argv, **kwargs):
            cancel.set()
            return mock.Mock(returncode=1)

        with mock.patch("docich.adapters.browser.procs.run", side_effect=slow_run):
            with self.assertRaises(ReadinessTimeoutError):
                adapter._probe_launch_command(time.monotonic() + 5, cancel)
        self.assertLess(time.monotonic() - started, 0.8, "cancel grace (0.5s) 内に収束すること")


class TestAlive(BrowserCoordinatorTestBase):
    def test_alive_reflects_window_and_pane(self):
        self.assertFalse(self.adapter.alive(time.monotonic() + 5, None))
        self._ready_window()
        self.assertTrue(self.adapter.alive(time.monotonic() + 5, None))
        self.tmux.pane_states = [PaneState(dead=True, pid=0)]
        self.assertFalse(self.adapter.alive(time.monotonic() + 5, None))


class TestCleanupAndAgent(BrowserCoordinatorTestBase):
    def test_cleanup_kills_owned_windows(self):
        self.tmux.windows["docich:game-g1"] = ("g1-abcdef", 1, "game")
        self.tmux.windows["docich:agent-g1"] = ("g1-abcdef", 1, "agent")
        self.adapter.cleanup_runtime(time.monotonic() + 5, None)
        self.assertEqual(self.tmux.windows, {})

    def test_start_agent_creates_generation_window(self):
        self.adapter.start_agent(time.monotonic() + 5, None)
        window_calls = [c for c in self.tmux.calls if c[0] == "create_window_owned"]
        self.assertEqual(len(window_calls), 1)
        _, name, cmd, ownership = window_calls[0]
        self.assertEqual(name, "agent-g1")
        self.assertEqual(ownership, ("g1-abcdef", 1, "agent"))
        self.assertIn("sorengame", cmd)

    def test_stop_agent_kills_owned_window(self):
        self.tmux.windows["docich:agent-g1"] = ("g1-abcdef", 1, "agent")
        self.adapter.stop_agent(time.monotonic() + 5, None)
        self.assertEqual(self.tmux.windows, {})


class TestFactory(BrowserCoordinatorTestBase):
    def test_factory_resolves_browser_game(self):
        adapter = make_coordinator_adapter(self.g, self.spec)
        self.assertIsInstance(adapter, browser.BrowserCoordinatorAdapter)
        self.assertEqual(adapter.name, "browser")


if __name__ == "__main__":
    unittest.main()
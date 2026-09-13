"""Phase 2b tests: Soren91 coordinator adapter + manual corner runner."""

import io
import json
import sys
import tempfile
import time
import unittest
import urllib.error
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402
from docich.adapters import make_coordinator_adapter  # noqa: E402
from docich.adapters.base import AdapterError  # noqa: E402
from docich.adapters.soren91 import (  # noqa: E402
    Soren91CoordinatorAdapter,
    is_tailscale_ipv4,
    parse_udp_listeners,
)
from docich.game_switch import ReadinessTimeoutError, RuntimeSpec  # noqa: E402
from docich.tmux import PaneState, TmuxOwnership  # noqa: E402

MAC_IP = "100.90.0.1"
OCI_IP = "100.90.0.2"
BASE_URL = f"http://{MAC_IP}:19191"
TOKEN = "test-token-0123456789abcdef"


class FakeTmux:
    def __init__(self):
        self.sessions = {}
        self.windows = {}
        self.window_env = {}
        self.session_cmds = {}
        self.window_cmds = {}
        self.window_cwd = {}

    @staticmethod
    def _expected(ownership):
        return (ownership.runtime_id, ownership.generation, ownership.role)

    def session_target_exists(self, session, strict=False):
        return session in self.sessions

    def window_target_exists(self, target, strict=False):
        return target in self.windows

    def create_game_session_owned(self, session, cmd, cols, rows, ownership):
        self.sessions[session] = self._expected(ownership)
        self.session_cmds[session] = list(cmd)

    def create_window_owned(self, name, cmd, ownership, env=None, cwd=None):
        target = f"docich-game-g{ownership.generation}:{name}"
        self.windows[target] = self._expected(ownership)
        self.window_cmds[target] = list(cmd)
        self.window_env[target] = dict(env or {})
        self.window_cwd[target] = cwd

    def read_session_ownership(self, session):
        runtime_id, generation, role = self.sessions[session]
        return TmuxOwnership(runtime_id=runtime_id, generation=generation, role=role)

    def read_window_ownership(self, target):
        runtime_id, generation, role = self.windows[target]
        return TmuxOwnership(runtime_id=runtime_id, generation=generation, role=role)

    def pane_states_checked(self, target):
        return [PaneState(dead=False, pid=1234)]

    def capture_pane_checked(self, target):
        return "screen"

    def kill_session_owned(self, session, expected):
        if session not in self.sessions:
            return False
        del self.sessions[session]
        return True

    def kill_window_owned(self, target, expected):
        if target not in self.windows:
            return False
        del self.windows[target]
        return True


class FakeHttpResponse:
    def __init__(self, status=200, payload=None):
        self.status = status
        self._payload = payload if payload is not None else {}

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Soren91AdapterTestBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.root = Path(self._tmpdir.name)
        games = self.root / "config" / "games"
        games.mkdir(parents=True, exist_ok=True)
        (games / "soren91.toml").write_text(
            '[game]\nname="soren91"\ntitle="Soren91"\nadapter="soren91"\n'
            '[agent]\nenabled=false\n'
            '[lifecycle]\nrequire_round_boundary=false\n'
            '[soren91]\ncdp_port=9322\nsrt_port=19192\nffplay_bin="ffplay"\nbot_path=""\n',
            encoding="utf-8",
        )
        self.g = config.load_global(self.root)
        self.game = config.load_game(self.g, "soren91")
        self.spec = RuntimeSpec(
            game="soren91", adapter="soren91", generation=3,
            runtime_id="g3-abcdef12", lease_id=None,
            runtime_dir=self.root / "run" / "g3-abcdef12",
            game_window="game-g3", agent_window="agent-g3",
            adapter_session="docich-game-g3",
        )
        self.tmux = FakeTmux()
        self._env = mock.patch.dict(
            "os.environ",
            {
                "SOREN91_MACOS_AGENT_BASE_URL": BASE_URL,
                "SOREN91_LOCAL_AGENT_TOKEN": TOKEN,
                "SOREN91_OCI_TAILSCALE_IP": OCI_IP,
            },
            clear=False,
        )
        self._env.start()
        self.addCleanup(self._env.stop)
        self._tmux_patch = mock.patch("docich.adapters.cli_game.Tmux", return_value=self.tmux)
        self._tmux_patch.start()
        self.addCleanup(self._tmux_patch.stop)
        self.http_calls = []
        self.http_plan = []  # list of (status, payload) or Exception instances

    def _adapter(self, game=None):
        return Soren91CoordinatorAdapter(self.g, game or self.game, self.spec)

    @contextmanager
    def _http(self):
        def fake_urlopen(request, timeout=None):
            body = request.data.decode("utf-8") if request.data else None
            self.http_calls.append(
                {
                    "url": request.full_url,
                    "method": request.get_method(),
                    "auth": request.get_header("Authorization"),
                    "content_type": request.get_header("Content-type"),
                    "body": body,
                }
            )
            if not self.http_plan:
                return FakeHttpResponse(200, {"ok": True, "running": False})
            item = self.http_plan.pop(0)
            if isinstance(item, Exception):
                raise item
            status, payload = item
            if status >= 400:
                raise urllib.error.HTTPError(
                    request.full_url, status, "error", {}, io.BytesIO(json.dumps(payload).encode())
                )
            return FakeHttpResponse(status, payload)

        with mock.patch(
            "docich.adapters.soren91.urllib.request.urlopen", side_effect=fake_urlopen
        ):
            yield

    @contextmanager
    def _bound_listener(self, bound=True):
        with mock.patch(
            "docich.adapters.soren91._udp_listener_bound", return_value=bool(bound)
        ):
            yield

    @contextmanager
    def _ffplay(self, protocols="muxers:\nsrt\n"):
        completed = SimpleNamespace(returncode=0, stdout=protocols, stderr="")
        with mock.patch("docich.procs.which", return_value="/usr/bin/ffplay"):
            with mock.patch("subprocess.run", return_value=completed):
                yield


class TestTailscaleValidation(Soren91AdapterTestBase):
    def test_tailscale_range(self):
        for host in ("100.64.0.1", "100.100.5.5", "100.127.255.255", MAC_IP):
            self.assertTrue(is_tailscale_ipv4(host), host)
        for host in ("", "127.0.0.1", "192.168.1.1", "8.8.8.8", "100.63.255.255",
                     "100.128.0.1", "101.64.0.1", "hostname.ts.net", "100.90.0.1/32",
                     "100.90.0", "100.90.0.1.5", "0.0.0.0"):
            self.assertFalse(is_tailscale_ipv4(host), host)

    def test_public_agent_host_rejected(self):
        with mock.patch.dict("os.environ", {"SOREN91_MACOS_AGENT_BASE_URL": "http://8.8.8.8:19191"}):
            with self.assertRaises(AdapterError):
                self._adapter()._agent_base()

    def test_missing_env_throws(self):
        for key in ("SOREN91_MACOS_AGENT_BASE_URL", "SOREN91_LOCAL_AGENT_TOKEN",
                    "SOREN91_OCI_TAILSCALE_IP"):
            with self.subTest(key=key), mock.patch.dict("os.environ", {key: ""}):
                adapter = self._adapter()
                with self.assertRaises(AdapterError):
                    if key == "SOREN91_MACOS_AGENT_BASE_URL":
                        adapter._agent_base()
                    elif key == "SOREN91_LOCAL_AGENT_TOKEN":
                        adapter._agent_token()
                    else:
                        adapter._oci_ip()

    def test_agent_url_requires_explicit_port_and_http(self):
        for bad in (f"http://{MAC_IP}", f"gopher://{MAC_IP}:19191",
                    f"http://user@{MAC_IP}:19191", f"http://example.com:19191"):
            with self.subTest(bad=bad), mock.patch.dict(
                "os.environ", {"SOREN91_MACOS_AGENT_BASE_URL": bad}
            ):
                with self.assertRaises(AdapterError):
                    self._adapter()._agent_base()

    def test_token_never_in_error_text(self):
        secret = "SECRET-TOKEN-abcdef-0123456789"
        with mock.patch.dict(
            "os.environ",
            {"SOREN91_LOCAL_AGENT_TOKEN": secret, "SOREN91_OCI_TAILSCALE_IP": "8.8.8.8"},
        ):
            adapter = self._adapter()
            with self.assertRaises(AdapterError) as caught:
                adapter.preflight(time.monotonic() + 30, None)
            self.assertNotIn(secret, str(caught.exception))


class TestSrtAndViewer(Soren91AdapterTestBase):
    def test_srt_urls(self):
        adapter = self._adapter()
        self.assertEqual(adapter.caller_srt_url(), f"srt://{OCI_IP}:19192?mode=caller")
        self.assertEqual(adapter.listener_srt_url(), f"srt://{OCI_IP}:19192?mode=listener")

    def test_viewer_command_bare_without_viewport(self):
        adapter = self._adapter()
        cmd = adapter._xterm_command()
        self.assertEqual(cmd[0], "ffplay")
        self.assertIn(f"srt://{OCI_IP}:19192?mode=listener", cmd)
        self.assertNotIn("presentation.py", " ".join(cmd))

    def test_viewer_command_wrapped_with_viewport(self):
        self.g.display.viewport_x = 0
        self.g.display.viewport_y = 90
        self.g.display.viewport_width = 960
        self.g.display.viewport_height = 540
        adapter = self._adapter()
        cmd = adapter._xterm_command()
        self.assertIn("presentation.py", cmd[1])
        self.assertIn("--title", cmd)
        self.assertIn("docich-present-g3-abcdef12", cmd)
        self.assertIn(f"srt://{OCI_IP}:19192?mode=listener", cmd)

    def test_no_round_boundary_capability(self):
        adapter = self._adapter()
        self.assertFalse(adapter.requires_round_boundary)
        self.assertIsNone(adapter.request_round_boundary)
        self.assertIsNone(adapter.cancel_round_boundary)

    def test_registry_resolves_soren91(self):
        adapter = make_coordinator_adapter(self.g, self.spec)
        self.assertIsInstance(adapter, Soren91CoordinatorAdapter)
        self.assertEqual(adapter.name, "soren91")


class TestPreflight(Soren91AdapterTestBase):
    def test_preflight_ok_without_agent(self):
        with self._ffplay():
            self._adapter().preflight(time.monotonic() + 30, None)

    def test_preflight_rejects_ffplay_without_srt(self):
        with mock.patch("docich.procs.which", return_value="/usr/bin/ffplay"):
            completed = SimpleNamespace(returncode=0, stdout="muxers:\nrtsp\n", stderr="")
            with mock.patch("subprocess.run", return_value=completed):
                with self.assertRaisesRegex(AdapterError, "SRT"):
                    self._adapter().preflight(time.monotonic() + 30, None)

    def test_preflight_requires_bot_when_agent_enabled(self):
        self.game.agent.enabled = True
        with self._ffplay():
            with mock.patch("docich.procs.which", return_value="/usr/bin/node"):
                with self.assertRaisesRegex(AdapterError, "bot"):
                    self._adapter().preflight(time.monotonic() + 30, None)

    def test_preflight_accepts_bot_file_when_agent_enabled(self):
        bot = self.root / "main.mjs"
        bot.write_text("// bot\n", encoding="utf-8")
        (self.root / "config" / "games" / "soren91.toml").write_text(
            '[game]\nname="soren91"\ntitle="Soren91"\nadapter="soren91"\n'
            '[agent]\nenabled=true\n'
            '[lifecycle]\nrequire_round_boundary=false\n'
            f'[soren91]\ncdp_port=9322\nsrt_port=19192\nffplay_bin="ffplay"\nbot_path="{bot}"\n',
            encoding="utf-8",
        )
        game = config.load_game(self.g, "soren91")
        with mock.patch(
            "docich.procs.which",
            side_effect=lambda name: f"/usr/bin/{name}",
        ):
            with self._ffplay():
                self._adapter(game).preflight(time.monotonic() + 30, None)


class TestLifecycle(Soren91AdapterTestBase):
    def test_materialize_posts_start_with_caller_url(self):
        with self._ffplay(), self._bound_listener(True), self._http():
            self.http_plan = [
                (200, {"ok": True, "running": False}),
                (202, {"ok": True, "started": True}),
            ]
            self._adapter().materialize_runtime(time.monotonic() + 30, None)
        self.assertIn("docich-game-g3", self.tmux.sessions)
        self.assertIn("docich-game-g3:game-g3", self.tmux.windows)
        posts = [c for c in self.http_calls if c["method"] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertTrue(posts[0]["url"].endswith("/v1/start"))
        self.assertEqual(
            json.loads(posts[0]["body"])["srtUrl"], f"srt://{OCI_IP}:19192?mode=caller"
        )
        self.assertEqual(posts[0]["auth"], f"Bearer {TOKEN}")

    def test_materialize_skips_post_when_already_running(self):
        with self._ffplay(), self._bound_listener(True), self._http():
            self.http_plan = [(200, {"ok": True, "running": True})]
            self._adapter().materialize_runtime(time.monotonic() + 30, None)
        self.assertEqual(
            [c for c in self.http_calls if c["method"] == "POST"], []
        )

    def test_materialize_treats_409_as_started(self):
        with self._ffplay(), self._bound_listener(True), self._http():
            self.http_plan = [
                (200, {"ok": True, "running": False}),
                (409, {"ok": False, "error": "already running"}),
            ]
            self._adapter().materialize_runtime(time.monotonic() + 30, None)

    def test_materialize_fails_closed_when_agent_unreachable(self):
        with self._ffplay(), self._bound_listener(True), self._http():
            self.http_plan = [urllib.error.URLError("refused")]
            with self.assertRaises(AdapterError):
                self._adapter().materialize_runtime(time.monotonic() + 30, None)

    def test_materialize_fails_closed_without_listener(self):
        with self._ffplay(), self._bound_listener(False), self._http():
            with self.assertRaises(ReadinessTimeoutError):
                self._adapter().materialize_runtime(time.monotonic() + 0.05, None)
        self.assertEqual(self.http_calls, [])

    def test_readiness_ok(self):
        adapter = self._adapter()
        with self._bound_listener(True), self._http():
            self.http_plan = [(200, {"ok": True, "running": True})]
            adapter.materialize_runtime(time.monotonic() + 30, None)
            self.http_plan = [(200, {"ok": True, "running": True})]
            adapter.readiness(time.monotonic() + 30, None)

    def test_readiness_fails_closed_when_renderer_not_running(self):
        adapter = self._adapter()
        with self._bound_listener(True), self._http():
            self.http_plan = [(200, {"ok": True, "running": True})]
            adapter.materialize_runtime(time.monotonic() + 30, None)
            self.http_plan = [(200, {"ok": True, "running": False})]
            with self.assertRaises(ReadinessTimeoutError):
                adapter.readiness(time.monotonic() + 1.5, None)

    def test_alive_reflects_agent_status(self):
        adapter = self._adapter()
        with self._bound_listener(True), self._http():
            self.http_plan = [(200, {"ok": True, "running": True})]
            adapter.materialize_runtime(time.monotonic() + 30, None)
            self.http_plan = [(200, {"ok": True, "running": True})]
            self.assertTrue(adapter.alive(time.monotonic() + 30, None))
            self.http_plan = [(200, {"ok": True, "running": False})]
            self.assertFalse(adapter.alive(time.monotonic() + 30, None))
            self.http_plan = [urllib.error.URLError("down")]
            self.assertFalse(adapter.alive(time.monotonic() + 30, None))

    def test_cleanup_posts_stop_and_tears_down(self):
        adapter = self._adapter()
        with self._bound_listener(True), self._http():
            self.http_plan = [(200, {"ok": True, "running": True})]
            adapter.materialize_runtime(time.monotonic() + 30, None)
        with self._bound_listener(False), self._http():
            self.http_plan = [(202, {"ok": True, "stopping": True})]
            adapter.cleanup_runtime(time.monotonic() + 30, None)
        posts = [c for c in self.http_calls if c["method"] == "POST"]
        self.assertTrue(any(c["url"].endswith("/v1/stop") for c in posts))
        self.assertNotIn("docich-game-g3:game-g3", self.tmux.windows)
        self.assertNotIn("docich-game-g3", self.tmux.sessions)

    def test_cleanup_survives_dead_agent(self):
        adapter = self._adapter()
        with self._bound_listener(True), self._http():
            self.http_plan = [(200, {"ok": True, "running": True})]
            adapter.materialize_runtime(time.monotonic() + 30, None)
        with self._bound_listener(False), self._http():
            self.http_plan = [urllib.error.URLError("gone")]
            adapter.cleanup_runtime(time.monotonic() + 30, None)
        self.assertNotIn("docich-game-g3", self.tmux.sessions)

    def test_cleanup_waits_for_udp_release(self):
        adapter = self._adapter()
        with self._bound_listener(True), self._http():
            self.http_plan = [(200, {"ok": True, "running": True})]
            adapter.materialize_runtime(time.monotonic() + 30, None)
        with mock.patch(
            "docich.adapters.soren91._udp_listener_bound", side_effect=[True, True, False]
        ), self._http():
            self.http_plan = [(202, {"ok": True, "stopping": True})]
            adapter.cleanup_runtime(time.monotonic() + 30, None)
        self.assertNotIn("docich-game-g3", self.tmux.sessions)

    def test_cleanup_throws_when_udp_port_stays_bound(self):
        adapter = self._adapter()
        with self._bound_listener(True), self._http():
            self.http_plan = [(200, {"ok": True, "running": True})]
            adapter.materialize_runtime(time.monotonic() + 30, None)
        with self._bound_listener(True), self._http():
            self.http_plan = [(202, {"ok": True, "stopping": True})]
            with self.assertRaises(AdapterError):
                adapter.cleanup_runtime(time.monotonic() + 0.3, None)


class TestUdpListenerParsing(Soren91AdapterTestBase):
    def test_ss_bound_ipv4(self):
        out = "UNCONN 0      0        100.90.0.2:19192      0.0.0.0:*          \n"
        self.assertTrue(parse_udp_listeners(out, 19192))

    def test_ss_unbound(self):
        out = "UNCONN 0      0        100.90.0.2:19199      0.0.0.0:*          \n"
        self.assertFalse(parse_udp_listeners(out, 19192))

    def test_ss_empty(self):
        self.assertFalse(parse_udp_listeners("", 19192))
        self.assertFalse(parse_udp_listeners("Netid State Recv-Q Send-Q Local Address:Port\n", 19192))

    def test_ss_bound_ipv6_and_udp_netid(self):
        out = "udp    UNCONN  0       0       [::]:19192                 [::]:*              \n"
        self.assertTrue(parse_udp_listeners(out, 19192))

    def test_ss_port_prefix_does_not_match(self):
        out = "UNCONN 0      0        100.90.0.2:191924     0.0.0.0:*          \n"
        self.assertFalse(parse_udp_listeners(out, 19192))

    def test_ss_ignores_tcp_rows(self):
        out = (
            "tcp    LISTEN  0       128     100.90.0.2:19192      0.0.0.0:*          \n"
            "UNCONN 0       0       100.90.0.2:19199             0.0.0.0:*          \n"
        )
        self.assertFalse(parse_udp_listeners(out, 19192))


class TestBotAgent(Soren91AdapterTestBase):
    def _enabled_game(self, extra_soren91=""):
        bot = self.root / "main.mjs"
        bot.write_text("// bot\n", encoding="utf-8")
        (self.root / "config" / "games" / "soren91.toml").write_text(
            '[game]\nname="soren91"\ntitle="Soren91"\nadapter="soren91"\n'
            '[agent]\nenabled=true\n'
            '[lifecycle]\nrequire_round_boundary=false\n'
            f'[soren91]\ncdp_port=9322\nsrt_port=19192\nffplay_bin="ffplay"\nbot_path="{bot}"\n'
            f"{extra_soren91}",
            encoding="utf-8",
        )
        return config.load_game(self.g, "soren91")

    def test_start_agent_noop_when_disabled(self):
        adapter = self._adapter()
        adapter.start_agent(time.monotonic() + 30, None)
        self.assertEqual(self.tmux.windows, {})

    def test_start_agent_launches_bot_with_remote_cdp(self):
        game = self._enabled_game()
        adapter = self._adapter(game)
        with mock.patch("docich.procs.which", return_value="/usr/bin/node"):
            adapter.start_agent(time.monotonic() + 30, None)
        target = "docich-game-g3:agent-g3"
        self.assertIn(target, self.tmux.windows)
        cmd = self.tmux.window_cmds[target]
        self.assertEqual(cmd[0], "/usr/bin/node")
        self.assertTrue(cmd[1].endswith("main.mjs"))
        self.assertEqual(
            self.tmux.window_env[target]["SOREN91_REMOTE_CDP_URL"],
            f"http://{MAC_IP}:9322",
        )
        self.assertEqual(self.tmux.window_env[target]["SOREN91_SHARED_BROWSER"], "1")
        adapter.stop_agent(time.monotonic() + 30, None)
        self.assertNotIn(target, self.tmux.windows)

    def test_materialize_starts_bot_and_start_agent_is_idempotent(self):
        game = self._enabled_game()
        adapter = self._adapter(game)
        with mock.patch("docich.procs.which", return_value="/usr/bin/node"):
            with self._ffplay(), self._bound_listener(True), self._http():
                self.http_plan = [
                    (200, {"ok": True, "running": False}),
                    (202, {"ok": True, "started": True}),
                ]
                adapter.materialize_runtime(time.monotonic() + 30, None)
        target = "docich-game-g3:agent-g3"
        self.assertIn(target, self.tmux.windows)
        self.assertEqual(
            self.tmux.window_env[target]["SOREN91_REMOTE_CDP_URL"],
            f"http://{MAC_IP}:9322",
        )
        self.assertEqual(self.tmux.window_cwd[target], str(self.root))
        with mock.patch("docich.procs.which", return_value="/usr/bin/node"):
            adapter.start_agent(time.monotonic() + 30, None)
        self.assertIn(target, self.tmux.windows)
        self.assertEqual(len(self.tmux.windows), 2)  # game + agent, no duplicate

    def test_materialize_skips_bot_when_disabled(self):
        adapter = self._adapter()
        with self._bound_listener(True), self._http():
            self.http_plan = [
                (200, {"ok": True, "running": False}),
                (202, {"ok": True, "started": True}),
            ]
            adapter.materialize_runtime(time.monotonic() + 30, None)
        self.assertNotIn("docich-game-g3:agent-g3", self.tmux.windows)

    def test_cleanup_stops_bot_window(self):
        game = self._enabled_game()
        adapter = self._adapter(game)
        with mock.patch("docich.procs.which", return_value="/usr/bin/node"):
            with self._bound_listener(True), self._http():
                self.http_plan = [
                    (200, {"ok": True, "running": False}),
                    (202, {"ok": True, "started": True}),
                ]
                adapter.materialize_runtime(time.monotonic() + 30, None)
        target = "docich-game-g3:agent-g3"
        self.assertIn(target, self.tmux.windows)
        with self._bound_listener(False), self._http():
            self.http_plan = [(202, {"ok": True, "stopping": True})]
            adapter.cleanup_runtime(time.monotonic() + 30, None)
        self.assertNotIn(target, self.tmux.windows)

    def test_bot_cwd_explicit_and_preflight_rejects_missing_dir(self):
        iso = self.root / "iso"
        iso.mkdir()
        game = self._enabled_game(f'bot_cwd="{iso}"\n')
        adapter = self._adapter(game)
        self.assertEqual(adapter._bot_cwd(), str(iso))
        with mock.patch("docich.procs.which", return_value="/usr/bin/node"):
            with self._ffplay():
                adapter.preflight(time.monotonic() + 30, None)
        game = self._enabled_game(f'bot_cwd="{self.root / "nope"}"\n')
        adapter = self._adapter(game)
        with mock.patch("docich.procs.which", return_value="/usr/bin/node"):
            with self._ffplay():
                with self.assertRaisesRegex(AdapterError, "作業ディレクトリ"):
                    adapter.preflight(time.monotonic() + 30, None)

    def test_viewer_wait_sec_default_and_validation(self):
        adapter = self._adapter()
        self.assertEqual(adapter.viewer_wait_sec, 120)
        self.g.display.viewport_x = 0
        self.g.display.viewport_y = 90
        self.g.display.viewport_width = 960
        self.g.display.viewport_height = 540
        cmd = adapter._xterm_command()
        self.assertIn("--viewer-wait-sec", cmd)
        self.assertEqual(cmd[cmd.index("--viewer-wait-sec") + 1], "120")
        (self.root / "config" / "games" / "soren91.toml").write_text(
            '[game]\nname="soren91"\ntitle="Soren91"\nadapter="soren91"\n'
            '[agent]\nenabled=false\n'
            '[lifecycle]\nrequire_round_boundary=false\n'
            '[soren91]\ncdp_port=9322\nsrt_port=19192\nffplay_bin="ffplay"\nbot_path=""\n'
            'viewer_wait_sec=5\n',
            encoding="utf-8",
        )
        game = config.load_game(self.g, "soren91")
        with self.assertRaisesRegex(AdapterError, "viewer_wait_sec"):
            self._adapter(game)


class FakeCoordinator:
    def __init__(self, current):
        self.current = current
        self.calls = []

    def switch(self, game):
        self.calls.append(("switch", game))
        self.current[0] = game
        return SimpleNamespace(status="succeeded", error_code=None, detail=None)


class ManualSoren91CornerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        games = self.root / "config" / "games"
        games.mkdir(parents=True)
        (self.root / "config" / "docich.toml").write_text(
            '[paths]\nstate_dir="run"\ngames_dir="config/games"\n', encoding="utf-8"
        )
        (games / "soren91.toml").write_text(
            '[game]\nname="soren91"\ntitle="Soren91"\nadapter="soren91"\n'
            '[agent]\nenabled=false\n'
            '[lifecycle]\nrequire_round_boundary=false\n'
            '[soren91]\ncdp_port=9322\nsrt_port=19192\n',
            encoding="utf-8",
        )
        (games / "sorengame.toml").write_text(
            '[game]\nname="sorengame"\ntitle="Soren"\nadapter="browser"\n', encoding="utf-8"
        )
        self.g = config.load_global(self.root)

    def _manager(self, coordinator, current, **kwargs):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from docich.soren91_corner_manual import ManualSoren91CornerManager

        now = datetime(2026, 9, 12, 17, 32, tzinfo=ZoneInfo("Asia/Tokyo"))
        slept = []
        mgr = ManualSoren91CornerManager(
            self.g,
            duration_minutes=kwargs.get("duration_minutes", 5),
            coordinator=coordinator,
            now=lambda: now,
            sleep=lambda seconds: slept.append(seconds),
            active_game_reader=lambda: current[0],
            ensure_runtime=lambda: None,
        )
        return mgr, slept

    def test_manual_runner_switches_and_restores_with_own_state(self):
        current = ["sorengame"]
        coordinator = FakeCoordinator(current)
        mgr, slept = self._manager(coordinator, current)
        result = mgr.start()
        self.assertEqual(result.status, "completed")
        self.assertEqual(
            coordinator.calls, [("switch", "soren91"), ("switch", "sorengame")]
        )
        self.assertEqual(slept, [300.0])
        self.assertFalse((self.g.state_dir / "retro_corner.json").exists())
        self.assertFalse((self.g.state_dir / "retro_corner_manual.json").exists())
        self.assertTrue((self.g.state_dir / "soren91_corner_manual.json").exists())

    def test_manual_runner_rejects_non_soren91_game_toml(self):
        (self.root / "config" / "games" / "soren91.toml").write_text(
            '[game]\nname="soren91"\ntitle="X"\nadapter="cli"\n'
            '[cli]\ncommand="robots"\n',
            encoding="utf-8",
        )
        current = ["sorengame"]
        coordinator = FakeCoordinator(current)
        mgr, _slept = self._manager(coordinator, current)
        with self.assertRaisesRegex(Exception, "soren91"):
            mgr.start()

    def test_manual_parser_defaults(self):
        from docich.soren91_corner_manual import _parser

        args = _parser().parse_args(["start"])
        self.assertEqual(args.duration_minutes, 5)
        args = _parser().parse_args(["start", "--duration-minutes", "10"])
        self.assertEqual(args.duration_minutes, 10)
        args = _parser().parse_args(["recover"])
        self.assertEqual(args.command, "recover")


if __name__ == "__main__":
    unittest.main()

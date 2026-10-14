import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config, state as state_mod  # noqa: E402
from docich.actions import Action  # noqa: E402
from docich.adapters import base  # noqa: E402
from docich.adapters import browser  # noqa: E402


class FakeXKit:
    def __init__(self, find_window_return: str | None = "0xabc"):
        self.calls: list[tuple] = []
        self.find_window_return = find_window_return

    def find_window(self, pattern):
        self.calls.append(("find_window", pattern))
        return self.find_window_return

    def focus(self, window_id):
        self.calls.append(("focus", window_id))

    def tap(self, keys, hold_ms):
        self.calls.append(("tap", list(keys), hold_ms))

    def type_text(self, text):
        self.calls.append(("type_text", text))

    def mouse_click(self, x, y, button=1):
        self.calls.append(("mouse_click", x, y, button))

    def screenshot(self, out_path, width, height):
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"fake-png")
        self.calls.append(("screenshot", str(out_path), width, height))
        return out_path


class BrowserAdapterTestBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self._tmpdir.name)
        self.g = config.load_global(self.repo_root)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _make_ctx(self, *, browser_raw: dict, xkit=None) -> base.AdapterContext:
        game = config.GameConfig(
            name="sorengame",
            title="soren game",
            adapter="browser",
            raw={"browser": browser_raw},
            agent=config.GameAgentConfig(),
            path=self.repo_root / "config" / "games" / "sorengame.toml",
        )
        state = state_mod.State(self.g)
        return base.AdapterContext(g=self.g, game=game, state=state, tmux=None, xkit=xkit or FakeXKit())


class TestCommandBuild(BrowserAdapterTestBase):
    def test_basic_command_structure(self):
        ctx = self._make_ctx(browser_raw={"url": "https://example.invalid/game", "binary": "/usr/bin/chromium"})
        adapter = browser.BrowserAdapter(ctx)
        cmd = adapter.command()

        self.assertEqual(cmd[0], "/usr/bin/chromium")
        self.assertIn(f"--window-size={self.g.display.width},{self.g.display.height}", cmd)
        self.assertIn("--window-position=0,0", cmd)
        self.assertIn("--no-first-run", cmd)
        self.assertIn("--disable-infobars", cmd)
        self.assertIn("--disable-session-crashed-bubble", cmd)
        self.assertIn("--autoplay-policy=no-user-gesture-required", cmd)
        self.assertTrue(any(a.startswith("--user-data-dir=") for a in cmd))
        self.assertIn("--kiosk", cmd)
        self.assertEqual(cmd[-1], "https://example.invalid/game")

    def test_kiosk_false_omits_flag(self):
        ctx = self._make_ctx(
            browser_raw={"url": "https://example.invalid/game", "binary": "/usr/bin/chromium", "kiosk": False}
        )
        adapter = browser.BrowserAdapter(ctx)
        cmd = adapter.command()
        self.assertNotIn("--kiosk", cmd)
        self.assertEqual(cmd[-1], "https://example.invalid/game")

    def test_extra_args_are_inserted_before_kiosk_and_url(self):
        ctx = self._make_ctx(
            browser_raw={
                "url": "https://example.invalid/game",
                "binary": "/usr/bin/chromium",
                "extra_args": ["--mute-audio", "--disable-gpu"],
            }
        )
        adapter = browser.BrowserAdapter(ctx)
        cmd = adapter.command()
        self.assertIn("--mute-audio", cmd)
        self.assertIn("--disable-gpu", cmd)
        i_extra = cmd.index("--mute-audio")
        i_kiosk = cmd.index("--kiosk")
        i_url = cmd.index("https://example.invalid/game")
        self.assertLess(i_extra, i_kiosk)
        self.assertLess(i_kiosk, i_url)

    def test_missing_url_without_launch_command_raises(self):
        ctx = self._make_ctx(browser_raw={"binary": "/usr/bin/chromium"})
        adapter = browser.BrowserAdapter(ctx)
        with self.assertRaises(base.AdapterError):
            adapter.command()


class TestLaunchCommandOverride(BrowserAdapterTestBase):
    def test_launch_command_is_returned_verbatim(self):
        ctx = self._make_ctx(
            browser_raw={"launch_command": ["bash", "-lc", "cd ~/soren && ./start.sh"]}
        )
        adapter = browser.BrowserAdapter(ctx)
        cmd = adapter.command()
        self.assertEqual(cmd, ["bash", "-lc", "cd ~/soren && ./start.sh"])

    def test_launch_command_skips_binary_and_url_requirements(self):
        # url も binary も無くてもエラーにならないこと (launch_command 優先)
        ctx = self._make_ctx(browser_raw={"launch_command": ["echo", "hi"]})
        adapter = browser.BrowserAdapter(ctx)
        cmd = adapter.command()
        self.assertEqual(cmd, ["echo", "hi"])

    def test_prepare_skips_binary_resolution_when_launch_command_set(self):
        ctx = self._make_ctx(browser_raw={"launch_command": ["echo", "hi"]})
        adapter = browser.BrowserAdapter(ctx)
        with mock.patch("docich.adapters.browser.procs.which", return_value=None) as which_mock:
            adapter.prepare()  # should not raise even though "which" would fail
        which_mock.assert_not_called()
        self.assertTrue((ctx.state.state_dir / "browser-profile").is_dir())


class TestBinaryAutoDetection(BrowserAdapterTestBase):
    def test_auto_picks_first_found_candidate(self):
        ctx = self._make_ctx(browser_raw={"url": "https://example.invalid/game", "binary": "auto"})
        adapter = browser.BrowserAdapter(ctx)

        def fake_which(name):
            return "/usr/bin/chromium-browser" if name == "chromium-browser" else None

        with mock.patch("docich.adapters.browser.procs.which", side_effect=fake_which):
            adapter.prepare()
        cmd = adapter.command()
        self.assertEqual(cmd[0], "/usr/bin/chromium-browser")

    def test_auto_failure_raises_adapter_error(self):
        ctx = self._make_ctx(browser_raw={"url": "https://example.invalid/game", "binary": "auto"})
        adapter = browser.BrowserAdapter(ctx)
        with mock.patch("docich.adapters.browser.procs.which", return_value=None):
            with self.assertRaises(base.AdapterError) as ctx_err:
                adapter.prepare()
        msg = str(ctx_err.exception)
        self.assertIn("chromium", msg)

    def test_explicit_binary_skips_auto_detection(self):
        ctx = self._make_ctx(browser_raw={"url": "https://example.invalid/game", "binary": "/opt/chrome/chrome"})
        adapter = browser.BrowserAdapter(ctx)
        with mock.patch("docich.adapters.browser.procs.which", return_value=None) as which_mock:
            adapter.prepare()
        which_mock.assert_not_called()
        self.assertEqual(adapter.command()[0], "/opt/chrome/chrome")


class TestPrepare(BrowserAdapterTestBase):
    def test_prepare_creates_profile_dir(self):
        ctx = self._make_ctx(browser_raw={"url": "https://example.invalid/game", "binary": "/usr/bin/chromium"})
        adapter = browser.BrowserAdapter(ctx)
        adapter.prepare()
        self.assertTrue((ctx.state.state_dir / "browser-profile").is_dir())


class TestObserve(BrowserAdapterTestBase):
    def test_observe_returns_screenshot_kind(self):
        xkit = FakeXKit()
        ctx = self._make_ctx(
            browser_raw={"url": "https://example.invalid/game", "binary": "/usr/bin/chromium"}, xkit=xkit
        )
        adapter = browser.BrowserAdapter(ctx)
        obs = adapter.observe()
        self.assertEqual(obs.kind, "screenshot")
        self.assertIsNone(obs.text)
        self.assertEqual(obs.adapter, "browser")
        self.assertEqual(obs.screenshot, str(ctx.state.screenshots_dir / "latest.png"))


class TestAct(BrowserAdapterTestBase):
    def test_key_action_focuses_then_taps(self):
        xkit = FakeXKit(find_window_return="0x42")
        ctx = self._make_ctx(
            browser_raw={"url": "https://example.invalid/game", "binary": "/usr/bin/chromium"}, xkit=xkit
        )
        adapter = browser.BrowserAdapter(ctx)
        adapter.act(Action(type="key", keys=["Tab"], hold_ms=100))
        self.assertIn(("find_window", browser.DEFAULT_WINDOW_PATTERN), xkit.calls)
        self.assertIn(("focus", "0x42"), xkit.calls)
        self.assertIn(("tap", ["Tab"], 100), xkit.calls)

    def test_key_action_uses_custom_window_pattern(self):
        xkit = FakeXKit(find_window_return="0x42")
        ctx = self._make_ctx(
            browser_raw={
                "url": "https://example.invalid/game",
                "binary": "/usr/bin/chromium",
                "window_pattern": "soren",
            },
            xkit=xkit,
        )
        adapter = browser.BrowserAdapter(ctx)
        adapter.act(Action(type="key", keys=["Tab"], hold_ms=100))
        self.assertIn(("find_window", "soren"), xkit.calls)

    def test_text_action_focuses_then_types(self):
        xkit = FakeXKit(find_window_return="0x42")
        ctx = self._make_ctx(
            browser_raw={"url": "https://example.invalid/game", "binary": "/usr/bin/chromium"}, xkit=xkit
        )
        adapter = browser.BrowserAdapter(ctx)
        adapter.act(Action(type="text", text="hello"))
        self.assertIn(("focus", "0x42"), xkit.calls)
        self.assertIn(("type_text", "hello"), xkit.calls)

    def test_mouse_action_clicks_without_focus_call(self):
        xkit = FakeXKit(find_window_return="0x42")
        ctx = self._make_ctx(
            browser_raw={"url": "https://example.invalid/game", "binary": "/usr/bin/chromium"}, xkit=xkit
        )
        adapter = browser.BrowserAdapter(ctx)
        adapter.act(Action(type="mouse", x=10, y=20, button=1))
        self.assertIn(("mouse_click", 10, 20, 1), xkit.calls)
        self.assertFalse(any(c[0] == "focus" for c in xkit.calls))

    def test_missing_window_skips_focus_without_raising(self):
        xkit = FakeXKit(find_window_return=None)
        ctx = self._make_ctx(
            browser_raw={"url": "https://example.invalid/game", "binary": "/usr/bin/chromium"}, xkit=xkit
        )
        adapter = browser.BrowserAdapter(ctx)
        adapter.act(Action(type="key", keys=["Tab"]))  # should not raise
        self.assertFalse(any(c[0] == "focus" for c in xkit.calls))

    def test_wait_action_is_noop(self):
        xkit = FakeXKit()
        ctx = self._make_ctx(
            browser_raw={"url": "https://example.invalid/game", "binary": "/usr/bin/chromium"}, xkit=xkit
        )
        adapter = browser.BrowserAdapter(ctx)
        adapter.act(Action(type="wait", ms=100))
        self.assertEqual(xkit.calls, [])

    def test_pad_and_special_raise(self):
        ctx = self._make_ctx(browser_raw={"url": "https://example.invalid/game", "binary": "/usr/bin/chromium"})
        adapter = browser.BrowserAdapter(ctx)
        with self.assertRaises(base.AdapterError):
            adapter.act(Action(type="pad", buttons=["a"]))
        with self.assertRaises(base.AdapterError):
            adapter.act(Action(type="special", key="Escape"))


if __name__ == "__main__":
    unittest.main()

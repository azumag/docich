import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config, state as state_mod  # noqa: E402
from docich.actions import Action  # noqa: E402
from docich.adapters import base  # noqa: E402
from docich.adapters import retroarch  # noqa: E402


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

    def screenshot(self, out_path, width, height):
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"fake-png")
        self.calls.append(("screenshot", str(out_path), width, height))
        return out_path


class RetroArchTestBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self._tmpdir.name)
        (self.repo_root / "games" / "roms").mkdir(parents=True)
        self.rom_path = self.repo_root / "games" / "roms" / "hanjuku-hero.sfc"
        self.rom_path.write_bytes(b"fake-rom")

        # ダミーの core .so (glob に依存しない絶対パス指定用)
        self.core_path = self.repo_root / "fake_core_libretro.so"
        self.core_path.write_bytes(b"fake-core")

    def tearDown(self):
        self._tmpdir.cleanup()

    def _make_ctx(self, *, retroarch_raw: dict, display_kwargs: dict | None = None,
                  audio_enabled: bool = True, xkit=None) -> base.AdapterContext:
        toml_lines = ["[display]"]
        for k, v in (display_kwargs or {}).items():
            toml_lines.append(f"{k} = {v}")
        toml_lines.append("[audio]")
        toml_lines.append(f"enabled = {'true' if audio_enabled else 'false'}")
        toml_path = self.repo_root / "docich.toml"
        toml_path.write_text("\n".join(toml_lines) + "\n", encoding="utf-8")
        g = config.load_global(self.repo_root, config_path=toml_path)

        game = config.GameConfig(
            name="hanjuku-hero",
            title="半熟英雄 (SFC)",
            adapter="retroarch",
            raw={"retroarch": retroarch_raw},
            agent=config.GameAgentConfig(),
            path=self.repo_root / "config" / "games" / "hanjuku-hero.toml",
        )
        state = state_mod.State(g)
        return base.AdapterContext(g=g, game=game, state=state, tmux=None, xkit=xkit or FakeXKit())

    def _cfg_text(self, ctx: base.AdapterContext) -> str:
        return (ctx.state.retroarch_dir / "retroarch.cfg").read_text(encoding="utf-8")


class TestPrepareGeneratesCfg(RetroArchTestBase):
    def test_cfg_has_required_drivers_and_settings(self):
        ctx = self._make_ctx(retroarch_raw={"rom": "games/roms/hanjuku-hero.sfc", "core": str(self.core_path)})
        adapter = retroarch.RetroArchAdapter(ctx)
        adapter.prepare()
        text = self._cfg_text(ctx)

        self.assertIn('input_driver = "sdl2"', text)
        self.assertIn('video_driver = "sdl2"', text)
        self.assertIn('gamemode_enable = "false"', text)
        self.assertIn('input_exit_emulator = "nul"', text)
        self.assertIn('network_cmd_enable = "true"', text)
        self.assertIn('network_cmd_port = "55355"', text)
        self.assertIn('config_save_on_exit = "false"', text)
        self.assertIn('pause_nonactive = "false"', text)
        self.assertIn('menu_driver = "rgui"', text)

    def test_cfg_binds_all_twelve_buttons(self):
        ctx = self._make_ctx(retroarch_raw={"rom": "games/roms/hanjuku-hero.sfc", "core": str(self.core_path)})
        adapter = retroarch.RetroArchAdapter(ctx)
        adapter.prepare()
        text = self._cfg_text(ctx)

        expected = {
            "a": "x", "b": "z", "x": "s", "y": "a", "l": "q", "r": "w",
            "start": "enter", "select": "rshift",
            "up": "up", "down": "down", "left": "left", "right": "right",
        }
        for button, cfg_val in expected.items():
            self.assertIn(f'input_player1_{button} = "{cfg_val}"', text)

    def test_cfg_video_fullscreen_matches_display_size(self):
        ctx = self._make_ctx(
            retroarch_raw={"rom": "games/roms/hanjuku-hero.sfc", "core": str(self.core_path)},
            display_kwargs={"width": 640, "height": 480},
        )
        adapter = retroarch.RetroArchAdapter(ctx)
        adapter.prepare()
        text = self._cfg_text(ctx)
        self.assertIn('video_fullscreen = "true"', text)
        self.assertIn('video_fullscreen_x = "640"', text)
        self.assertIn('video_fullscreen_y = "480"', text)

    def test_audio_enable_follows_global_audio_setting(self):
        ctx_on = self._make_ctx(
            retroarch_raw={"rom": "games/roms/hanjuku-hero.sfc", "core": str(self.core_path)}, audio_enabled=True
        )
        retroarch.RetroArchAdapter(ctx_on).prepare()
        self.assertIn('audio_enable = "true"', self._cfg_text(ctx_on))

        ctx_off = self._make_ctx(
            retroarch_raw={"rom": "games/roms/hanjuku-hero.sfc", "core": str(self.core_path)}, audio_enabled=False
        )
        retroarch.RetroArchAdapter(ctx_off).prepare()
        self.assertIn('audio_enable = "false"', self._cfg_text(ctx_off))

    def test_prepare_creates_state_save_system_dirs(self):
        ctx = self._make_ctx(retroarch_raw={"rom": "games/roms/hanjuku-hero.sfc", "core": str(self.core_path)})
        adapter = retroarch.RetroArchAdapter(ctx)
        adapter.prepare()
        self.assertTrue((ctx.state.retroarch_dir / "states").is_dir())
        self.assertTrue((ctx.state.retroarch_dir / "saves").is_dir())
        self.assertTrue((ctx.state.retroarch_dir / "system").is_dir())

    def test_prepare_is_idempotent(self):
        ctx = self._make_ctx(retroarch_raw={"rom": "games/roms/hanjuku-hero.sfc", "core": str(self.core_path)})
        adapter = retroarch.RetroArchAdapter(ctx)
        adapter.prepare()
        adapter.prepare()  # should not raise, cfg overwritten cleanly
        self.assertTrue((ctx.state.retroarch_dir / "retroarch.cfg").is_file())


class TestPadMapOverride(RetroArchTestBase):
    def test_pad_map_overrides_cfg_value_and_xdotool_key(self):
        ctx = self._make_ctx(
            retroarch_raw={
                "rom": "games/roms/hanjuku-hero.sfc",
                "core": str(self.core_path),
                "pad_map": {"a": "space", "start": "p"},
            }
        )
        adapter = retroarch.RetroArchAdapter(ctx)
        adapter.prepare()
        text = self._cfg_text(ctx)
        self.assertIn('input_player1_a = "space"', text)
        self.assertIn('input_player1_start = "p"', text)

        buttons = adapter._resolved_buttons()
        self.assertEqual(buttons["a"], ("space", "space"))
        self.assertEqual(buttons["start"], ("p", "p"))
        # 上書きしなかったボタンは既定のまま
        self.assertEqual(buttons["b"], ("z", "z"))

    def test_pad_map_known_translation_tokens(self):
        ctx = self._make_ctx(
            retroarch_raw={
                "rom": "games/roms/hanjuku-hero.sfc",
                "core": str(self.core_path),
                "pad_map": {"a": "rshift", "b": "lshift", "x": "up", "y": "enter"},
            }
        )
        adapter = retroarch.RetroArchAdapter(ctx)
        buttons = adapter._resolved_buttons()
        self.assertEqual(buttons["a"], ("rshift", "Shift_R"))
        self.assertEqual(buttons["b"], ("lshift", "Shift_L"))
        self.assertEqual(buttons["x"], ("up", "Up"))
        self.assertEqual(buttons["y"], ("enter", "Return"))

    def test_pad_map_unknown_token_raises(self):
        ctx = self._make_ctx(
            retroarch_raw={
                "rom": "games/roms/hanjuku-hero.sfc",
                "core": str(self.core_path),
                "pad_map": {"a": "f12"},
            }
        )
        adapter = retroarch.RetroArchAdapter(ctx)
        with self.assertRaises(base.AdapterError):
            adapter._resolved_buttons()

    def test_pad_map_unknown_button_name_raises(self):
        ctx = self._make_ctx(
            retroarch_raw={
                "rom": "games/roms/hanjuku-hero.sfc",
                "core": str(self.core_path),
                "pad_map": {"turbo": "x"},
            }
        )
        adapter = retroarch.RetroArchAdapter(ctx)
        with self.assertRaises(base.AdapterError):
            adapter._resolved_buttons()


class TestCoreAutoDiscovery(RetroArchTestBase):
    def test_auto_picks_first_hit_in_priority_order(self):
        ctx = self._make_ctx(retroarch_raw={"rom": "games/roms/hanjuku-hero.sfc", "core": "auto"})
        adapter = retroarch.RetroArchAdapter(ctx)

        def fake_glob(pattern):
            if "bsnes_mercury_performance" in pattern:
                return ["/usr/lib/aarch64-linux-gnu/libretro/bsnes_mercury_performance_libretro.so"]
            if "snes9x" in pattern:
                return []
            return []

        with mock.patch("docich.adapters.retroarch.glob.glob", side_effect=fake_glob):
            core = adapter._resolve_core()
        self.assertEqual(core, "/usr/lib/aarch64-linux-gnu/libretro/bsnes_mercury_performance_libretro.so")

    def test_auto_prefers_snes9x_when_multiple_available(self):
        ctx = self._make_ctx(retroarch_raw={"rom": "games/roms/hanjuku-hero.sfc", "core": "auto"})
        adapter = retroarch.RetroArchAdapter(ctx)

        def fake_glob(pattern):
            if "snes9x" in pattern:
                return ["/usr/lib/x86_64-linux-gnu/libretro/snes9x_libretro.so"]
            return ["/usr/lib/x86_64-linux-gnu/libretro/bsnes_mercury_performance_libretro.so"]

        with mock.patch("docich.adapters.retroarch.glob.glob", side_effect=fake_glob):
            core = adapter._resolve_core()
        self.assertEqual(core, "/usr/lib/x86_64-linux-gnu/libretro/snes9x_libretro.so")

    def test_auto_none_found_raises_adapter_error_mentioning_apt(self):
        ctx = self._make_ctx(retroarch_raw={"rom": "games/roms/hanjuku-hero.sfc", "core": "auto"})
        adapter = retroarch.RetroArchAdapter(ctx)
        with mock.patch("docich.adapters.retroarch.glob.glob", return_value=[]):
            with self.assertRaises(base.AdapterError) as ctx_err:
                adapter._resolve_core()
        self.assertIn("apt install", str(ctx_err.exception))

    def test_explicit_absolute_core_path_used_as_is(self):
        ctx = self._make_ctx(retroarch_raw={"rom": "games/roms/hanjuku-hero.sfc", "core": str(self.core_path)})
        adapter = retroarch.RetroArchAdapter(ctx)
        self.assertEqual(adapter._resolve_core(), str(self.core_path))

    def test_explicit_core_path_missing_raises(self):
        missing = self.repo_root / "does_not_exist_libretro.so"
        ctx = self._make_ctx(retroarch_raw={"rom": "games/roms/hanjuku-hero.sfc", "core": str(missing)})
        adapter = retroarch.RetroArchAdapter(ctx)
        with self.assertRaises(base.AdapterError):
            adapter._resolve_core()


class TestRomResolution(RetroArchTestBase):
    def test_missing_rom_key_raises(self):
        ctx = self._make_ctx(retroarch_raw={"core": str(self.core_path)})
        adapter = retroarch.RetroArchAdapter(ctx)
        with self.assertRaises(base.AdapterError):
            adapter.prepare()

    def test_rom_file_absent_raises_with_guidance_message(self):
        ctx = self._make_ctx(
            retroarch_raw={"rom": "games/roms/does-not-exist.sfc", "core": str(self.core_path)}
        )
        adapter = retroarch.RetroArchAdapter(ctx)
        with self.assertRaises(base.AdapterError) as ctx_err:
            adapter.prepare()
        msg = str(ctx_err.exception)
        self.assertIn("games/roms/README.md", msg)
        self.assertIn("does-not-exist.sfc", msg)

    def test_relative_rom_is_resolved_against_repo_root(self):
        ctx = self._make_ctx(retroarch_raw={"rom": "games/roms/hanjuku-hero.sfc", "core": str(self.core_path)})
        adapter = retroarch.RetroArchAdapter(ctx)
        self.assertEqual(adapter._resolve_rom(), self.rom_path)


class TestCommand(RetroArchTestBase):
    def test_command_starts_with_dbus_run_session(self):
        ctx = self._make_ctx(retroarch_raw={"rom": "games/roms/hanjuku-hero.sfc", "core": str(self.core_path)})
        adapter = retroarch.RetroArchAdapter(ctx)
        adapter.prepare()
        cmd = adapter.command()
        self.assertEqual(cmd[:3], ["dbus-run-session", "--", "retroarch"])
        self.assertIn("--config", cmd)
        self.assertIn(str(ctx.state.retroarch_dir / "retroarch.cfg"), cmd)
        self.assertIn("-L", cmd)
        self.assertIn(str(self.core_path), cmd)
        self.assertIn(str(self.rom_path), cmd)


class TestObserve(RetroArchTestBase):
    def test_observe_returns_screenshot_kind(self):
        xkit = FakeXKit()
        ctx = self._make_ctx(
            retroarch_raw={"rom": "games/roms/hanjuku-hero.sfc", "core": str(self.core_path)}, xkit=xkit
        )
        adapter = retroarch.RetroArchAdapter(ctx)
        obs = adapter.observe()
        self.assertEqual(obs.kind, "screenshot")
        self.assertIsNone(obs.text)
        self.assertEqual(obs.screenshot, str(ctx.state.screenshots_dir / "latest.png"))
        self.assertEqual(obs.adapter, "retroarch")


class TestAct(RetroArchTestBase):
    def test_pad_action_resolves_to_xdotool_keys_and_taps(self):
        xkit = FakeXKit(find_window_return="0x123")
        ctx = self._make_ctx(
            retroarch_raw={"rom": "games/roms/hanjuku-hero.sfc", "core": str(self.core_path)}, xkit=xkit
        )
        adapter = retroarch.RetroArchAdapter(ctx)
        adapter.act(Action(type="pad", buttons=["a", "start"], hold_ms=150))

        self.assertIn(("find_window", retroarch.WINDOW_PATTERN), xkit.calls)
        self.assertIn(("focus", "0x123"), xkit.calls)
        tap_calls = [c for c in xkit.calls if c[0] == "tap"]
        self.assertEqual(tap_calls, [("tap", ["x", "Return"], 150)])

    def test_pad_focus_is_cached_across_calls(self):
        xkit = FakeXKit(find_window_return="0x999")
        ctx = self._make_ctx(
            retroarch_raw={"rom": "games/roms/hanjuku-hero.sfc", "core": str(self.core_path)}, xkit=xkit
        )
        adapter = retroarch.RetroArchAdapter(ctx)
        adapter.act(Action(type="pad", buttons=["a"], hold_ms=100))
        adapter.act(Action(type="pad", buttons=["b"], hold_ms=100))

        find_calls = [c for c in xkit.calls if c[0] == "find_window"]
        self.assertEqual(len(find_calls), 1)  # 2 回目は instance キャッシュを使う
        focus_calls = [c for c in xkit.calls if c[0] == "focus"]
        self.assertEqual(len(focus_calls), 2)  # focus 自体は毎回呼ぶ

    def test_pad_missing_window_skips_focus_without_raising(self):
        xkit = FakeXKit(find_window_return=None)
        ctx = self._make_ctx(
            retroarch_raw={"rom": "games/roms/hanjuku-hero.sfc", "core": str(self.core_path)}, xkit=xkit
        )
        adapter = retroarch.RetroArchAdapter(ctx)
        adapter.act(Action(type="pad", buttons=["a"], hold_ms=100))  # should not raise
        self.assertFalse(any(c[0] == "focus" for c in xkit.calls))
        self.assertTrue(any(c[0] == "tap" for c in xkit.calls))

    def test_key_action_taps_raw_keys(self):
        xkit = FakeXKit(find_window_return="0x1")
        ctx = self._make_ctx(
            retroarch_raw={"rom": "games/roms/hanjuku-hero.sfc", "core": str(self.core_path)}, xkit=xkit
        )
        adapter = retroarch.RetroArchAdapter(ctx)
        adapter.act(Action(type="key", keys=["Up", "x"], hold_ms=80))
        tap_calls = [c for c in xkit.calls if c[0] == "tap"]
        self.assertEqual(tap_calls, [("tap", ["Up", "x"], 80)])

    def test_wait_action_is_noop(self):
        xkit = FakeXKit()
        ctx = self._make_ctx(
            retroarch_raw={"rom": "games/roms/hanjuku-hero.sfc", "core": str(self.core_path)}, xkit=xkit
        )
        adapter = retroarch.RetroArchAdapter(ctx)
        adapter.act(Action(type="wait", ms=100))
        self.assertEqual(xkit.calls, [])

    def test_unsupported_action_types_raise(self):
        ctx = self._make_ctx(retroarch_raw={"rom": "games/roms/hanjuku-hero.sfc", "core": str(self.core_path)})
        adapter = retroarch.RetroArchAdapter(ctx)
        for action in (
            Action(type="text", text="hi"),
            Action(type="special", key="Escape"),
            Action(type="mouse", x=1, y=2),
        ):
            with self.assertRaises(base.AdapterError):
                adapter.act(action)


if __name__ == "__main__":
    unittest.main()

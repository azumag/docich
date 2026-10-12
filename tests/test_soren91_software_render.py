"""Soren91's SDL opt-in must not alter transport, audio or other runtimes.

These tests cover command construction. The real presentation subprocess/env
contract is covered by test_presentation_software_render.py; neither suite
proves an improvement in production CPU usage or A/V quality.
"""
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.adapters.base import AdapterError  # noqa: E402
from docich.adapters.cli_game import CliCoordinatorAdapter  # noqa: E402
from docich.adapters.soren91 import Soren91CoordinatorAdapter  # noqa: E402
from docich.presentation import _parser  # noqa: E402

MISSING = object()
TEST_SRT_URL = "srt://127.0.0.1:19192?mode=listener"


class Soren91SoftwareRenderTests(unittest.TestCase):
    def adapter(self, value=MISSING, *, width=960, height=540, **options):
        raw = {"ffplay_bin": "custom-ffplay", "viewer_wait_sec": 240, **options}
        if value is not MISSING:
            raw["sdl_software_render"] = value
        game = SimpleNamespace(raw={"soren91": raw}, agent=SimpleNamespace(enabled=False))
        g = SimpleNamespace(display=SimpleNamespace(
            name=":99", viewport_x=0, viewport_y=90,
            viewport_width=width, viewport_height=height,
        ))
        spec = SimpleNamespace(runtime_id="g3-sdl-test")
        # Construction is isolated from tmux and filesystem setup. The real
        # Soren91 constructor and command methods still execute unchanged.
        with mock.patch.object(CliCoordinatorAdapter, "__init__", return_value=None):
            adapter = Soren91CoordinatorAdapter(g, game, spec)
        adapter.g, adapter.game, adapter.spec = g, game, spec
        return adapter

    def command(self, adapter):
        with mock.patch.object(adapter, "listener_srt_url", return_value=TEST_SRT_URL):
            return adapter._xterm_command()

    def test_default_and_explicit_false_keep_the_same_command(self):
        default = self.adapter()
        self.assertIs(default.sdl_software_render, False)
        command = self.command(default)
        self.assertEqual(command, self.command(self.adapter(False)))
        self.assertNotIn("--sdl-software-render", command)

    def test_true_adds_only_one_presenter_flag(self):
        baseline = self.command(self.adapter(False))
        candidate = self.command(self.adapter(True))
        flag = "--sdl-software-render"
        self.assertEqual(candidate.count(flag), 1)
        self.assertLess(candidate.index(flag), candidate.index("--"))
        self.assertEqual([arg for arg in candidate if arg != flag], baseline)
        self.assertEqual(candidate[candidate.index("--") + 1], "custom-ffplay")

    def test_real_parser_preserves_viewport_fps_audio_and_srt(self):
        command = self.command(self.adapter(True))
        args = _parser().parse_args(command[command.index("--display"):])
        self.assertIs(args.sdl_software_render, True)
        self.assertEqual((args.x, args.y, args.width, args.height), (0, 90, 960, 540))
        self.assertEqual((args.framerate, args.fit, args.align), (15, "contain", "center"))
        self.assertEqual(args.viewer_wait_sec, 240)
        self.assertEqual(args.audio_sink, "soren_null")
        self.assertEqual(args.command, [
            "--", "custom-ffplay", "-loglevel", "warning", "-nostats",
            "-fflags", "nobuffer", "-flags", "low_delay", "-i", TEST_SRT_URL,
        ])

    def test_rejects_non_boolean_settings_without_echoing_the_value(self):
        for value in (None, "false", "true", "", 0, 1, [], {}, "private-value-sentinel"):
            with self.subTest(value=type(value).__name__):
                with self.assertRaisesRegex(AdapterError, r"sdl_software_render") as caught:
                    self.adapter(value)
                self.assertNotIn("private-value-sentinel", str(caught.exception))

    def test_enabled_without_contained_viewport_is_rejected(self):
        for width, height in ((0, 540), (960, 0), (-1, 540), (960, -1)):
            with self.subTest(width=width, height=height):
                with self.assertRaisesRegex(AdapterError, "viewport"):
                    self.adapter(True, width=width, height=height)

    def test_disabled_direct_viewer_path_remains_unchanged(self):
        command = self.command(self.adapter(False, width=0, height=0))
        self.assertEqual(command[0], "custom-ffplay")
        self.assertEqual(command[-1], TEST_SRT_URL)
        self.assertNotIn("--sdl-software-render", command)
        self.assertNotIn("--", command)

    def test_audio_opt_out_is_preserved(self):
        command = self.command(self.adapter(True, audio_sink=""))
        self.assertNotIn("--audio-sink", command)
        self.assertNotIn("-an", command)
        self.assertIn("--sdl-software-render", command)

    def test_no_global_environment_or_remote_bot_change(self):
        before = dict(os.environ)
        baseline, candidate = self.adapter(False), self.adapter(True)
        for adapter in (baseline, candidate):
            with mock.patch.object(adapter, "remote_cdp_url", return_value="http://127.0.0.1:9322"):
                self.assertEqual(adapter._agent_window_env(), {
                    "SOREN91_SHARED_BROWSER": "1",
                    "SOREN91_REMOTE_CDP_URL": "http://127.0.0.1:9322",
                    "SOREN91_FULLSCREEN_WINDOW": "0",
                })
            self.command(adapter)
        self.assertEqual(dict(os.environ), before)


if __name__ == "__main__":
    unittest.main()

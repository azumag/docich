"""Offline checks only: no commercial content, emulator, display or VM access."""
from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.actions import parse_actions  # noqa: E402
from docich.adapters.base import AdapterError  # noqa: E402
from docich.adapters import retroarch  # noqa: E402
from docich.config import list_games, load_game, load_global  # noqa: E402
from docich.retro_corner import (  # noqa: E402
    RetroCornerError, RetroCornerManager, load_retro_corner_config,
)

EXAMPLES = ROOT / "config" / "examples" / "time-commando"


class TestTimeCommandoProfile(unittest.TestCase):
    def setUp(self):
        self.g = replace(load_global(ROOT), games_dir=EXAMPLES)
        self.game = load_game(self.g, "time-commando")

    def test_schema_and_disabled_defaults(self):
        self.assertEqual(self.game.adapter, "retroarch")
        self.assertIs(self.game.agent.enabled, False)
        self.assertEqual(self.game.agent.command, "")
        self.assertIs(self.game.lifecycle.require_round_boundary, True)
        self.assertEqual(self.game.raw["retro_corner"], {
            "enabled": False, "unattended": False,
        })
        self.assertEqual([g.name for g in list_games(self.g)], ["time-commando"])

    def test_example_is_not_automatically_discovered_or_scheduled(self):
        normal = load_global(ROOT)
        self.assertNotIn("time-commando", [g.name for g in list_games(normal)])
        self.assertNotIn("time-commando", normal.rotation.games)
        for filename in ("docich.toml", "docich.soren-live.toml"):
            global_config = load_global(ROOT, ROOT / "config" / filename)
            self.assertNotIn("time-commando", load_retro_corner_config(global_config).games)

    def test_paths_are_absolute_external_placeholders_and_fail_closed(self):
        raw = retroarch.retroarch_raw(self.game)
        for key, marker in (("rom", "REPLACE_WITH_USER_OWNED_DATA"),
                            ("core", "REPLACE_WITH_INSTALLED_CORE")):
            path = Path(raw[key])
            self.assertTrue(path.is_absolute())
            self.assertIn(marker, path.parts)
            self.assertFalse(path.is_relative_to(ROOT))
        self.assertNotEqual(raw["core"], "auto")
        with mock.patch.object(Path, "is_file", return_value=False):
            with self.assertRaises(AdapterError):
                retroarch.resolve_rom(self.g, self.game)
            with self.assertRaises(AdapterError):
                retroarch.resolve_core(self.game)

    def test_generated_settings_use_existing_adapter_without_audio(self):
        lines = retroarch.retroarch_cfg_lines(
            self.g, self.game, ROOT / "run" / "offline-only" / "retroarch.cfg", 55355,
        )
        self.assertIn('audio_enable = "false"', lines)
        self.assertFalse(any(line.startswith("audio_device =") for line in lines))
        self.assertIn('input_exit_emulator = "nul"', lines)
        self.assertIn('config_save_on_exit = "false"', lines)
        retroarch.resolved_buttons(self.game)

    def test_disabled_corner_rejects_registration_before_runtime_actions(self):
        coordinator = mock.Mock()
        ensure_runtime = mock.Mock()
        manager = RetroCornerManager(
            self.g, coordinator=coordinator, ensure_runtime=ensure_runtime,
            active_game_reader=lambda: None, chat=mock.Mock(), stream_game=mock.Mock(),
        )
        with self.assertRaisesRegex(RetroCornerError, "無効"):
            manager._validate_games([self.game.name])
        coordinator.start.assert_not_called()
        coordinator.switch.assert_not_called()
        ensure_runtime.assert_not_called()

    def test_documented_simultaneous_keys_parse_without_injection(self):
        for keys in (["Control_L", "Up"], ["Alt_L", "Left"], ["space"]):
            action = parse_actions({"type": "key", "keys": keys, "hold_ms": 100})[0]
            self.assertEqual(action.keys, keys)
            self.assertEqual(action.hold_ms, 100)

    def test_safe_profile_rejects_default_uncontained_global_before_content_checks(self):
        self.assertEqual(self.g.display.viewport_width, 0)
        self.assertEqual(self.g.display.viewport_height, 0)
        adapter = object.__new__(retroarch.RetroArchCoordinatorAdapter)
        adapter.g = self.g
        adapter.game = self.game
        adapter._check_active = mock.Mock()
        with mock.patch.object(retroarch, "resolve_rom") as rom, \
                mock.patch.object(retroarch, "resolve_core") as core, \
                mock.patch.object(retroarch.procs, "which") as which:
            with self.assertRaisesRegex(AdapterError, "private contained presentation"):
                adapter.preflight(1.0, None)
        rom.assert_not_called()
        core.assert_not_called()
        which.assert_not_called()


if __name__ == "__main__":
    unittest.main()

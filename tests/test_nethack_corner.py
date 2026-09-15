import json
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402
from docich.nethack_corner import (  # noqa: E402
    ANNOUNCE_TEXT,
    END_ANNOUNCE_TEXT,
    NethackCornerConfig,
    NethackCornerError,
    NethackCornerManager,
    load_nethack_corner_config,
)
from docich.nethack_corner_manual import ManualNethackCornerManager  # noqa: E402
from docich.retro_corner import RetroCornerError  # noqa: E402


def _succeeded():
    return SimpleNamespace(status="succeeded", error_code=None, detail=None)


class FakeCoordinator:
    def __init__(self, current):
        self.current = current
        self.calls = []

    def start(self, game):
        self.calls.append(("start", game))
        self.current[0] = game
        return _succeeded()

    def switch(self, game):
        self.calls.append(("switch", game))
        self.current[0] = game
        return _succeeded()

    def stop(self):
        self.calls.append(("stop", None))
        self.current[0] = None
        return _succeeded()


class NethackCornerTestBase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        (self.root / "config" / "games").mkdir(parents=True)
        (self.root / "config" / "docich.toml").write_text(
            """
[paths]
state_dir = "run"
games_dir = "config/games"

[nethack_corner]
enabled = true
start_hour = 22
duration_minutes = 30
timezone = "Asia/Tokyo"
""",
            encoding="utf-8",
        )
        (self.root / "config" / "games" / "nethack.toml").write_text(
            """
[game]
name = "nethack"
title = "NetHack"
adapter = "cli"

[cli]
command = "nethack"

[agent]
enabled = false
brain = "random"
interval_ms = 1500
""",
            encoding="utf-8",
        )
        self.g = config.load_global(self.root)
        self.cfg = load_nethack_corner_config(self.g)
        self.now_value = datetime(2026, 9, 16, 22, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        self.chats = []
        self.voices = []

    def tearDown(self):
        self.tempdir.cleanup()

    def manager(self, current, **kwargs):
        coordinator = kwargs.pop("coordinator", FakeCoordinator(current))
        defaults = {
            "config": self.cfg,
            "coordinator": coordinator,
            "now": lambda: self.now_value,
            "sleep": lambda seconds: None,
            "active_game_reader": lambda: current[0],
            "ensure_runtime": lambda: None,
            "chat": self.chats.append,
            "voice": self.voices.append,
        }
        defaults.update(kwargs)
        return NethackCornerManager(self.g, **defaults), coordinator


class TestNethackCornerConfig(NethackCornerTestBase):
    def test_default_profile_is_disabled(self):
        root = Path(__file__).resolve().parents[1]
        default_g = config.load_global(root, root / "config/docich.toml")
        default_cfg = load_nethack_corner_config(default_g)
        self.assertFalse(default_cfg.enabled)
        self.assertEqual(default_cfg.games, ("nethack",))

    def test_loads_config(self):
        self.assertEqual(
            self.cfg,
            NethackCornerConfig(
                enabled=True,
                start_hour=22,
                duration_minutes=30,
                timezone="Asia/Tokyo",
            ),
        )

    def test_invalid_values_are_rejected(self):
        path = self.root / "config" / "docich.toml"
        bad_bodies = (
            'enabled = "yes"\n',
            "start_hour = 24\n",
            "duration_minutes = 0\n",
            'timezone = "Mars/Olympus"\n',
            'weekdays = ["sat"]\n',
            "weekdays = [7]\n",
            "weekdays = [1, 1]\n",
            "weekdays = []\n",
            'games = ["robots"]\n',
        )
        for body in bad_bodies:
            with self.subTest(body=body):
                path.write_text("[nethack_corner]\n" + body, encoding="utf-8")
                g = config.load_global(self.root)
                with self.assertRaises(NethackCornerError):
                    load_nethack_corner_config(g)

    def test_weekdays_are_accepted(self):
        path = self.root / "config" / "docich.toml"
        path.write_text(
            "[nethack_corner]\nenabled = true\nweekdays = [1, 3, 5]\n",
            encoding="utf-8",
        )
        g = config.load_global(self.root)
        cfg = load_nethack_corner_config(g)
        self.assertEqual(cfg.weekdays, (1, 3, 5))

    def test_non_cli_nethack_definition_is_rejected(self):
        (self.root / "config" / "games" / "nethack.toml").write_text(
            '[game]\nname = "nethack"\ntitle = "NetHack"\nadapter = "browser"\n',
            encoding="utf-8",
        )
        current = [None]
        mgr, _ = self.manager(current)
        with self.assertRaises(RetroCornerError):
            mgr.start()


class TestNethackCornerLifecycle(NethackCornerTestBase):
    def test_start_switches_to_nethack_and_restores_previous_game(self):
        current = ["robots"]
        sleeps = []
        mgr, coordinator = self.manager(current, sleep=sleeps.append)
        result = mgr.start()
        self.assertEqual(result.status, "completed")
        self.assertEqual(
            coordinator.calls,
            [("switch", "nethack"), ("switch", "robots")],
        )
        self.assertEqual(current[0], "robots")
        self.assertEqual(sleeps, [30 * 60])

    def test_start_from_idle_returns_to_idle(self):
        current = [None]
        mgr, coordinator = self.manager(current)
        self.assertEqual(mgr.start().status, "completed")
        self.assertEqual(
            coordinator.calls,
            [("start", "nethack"), ("stop", None)],
        )
        self.assertIsNone(current[0])

    def test_start_and_end_announcements_are_best_effort(self):
        current = [None]
        mgr, _ = self.manager(current)
        self.assertEqual(mgr.start().status, "completed")
        self.assertEqual(self.chats, [ANNOUNCE_TEXT, END_ANNOUNCE_TEXT])
        self.assertEqual(self.voices, [ANNOUNCE_TEXT, END_ANNOUNCE_TEXT])
        state = mgr.status()
        self.assertTrue(state.get("announced"))
        self.assertTrue(state.get("end_announced"))

    def test_disabled_tick_is_noop(self):
        self.cfg = replace(self.cfg, enabled=False)
        current = ["robots"]
        mgr, coordinator = self.manager(current)
        result = mgr.tick()
        self.assertEqual(result.status, "noop")
        self.assertEqual(result.detail, "disabled")
        self.assertEqual(coordinator.calls, [])

    def test_scheduled_tick_obeys_hour_and_weekday(self):
        current = [None]
        sleeps = []
        mgr, coordinator = self.manager(current, sleep=sleeps.append)
        self.assertEqual(mgr._scheduled_tick(self.now_value).status, "completed")
        self.assertEqual(
            coordinator.calls,
            [("start", "nethack"), ("stop", None)],
        )
        self.assertEqual(sleeps, [30 * 60])

        self.now_value = self.now_value.replace(hour=21)
        self.assertEqual(mgr._scheduled_tick(self.now_value).detail, "outside-window")

    def test_state_is_isolated_from_other_corners(self):
        current = [None]
        mgr, _ = self.manager(current)
        mgr.start()
        state_file = self.g.state_dir / "nethack_corner.json"
        self.assertTrue(state_file.is_file())
        self.assertFalse((self.g.state_dir / "retro_corner.json").exists())
        self.assertFalse((self.g.state_dir / "soren91_corner.json").exists())
        self.assertFalse((self.g.state_dir / "nethack_corner_manual.json").exists())
        self.assertEqual(state_file.stat().st_mode & 0o777, 0o600)
        data = json.loads(state_file.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "completed")


class TestManualNethackCorner(NethackCornerTestBase):
    def test_manual_runner_uses_separate_state_and_duration(self):
        current = [None]
        coordinator = FakeCoordinator(current)
        sleeps = []
        mgr = ManualNethackCornerManager(
            self.g,
            duration_minutes=7,
            coordinator=coordinator,
            now=lambda: self.now_value,
            sleep=sleeps.append,
            active_game_reader=lambda: current[0],
            ensure_runtime=lambda: None,
            chat=self.chats.append,
            voice=self.voices.append,
        )
        self.assertEqual(mgr.start().status, "completed")
        self.assertEqual(sleeps, [7 * 60])
        self.assertTrue((self.g.state_dir / "nethack_corner_manual.json").is_file())
        self.assertFalse((self.g.state_dir / "nethack_corner.json").exists())
        self.assertNotEqual(
            mgr.lock_path,
            self.g.state_dir / "locks/nethack-corner.lock",
        )

    def test_manual_duration_is_bounded(self):
        with self.assertRaises(NethackCornerError):
            ManualNethackCornerManager(self.g, duration_minutes=0)
        with self.assertRaises(NethackCornerError):
            ManualNethackCornerManager(self.g, duration_minutes=121)


if __name__ == "__main__":
    unittest.main()

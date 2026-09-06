import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from docich import config
from docich.retro_corner import RetroCornerConfig, RetroCornerManager


class FakeCoordinator:
    def __init__(self, current):
        self.current = current
        self.calls = []

    def switch(self, game):
        self.calls.append(("switch", game))
        self.current[0] = game
        return SimpleNamespace(status="succeeded", error_code=None, detail=None)


class ManualRetroCornerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        games = self.root / "config" / "games"
        games.mkdir(parents=True)
        (self.root / "config" / "docich.toml").write_text(
            '[paths]\nstate_dir="run"\ngames_dir="config/games"\n', encoding="utf-8"
        )
        (games / "ninvaders.toml").write_text(
            '[game]\nname="ninvaders"\ntitle="Space Invaders"\nadapter="cli"\n'
            '[cli]\ncommand="/usr/local/bin/ninvaders_docich"\n'
            '[agent]\nenabled=false\n'
            '[retro_corner]\nunattended=true\n',
            encoding="utf-8",
        )
        (games / "sorengame.toml").write_text(
            '[game]\nname="sorengame"\ntitle="Soren"\nadapter="browser"\n', encoding="utf-8"
        )
        self.g = config.load_global(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_manual_start_can_override_game_and_duration_for_unattended_cli(self):
        current = ["sorengame"]
        coordinator = FakeCoordinator(current)
        slept = []
        now = datetime(2026, 9, 6, 17, 32, tzinfo=ZoneInfo("Asia/Tokyo"))
        mgr = RetroCornerManager(
            self.g,
            config=RetroCornerConfig(enabled=True, games=["robots"]),
            coordinator=coordinator,
            now=lambda: now,
            sleep=lambda seconds: slept.append(seconds),
            active_game_reader=lambda: current[0],
            ensure_runtime=lambda: None,
        )

        result = mgr.start(game="ninvaders", duration_minutes=5)

        self.assertEqual(result.status, "completed")
        self.assertEqual(coordinator.calls, [("switch", "ninvaders"), ("switch", "sorengame")])
        self.assertEqual(slept, [300.0])
        self.assertEqual(current[0], "sorengame")

    def test_scheduled_config_stays_robots_after_manual_override(self):
        cfg = RetroCornerConfig(enabled=True, games=["robots"])
        self.assertEqual(cfg.games, ["robots"])


if __name__ == "__main__":
    unittest.main()

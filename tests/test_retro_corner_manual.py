import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from docich import config
from docich.retro_corner import RetroCornerConfig
from docich.retro_corner_manual import ManualRetroCornerManager
from docich.retro_corner import RetroCornerError  # noqa: E402


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

    def test_manual_runner_can_use_unattended_cli_and_custom_duration(self):
        current = ["sorengame"]
        coordinator = FakeCoordinator(current)
        slept = []
        now = datetime(2026, 9, 6, 17, 32, tzinfo=ZoneInfo("Asia/Tokyo"))
        mgr = ManualRetroCornerManager(
            self.g,
            game="ninvaders",
            duration_minutes=5,
            coordinator=coordinator,
            now=lambda: now,
            sleep=lambda seconds: slept.append(seconds),
            active_game_reader=lambda: current[0],
            ensure_runtime=lambda: None,
        )

        result = mgr.start()

        self.assertEqual(result.status, "completed")
        self.assertEqual(coordinator.calls, [("switch", "ninvaders"), ("switch", "sorengame")])
        self.assertEqual(slept, [300.0])
        self.assertEqual(current[0], "sorengame")
        self.assertFalse((self.g.state_dir / "retro_corner.json").exists())
        self.assertTrue((self.g.state_dir / "retro_corner_manual.json").exists())

    def test_manual_runner_does_not_mutate_daily_schedule_config(self):
        daily = RetroCornerConfig(enabled=True, games=["robots"])
        self.assertEqual(daily.games, ["robots"])


class ManualRotationTargetContractTests(ManualRetroCornerTests):
    """A rotation dispatch hands the selected target to every manager (#998).

    This manual manager used to declare ``_validate_games(self)`` while the
    shared ``_begin_locked`` passes ``[target]``, so the exact #986 TypeError
    was one rotation mode away. The contract is now shared: an owned target
    is accepted, an empty or unowned one is rejected instead of silently
    validating the whole config.
    """

    def _manager(self):
        return ManualRetroCornerManager(
            self.g,
            game="ninvaders",
            duration_minutes=5,
            coordinator=FakeCoordinator(["sorengame"]),
            now=lambda: datetime(2026, 9, 6, 17, 32, tzinfo=ZoneInfo("Asia/Tokyo")),
            sleep=lambda seconds: None,
            active_game_reader=lambda: ["sorengame"][0],
            ensure_runtime=lambda: None,
        )

    def test_accepts_the_owned_rotation_target(self):
        self._manager()._validate_games(["ninvaders"])

    def test_rejects_empty_unowned_and_superset_targets(self):
        mgr = self._manager()
        for names in ([], ["nsnake"], ["ninvaders", "nsnake"]):
            with self.subTest(names=names):
                with self.assertRaises(RetroCornerError):
                    mgr._validate_games(names)

    def test_default_call_still_validates_the_configured_games(self):
        self._manager()._validate_games()


if __name__ == "__main__":
    unittest.main()

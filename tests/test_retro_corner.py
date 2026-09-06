import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, date
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402
from docich.retro_corner import (  # noqa: E402
    RetroCornerConfig,
    RetroCornerError,
    RetroCornerManager,
    load_retro_corner_config,
    select_game,
)


class FakeCoordinator:
    def __init__(self, current):
        self.current = current
        self.calls = []

    def start(self, game):
        self.calls.append(("start", game))
        self.current[0] = game
        return SimpleNamespace(status="succeeded", error_code=None, detail=None)

    def switch(self, game):
        self.calls.append(("switch", game))
        self.current[0] = game
        return SimpleNamespace(status="succeeded", error_code=None, detail=None)

    def stop(self):
        self.calls.append(("stop", None))
        self.current[0] = None
        return SimpleNamespace(status="succeeded", error_code=None, detail=None)


class RetroCornerTestBase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        (self.root / "config" / "games").mkdir(parents=True)
        (self.root / "config" / "docich.toml").write_text(
            """
[paths]
state_dir = "run"
games_dir = "config/games"

[retro_corner]
enabled = true
start_hour = 20
duration_minutes = 60
timezone = "Asia/Tokyo"
games = ["robots"]
""",
            encoding="utf-8",
        )
        (self.root / "config" / "games" / "robots.toml").write_text(
            """
[game]
name = "robots"
title = "Robots"
adapter = "cli"
[agent]
enabled = true
brain = "resolver"
""",
            encoding="utf-8",
        )
        for name in ("sorengame", "nethack"):
            (self.root / "config" / "games" / f"{name}.toml").write_text(
                f"[game]\nname = \"{name}\"\ntitle = \"{name}\"\nadapter = \"browser\"\n",
                encoding="utf-8",
            )
        self.g = config.load_global(self.root)
        self.cfg = load_retro_corner_config(self.g)
        self.now_value = datetime(2026, 9, 6, 20, 0, tzinfo=ZoneInfo("Asia/Tokyo"))

    def tearDown(self):
        self.tempdir.cleanup()

    def manager(self, current, sleep=None):
        coordinator = FakeCoordinator(current)
        mgr = RetroCornerManager(
            self.g,
            config=self.cfg,
            coordinator=coordinator,
            now=lambda: self.now_value,
            sleep=sleep or (lambda seconds: None),
            active_game_reader=lambda: current[0],
        )
        return mgr, coordinator


class TestRetroCornerConfig(RetroCornerTestBase):
    def test_loads_production_shape(self):
        self.assertEqual(
            self.cfg,
            RetroCornerConfig(
                enabled=True,
                start_hour=20,
                duration_minutes=60,
                timezone="Asia/Tokyo",
                games=["robots"],
            ),
        )

    def test_invalid_timezone_is_rejected(self):
        path = self.root / "config" / "docich.toml"
        path.write_text('[retro_corner]\ntimezone = "Mars/Olympus"\ngames = ["robots"]\n', encoding="utf-8")
        self.g = config.load_global(self.root)
        with self.assertRaises(RetroCornerError):
            load_retro_corner_config(self.g)

    def test_invalid_hour_duration_and_empty_games_are_rejected(self):
        bad_values = (
            "start_hour = 24\nduration_minutes = 60\ngames = [\"robots\"]\n",
            "start_hour = 20\nduration_minutes = 0\ngames = [\"robots\"]\n",
            "start_hour = 20\nduration_minutes = 60\ngames = []\n",
        )
        path = self.root / "config" / "docich.toml"
        for body in bad_values:
            with self.subTest(body=body):
                path.write_text("[retro_corner]\n" + body, encoding="utf-8")
                self.g = config.load_global(self.root)
                with self.assertRaises(RetroCornerError):
                    load_retro_corner_config(self.g)

    def test_target_must_be_cli_with_enabled_agent(self):
        path = self.root / "config" / "docich.toml"
        path.write_text('[retro_corner]\nenabled = true\ngames = ["nethack"]\n', encoding="utf-8")
        self.g = config.load_global(self.root)
        cfg = load_retro_corner_config(self.g)
        mgr = RetroCornerManager(
            self.g,
            config=cfg,
            coordinator=FakeCoordinator(["sorengame"]),
            now=lambda: self.now_value,
            sleep=lambda seconds: None,
            active_game_reader=lambda: "sorengame",
        )
        with self.assertRaises(RetroCornerError):
            mgr.start()


class TestRetroCornerSelection(unittest.TestCase):
    def test_selection_is_deterministic_per_local_date(self):
        games = ["robots", "ninvaders", "nsnake"]
        day = date(2026, 9, 6)
        expected = games[day.toordinal() % len(games)]
        self.assertEqual(select_game(games, day), expected)
        self.assertEqual(select_game(games, day), expected)


class TestRetroCornerLifecycle(RetroCornerTestBase):
    def test_restores_previous_game_after_duration(self):
        current = ["sorengame"]
        mgr, coordinator = self.manager(current)
        result = mgr.start()
        self.assertEqual(result.status, "completed")
        self.assertEqual(coordinator.calls, [("switch", "robots"), ("switch", "sorengame")])
        self.assertEqual(current[0], "sorengame")

    def test_idle_before_corner_returns_to_idle(self):
        current = [None]
        mgr, coordinator = self.manager(current)
        result = mgr.start()
        self.assertEqual(result.status, "completed")
        self.assertEqual(coordinator.calls, [("start", "robots"), ("stop", None)])
        self.assertIsNone(current[0])

    def test_manual_operator_switch_is_not_overwritten(self):
        current = ["sorengame"]

        def manual_switch(_seconds):
            current[0] = "nethack"

        mgr, coordinator = self.manager(current, sleep=manual_switch)
        result = mgr.start()
        self.assertEqual(result.status, "interrupted")
        self.assertEqual(coordinator.calls, [("switch", "robots")])
        self.assertEqual(current[0], "nethack")

    def test_manual_stop_can_end_corner_during_wait(self):
        current = ["sorengame"]
        holder = {}

        def early_stop(_seconds):
            holder["stop"] = mgr.stop()

        mgr, coordinator = self.manager(current, sleep=early_stop)
        result = mgr.start()
        self.assertEqual(holder["stop"].status, "completed")
        self.assertEqual(result.status, "completed")
        self.assertEqual(coordinator.calls, [("switch", "robots"), ("switch", "sorengame")])

    def test_tick_outside_start_hour_is_noop(self):
        self.now_value = datetime(2026, 9, 6, 19, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        current = ["sorengame"]
        mgr, coordinator = self.manager(current)
        result = mgr.tick()
        self.assertEqual(result.status, "noop")
        self.assertEqual(coordinator.calls, [])

    def test_tick_runs_only_once_per_local_date(self):
        current = ["sorengame"]
        mgr, coordinator = self.manager(current)
        first = mgr.tick()
        second = mgr.tick()
        self.assertEqual(first.status, "completed")
        self.assertEqual(second.status, "noop")
        self.assertEqual(
            coordinator.calls,
            [("switch", "robots"), ("switch", "sorengame")],
        )

    def test_expired_active_state_is_reconciled_on_next_tick(self):
        current = ["robots"]
        mgr, coordinator = self.manager(current)
        mgr._write_state(
            {
                "schema_version": 1,
                "status": "active",
                "date": "2026-09-05",
                "game": "robots",
                "previous_game": "sorengame",
                "started_at": "2026-09-05T20:00:00+09:00",
                "ends_at": "2026-09-05T21:00:00+09:00",
                "completed_at": None,
                "last_error": None,
            }
        )
        self.now_value = datetime(2026, 9, 6, 19, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        result = mgr.tick()
        self.assertEqual(result.status, "noop")
        self.assertEqual(current[0], "sorengame")
        self.assertEqual(coordinator.calls, [("switch", "sorengame")])
        self.assertEqual(mgr.status()["status"], "completed")

    def test_status_state_is_private_json(self):
        current = ["sorengame"]
        mgr, _ = self.manager(current)
        mgr.start()
        state_file = self.g.state_dir / "retro_corner.json"
        data = json.loads(state_file.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["date"], "2026-09-06")
        self.assertEqual(state_file.stat().st_mode & 0o777, 0o600)


class TestUserFacingCommand(unittest.TestCase):
    def test_docich_routes_retro_corner_status(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg = tmp_path / "docich.toml"
            cfg.write_text(
                f'[paths]\nstate_dir = "{tmp_path / "run"}"\n[retro_corner]\ngames = ["robots"]\n',
                encoding="utf-8",
            )
            result = subprocess.run(
                [str(repo / "bin/docich"), "--config", str(cfg), "retro-corner", "status", "--json"],
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "idle")


class TestSystemdTemplates(unittest.TestCase):
    def test_service_and_timer_contract(self):
        root = Path(__file__).resolve().parents[1]
        service = (root / "scripts/systemd/docich-retro-corner.service").read_text(encoding="utf-8")
        timer = (root / "scripts/systemd/docich-retro-corner.timer").read_text(encoding="utf-8")
        self.assertIn("ExecStart=__DOCICH_ROOT__/bin/docich retro-corner tick", service)
        self.assertIn("TimeoutStartSec=15h", service)
        self.assertIn("OnCalendar=hourly", timer)
        self.assertIn("Persistent=false", timer)


if __name__ == "__main__":
    unittest.main()

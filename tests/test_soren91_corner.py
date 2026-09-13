import fcntl
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402
from docich.retro_corner import RetroCornerError  # noqa: E402
from docich.soren91_corner import (  # noqa: E402
    ANNOUNCE_TEXT,
    DELIVERY_SOURCE,
    GAME_NAME,
    Soren91CornerConfig,
    Soren91CornerError,
    Soren91CornerManager,
    load_soren91_corner_config,
)


def _recover_ok():
    return SimpleNamespace(
        status="succeeded",
        operation="recover",
        error_code=None,
        detail="recovery は不要でした",
        cleanup_pending=False,
    )


def _recover_failed():
    return SimpleNamespace(
        status="failed",
        operation="recover",
        error_code="ERROR_RECOVERY_REQUIRED",
        detail="canonical stateが壊れているため自動復旧できません",
        cleanup_pending=False,
    )


def _succeeded():
    return SimpleNamespace(status="succeeded", error_code=None, detail=None)


class FakeCoordinator:
    def __init__(self, current, *, fail_first_start=False):
        self.current = current
        self.calls = []
        self.recover_calls = 0
        self.fail_first_start = fail_first_start
        self.start_attempts = 0

    def start(self, game):
        self.start_attempts += 1
        if self.fail_first_start and self.start_attempts == 1:
            return SimpleNamespace(status="failed", error_code="E_START", detail="boom")
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

    def recover(self):
        self.recover_calls += 1
        return _recover_ok()


class Soren91CornerTestBase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        (self.root / "config" / "games").mkdir(parents=True)
        (self.root / "config" / "docich.toml").write_text(
            """
[paths]
state_dir = "run"
games_dir = "config/games"

[soren91_corner]
enabled = true
start_hour = 21
duration_minutes = 30
timezone = "Asia/Tokyo"
""",
            encoding="utf-8",
        )
        (self.root / "config" / "games" / "soren91.toml").write_text(
            """
[game]
name = "soren91"
title = "Soren91"
adapter = "soren91"
[agent]
enabled = false
""",
            encoding="utf-8",
        )
        self.g = config.load_global(self.root)
        self.cfg = load_soren91_corner_config(self.g)
        self.now_value = datetime(2026, 9, 6, 21, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        self.chats = []

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
        }
        defaults.update(kwargs)
        mgr = Soren91CornerManager(self.g, **defaults)
        return mgr, coordinator


class TestSoren91CornerConfig(Soren91CornerTestBase):
    def test_defaults_are_off(self):
        root = Path(__file__).resolve().parents[1]
        default_g = config.load_global(root, root / "config/docich.toml")
        live_g = config.load_global(root, root / "config/docich.soren-live.toml")
        default_cfg = load_soren91_corner_config(default_g)
        live_cfg = load_soren91_corner_config(live_g)
        self.assertFalse(default_cfg.enabled)
        self.assertFalse(live_cfg.enabled)
        self.assertEqual(live_cfg.start_hour, 21)
        self.assertEqual(live_cfg.duration_minutes, 30)
        self.assertEqual(live_cfg.timezone, "Asia/Tokyo")
        self.assertEqual(live_cfg.games, ("soren91",))

    def test_loads_test_shape(self):
        self.assertEqual(
            self.cfg,
            Soren91CornerConfig(
                enabled=True,
                start_hour=21,
                duration_minutes=30,
                timezone="Asia/Tokyo",
            ),
        )

    def test_invalid_values_are_rejected(self):
        path = self.root / "config" / "docich.toml"
        bad_bodies = (
            "enabled = \"yes\"\n",
            "start_hour = 24\nduration_minutes = 30\n",
            "start_hour = 21\nduration_minutes = 0\n",
            "timezone = \"Mars/Olympus\"\n",
            "weekdays = [\"sat\"]\n",
            "weekdays = [7]\n",
            "weekdays = [1, 1]\n",
            "weekdays = []\n",
            "games = [\"ninvaders\"]\n",
            "improve_matches = 11\n",
        )
        for body in bad_bodies:
            with self.subTest(body=body):
                path.write_text("[soren91_corner]\n" + body, encoding="utf-8")
                self.g = config.load_global(self.root)
                with self.assertRaises(Soren91CornerError):
                    load_soren91_corner_config(self.g)

    def test_weekdays_are_accepted(self):
        path = self.root / "config" / "docich.toml"
        path.write_text("[soren91_corner]\nenabled = true\nweekdays = [5, 6]\n", encoding="utf-8")
        self.g = config.load_global(self.root)
        cfg = load_soren91_corner_config(self.g)
        self.assertEqual(cfg.weekdays, (5, 6))

    def test_games_override_is_rejected(self):
        path = self.root / "config" / "docich.toml"
        path.write_text(
            "[soren91_corner]\nenabled = true\ngames = [\"soren91\", \"robots\"]\n",
            encoding="utf-8",
        )
        self.g = config.load_global(self.root)
        with self.assertRaises(Soren91CornerError):
            load_soren91_corner_config(self.g)

    def test_non_soren91_game_definition_is_rejected(self):
        (self.root / "config" / "games" / "soren91.toml").write_text(
            '[game]\nname = "soren91"\ntitle = "Soren91"\nadapter = "cli"\n',
            encoding="utf-8",
        )
        current = [None]
        mgr, _ = self.manager(current)
        with self.assertRaises(RetroCornerError):
            mgr.start()


class TestSoren91CornerLifecycle(Soren91CornerTestBase):
    def test_tick_runs_full_overlay_cycle_and_restores(self):
        current = ["sorengame"]
        sleeps = []
        mgr, coordinator = self.manager(current, sleep=sleeps.append)
        result = mgr.tick()
        self.assertEqual(result.status, "completed")
        self.assertEqual(
            coordinator.calls, [("switch", "soren91"), ("switch", "sorengame")]
        )
        self.assertEqual(current[0], "sorengame")
        self.assertEqual(sleeps, [30 * 60])

    def test_tick_from_idle_stops_back_to_idle(self):
        current = [None]
        mgr, coordinator = self.manager(current)
        result = mgr.tick()
        self.assertEqual(result.status, "completed")
        self.assertEqual(coordinator.calls, [("start", "soren91"), ("stop", None)])
        self.assertIsNone(current[0])

    def test_tick_outside_start_hour_is_noop(self):
        self.now_value = datetime(2026, 9, 6, 20, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        current = ["sorengame"]
        mgr, coordinator = self.manager(current)
        result = mgr.tick()
        self.assertEqual(result.status, "noop")
        self.assertEqual(result.detail, "outside-window")
        self.assertEqual(coordinator.calls, [])

    def test_disabled_tick_is_noop(self):
        from dataclasses import replace

        self.cfg = replace(self.cfg, enabled=False)
        current = ["sorengame"]
        mgr, coordinator = self.manager(current)
        result = mgr.tick()
        self.assertEqual(result.status, "noop")
        self.assertEqual(result.detail, "disabled")
        self.assertEqual(coordinator.calls, [])

    def test_weekday_gate_skips_non_listed_day(self):
        from dataclasses import replace

        today = self.now_value.weekday()
        other = (today + 1) % 7
        self.cfg = replace(self.cfg, weekdays=(other,))
        current = ["sorengame"]
        mgr, coordinator = self.manager(current)
        result = mgr.tick()
        self.assertEqual(result.status, "noop")
        self.assertEqual(result.detail, "outside-window")
        self.assertEqual(coordinator.calls, [])

    def test_weekday_gate_runs_on_listed_day(self):
        from dataclasses import replace

        self.cfg = replace(self.cfg, weekdays=(self.now_value.weekday(),))
        current = [None]
        mgr, coordinator = self.manager(current)
        self.assertEqual(mgr.tick().status, "completed")
        self.assertEqual(coordinator.calls, [("start", "soren91"), ("stop", None)])

    def test_tick_runs_only_once_per_local_date(self):
        current = ["sorengame"]
        mgr, coordinator = self.manager(current)
        first = mgr.tick()
        second = mgr.tick()
        self.assertEqual(first.status, "completed")
        self.assertEqual(second.status, "noop")
        self.assertEqual(
            coordinator.calls, [("switch", "soren91"), ("switch", "sorengame")]
        )

    def test_transition_delay_does_not_shorten_corner(self):
        from datetime import timedelta

        current = [None]
        sleeps = []
        mgr, coordinator = self.manager(current, sleep=sleeps.append)
        original = coordinator.start

        def delayed(game):
            self.now_value += timedelta(minutes=25)
            return original(game)

        coordinator.start = delayed
        mgr.tick()
        self.assertEqual(sleeps, [30 * 60])

    def test_expired_active_state_is_reconciled_on_next_tick(self):
        current = ["soren91"]
        mgr, coordinator = self.manager(current)
        mgr._write_state(
            {
                "schema_version": 1,
                "status": "active",
                "date": "2026-09-05",
                "game": "soren91",
                "previous_game": "sorengame",
                "started_at": "2026-09-05T21:00:00+09:00",
                "ends_at": "2026-09-05T21:30:00+09:00",
                "completed_at": None,
                "last_error": None,
            }
        )
        self.now_value = datetime(2026, 9, 6, 20, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        result = mgr.tick()
        self.assertEqual(result.status, "noop")
        self.assertEqual(current[0], "sorengame")
        self.assertEqual(coordinator.calls, [("switch", "sorengame")])
        self.assertEqual(mgr.status()["status"], "completed")

    def test_state_files_are_isolated_from_other_corners(self):
        current = [None]
        mgr, _ = self.manager(current)
        mgr.tick()
        self.assertTrue((self.g.state_dir / "soren91_corner.json").is_file())
        self.assertFalse((self.g.state_dir / "retro_corner.json").exists())
        self.assertFalse((self.g.state_dir / "soren91_corner_manual.json").exists())
        self.assertNotEqual(
            mgr.lock_path, self.g.state_dir / "locks/retro-corner.lock"
        )

    def test_status_state_is_private_json(self):
        current = [None]
        mgr, _ = self.manager(current)
        mgr.tick()
        state_file = self.g.state_dir / "soren91_corner.json"
        data = json.loads(state_file.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["date"], "2026-09-06")
        self.assertEqual(state_file.stat().st_mode & 0o777, 0o600)


class TestSoren91CornerAnnounce(Soren91CornerTestBase):
    def test_announce_is_viewer_facing_fixed_text(self):
        current = [None]
        mgr, _ = self.manager(current)
        self.assertEqual(mgr.start().status, "completed")
        self.assertEqual(self.chats, [ANNOUNCE_TEXT])
        self.assertEqual(ANNOUNCE_TEXT, "ソ連ゲーム91、メリケンAIのコーナーです。")
        for banned in ("Mac", "レンダラー", "renderer", "CDP", "SRT", "bot"):
            self.assertNotIn(banned, self.chats[0])
        self.assertTrue(mgr.status().get("announced"))

    def test_announce_failure_does_not_fail_corner(self):
        def boom(text):
            raise RuntimeError("sink down")

        current = [None]
        mgr, _ = self.manager(current, chat=boom)
        self.assertEqual(mgr.start().status, "completed")
        state = mgr.status()
        self.assertNotIn("announced", state)
        self.assertIn("announce_error", state)

    def test_second_announce_is_skipped(self):
        current = [None]
        mgr, _ = self.manager(current)
        mgr.start()
        with mgr._locked():
            state = mgr._read_state()
            mgr._announce_start_locked(state)
        self.assertEqual(self.chats, [ANNOUNCE_TEXT])

    def test_default_chat_uses_soren91_delivery_source(self):
        import docich.soren91_corner as corner_module

        seen = []
        real = corner_module.enqueue_chat
        corner_module.enqueue_chat = lambda g, text, *, source="docich": seen.append(
            (text, source)
        )
        try:
            current = [None]
            coordinator = FakeCoordinator(current)
            mgr = Soren91CornerManager(
                self.g,
                config=self.cfg,
                coordinator=coordinator,
                now=lambda: self.now_value,
                sleep=lambda seconds: None,
                active_game_reader=lambda: current[0],
                ensure_runtime=lambda: None,
            )
            mgr._chat("hello")
        finally:
            corner_module.enqueue_chat = real
        self.assertEqual(seen, [("hello", DELIVERY_SOURCE)])
        self.assertEqual(DELIVERY_SOURCE, "soren91-corner")


class TestSoren91CornerRecover(Soren91CornerTestBase):
    def test_failed_start_recovers_and_retries_once(self):
        current = [None]
        coordinator = FakeCoordinator(current, fail_first_start=True)
        mgr, _ = self.manager(current, coordinator=coordinator)
        result = mgr.start()
        self.assertEqual(result.status, "completed")
        self.assertEqual(coordinator.recover_calls, 1)
        self.assertEqual(coordinator.calls, [("start", "soren91"), ("stop", None)])

    def test_unrecoverable_start_marks_failed(self):
        class AlwaysFail(FakeCoordinator):
            def start(self, game):
                self.calls.append(("start-failed", game))
                return SimpleNamespace(
                    status="failed", error_code="E_START", detail="boom"
                )

            def recover(self):
                self.recover_calls += 1
                return _recover_failed()

        current = [None]
        coordinator = AlwaysFail(current)
        mgr, _ = self.manager(current, coordinator=coordinator)
        with self.assertRaises(RetroCornerError):
            mgr.start()
        state = mgr.status()
        self.assertEqual(state["status"], "failed")
        self.assertIn("boom", state["last_error"])

    def test_no_recover_support_raises_original_error(self):
        class NoRecover:
            def __init__(self, current):
                self.current = current
                self.calls = []

            def start(self, game):
                self.calls.append(("start-failed", game))
                return SimpleNamespace(
                    status="failed", error_code="E_START", detail="boom"
                )

            def switch(self, game):
                self.calls.append(("switch", game))
                self.current[0] = game
                return _succeeded()

            def stop(self):
                self.calls.append(("stop", None))
                self.current[0] = None
                return _succeeded()

        current = [None]
        mgr, _ = self.manager(current, coordinator=NoRecover(current))
        with self.assertRaises(RetroCornerError):
            mgr.start()
        self.assertEqual(mgr.status()["status"], "failed")


class TestSoren91CornerImprove(Soren91CornerTestBase):
    def test_improve_is_recorded_as_unsupported(self):
        from dataclasses import replace

        self.cfg = replace(self.cfg, improve_agents="agent-a")
        current = [None]
        mgr, _ = self.manager(current)
        self.assertEqual(mgr.start().status, "completed")
        self.assertEqual(
            mgr.status().get("improve_job"),
            {"spawned": False, "reason": "soren91-improve-not-supported"},
        )

    def test_no_improve_job_without_agents(self):
        current = [None]
        mgr, _ = self.manager(current)
        self.assertEqual(mgr.start().status, "completed")
        self.assertNotIn("improve_job", mgr.status())


class TestSoren91CornerGuards(Soren91CornerTestBase):
    def test_duplicate_tick_is_noop(self):
        current = ["sorengame"]
        mgr, coordinator = self.manager(current)
        mgr.tick_guard_path.parent.mkdir(parents=True, exist_ok=True)
        held = mgr.tick_guard_path.open("a+")
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            result = mgr.tick()
        finally:
            fcntl.flock(held.fileno(), fcntl.LOCK_UN)
            held.close()
        self.assertEqual(result.status, "noop")
        self.assertEqual(result.detail, "already-running")
        self.assertEqual(coordinator.calls, [])

    def test_tick_takes_program_slot(self):
        from docich.trading.soren_output import resolve_soren_root

        current = [None]
        mgr, _ = self.manager(current)
        self.assertEqual(mgr.tick().status, "completed")
        queue_entry = (
            resolve_soren_root(self.g) / "tmp/state/docich_program_queue/soren91_corner.json"
        )
        self.assertTrue(queue_entry.is_file())
        self.assertEqual(json.loads(queue_entry.read_text(encoding="utf-8"))["status"], "done")

    def test_expired_when_program_busy_past_deadline(self):
        from docich.trading.soren_output import resolve_soren_root

        root = resolve_soren_root(self.g) / "tmp" / "state"
        root.mkdir(parents=True, exist_ok=True)
        other = self.root / "other_corner.json"
        other.write_text(json.dumps({"status": "active"}), encoding="utf-8")
        (root / "docich_program_active.json").write_text(
            json.dumps({"owner_state": str(other)}), encoding="utf-8"
        )
        held = (root / "docich_program.lock").open("a")
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            current = ["sorengame"]
            mgr, coordinator = self.manager(current)
            result = mgr.tick()
        finally:
            fcntl.flock(held.fileno(), fcntl.LOCK_UN)
            held.close()
        # fixture 時刻 (2026-09-06) の当日末は実時刻より過去のため即時 expired。
        self.assertEqual(result.status, "expired")
        self.assertEqual(result.detail, "program-wait-expired")
        self.assertEqual(coordinator.calls, [])


class TestUserFacingCommand(unittest.TestCase):
    def test_docich_routes_soren91_corner_status(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg = tmp_path / "docich.toml"
            cfg.write_text(
                f'[paths]\nstate_dir = "{tmp_path / "run"}"\n',
                encoding="utf-8",
            )
            result = subprocess.run(
                [str(repo / "bin/docich"), "--config", str(cfg), "soren91-corner", "status", "--json"],
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "idle")

    def test_docich_still_routes_soren91_corner_manual(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg = tmp_path / "docich.toml"
            cfg.write_text(
                f'[paths]\nstate_dir = "{tmp_path / "run"}"\n',
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    str(repo / "bin/docich"), "--config", str(cfg),
                    "soren91-corner-manual", "status", "--json",
                ],
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "idle")


class TestSystemdTemplates(unittest.TestCase):
    def test_service_and_timer_contract(self):
        root = Path(__file__).resolve().parents[1]
        service = (root / "scripts/systemd/docich-soren91-corner.service").read_text(
            encoding="utf-8"
        )
        timer = (root / "scripts/systemd/docich-soren91-corner.timer").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("[Install]", service)
        self.assertNotIn("ExecStartPre=", service)
        self.assertIn(
            "ExecStart=__DOCICH_ROOT__/bin/docich --config "
            "__DOCICH_ROOT__/config/docich.soren-live.toml soren91-corner tick",
            service,
        )
        self.assertIn("TimeoutStartSec=infinity", service)
        self.assertIn("OnCalendar=*-*-* *:*:00", timer)
        self.assertIn("Persistent=false", timer)


if __name__ == "__main__":
    unittest.main()

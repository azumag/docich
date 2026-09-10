import json
import os
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
            ensure_runtime=lambda: None,
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
            ensure_runtime=lambda: None,
        )
        with self.assertRaises(RetroCornerError):
            mgr.start()

    def test_self_play_game_without_agent_is_accepted(self):
        (self.root / "config" / "games" / "gnurobots.toml").write_text(
            """
[game]
name = "gnurobots"
title = "GNU Robots"
adapter = "cli"
[agent]
enabled = false
[corner]
self_play = true
""",
            encoding="utf-8",
        )
        path = self.root / "config" / "docich.toml"
        path.write_text('[retro_corner]\nenabled = true\ngames = ["gnurobots"]\n', encoding="utf-8")
        self.g = config.load_global(self.root)
        cfg = load_retro_corner_config(self.g)
        current = ["sorengame"]
        mgr = RetroCornerManager(
            self.g,
            config=cfg,
            coordinator=FakeCoordinator(current),
            now=lambda: self.now_value,
            sleep=lambda seconds: None,
            active_game_reader=lambda: current[0],
            ensure_runtime=lambda: None,
        )
        mgr._validate_games()

    def test_agent_disabled_without_self_play_is_rejected(self):
        (self.root / "config" / "games" / "gnurobots.toml").write_text(
            """
[game]
name = "gnurobots"
title = "GNU Robots"
adapter = "cli"
[agent]
enabled = false
""",
            encoding="utf-8",
        )
        path = self.root / "config" / "docich.toml"
        path.write_text('[retro_corner]\nenabled = true\ngames = ["gnurobots"]\n', encoding="utf-8")
        self.g = config.load_global(self.root)
        cfg = load_retro_corner_config(self.g)
        mgr = RetroCornerManager(
            self.g,
            config=cfg,
            coordinator=FakeCoordinator(["sorengame"]),
            now=lambda: self.now_value,
            sleep=lambda seconds: None,
            active_game_reader=lambda: "sorengame",
            ensure_runtime=lambda: None,
        )
        with self.assertRaises(RetroCornerError):
            mgr._validate_games()


class TestProductionProfile(unittest.TestCase):
    def test_only_soren_live_profile_enables_daily_corner(self):
        root = Path(__file__).resolve().parents[1]
        default_g = config.load_global(root, root / "config/docich.toml")
        live_g = config.load_global(root, root / "config/docich.soren-live.toml")
        default_cfg = load_retro_corner_config(default_g)
        live_cfg = load_retro_corner_config(live_g)
        self.assertFalse(default_cfg.enabled)
        self.assertTrue(live_cfg.enabled)
        self.assertEqual(live_g.display.number, 99)
        self.assertFalse(live_g.display.managed)
        self.assertEqual(
            (live_g.display.viewport_x, live_g.display.viewport_y,
             live_g.display.viewport_width, live_g.display.viewport_height),
            (0, 90, 960, 540),
        )
        self.assertEqual(live_cfg.start_hour, 19)
        self.assertEqual(live_cfg.duration_minutes, 30)
        self.assertEqual(live_cfg.games, ["gnurobots"])


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

    def test_outside_window_does_not_prepare_runtime(self):
        self.now_value = datetime(2026, 9, 6, 19, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        current = ["sorengame"]
        prepared = []
        coordinator = FakeCoordinator(current)
        mgr = RetroCornerManager(
            self.g,
            config=self.cfg,
            coordinator=coordinator,
            now=lambda: self.now_value,
            sleep=lambda seconds: None,
            active_game_reader=lambda: current[0],
            ensure_runtime=lambda: prepared.append(True),
        )
        self.assertEqual(mgr.tick().status, "noop")
        self.assertEqual(prepared, [])

    def test_start_prepares_runtime_once(self):
        current = [None]
        prepared = []
        coordinator = FakeCoordinator(current)
        mgr = RetroCornerManager(
            self.g,
            config=self.cfg,
            coordinator=coordinator,
            now=lambda: self.now_value,
            sleep=lambda seconds: None,
            active_game_reader=lambda: current[0],
            ensure_runtime=lambda: prepared.append(True),
        )
        self.assertEqual(mgr.start().status, "completed")
        self.assertEqual(prepared, [True])

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
        self.assertNotIn("ExecStartPre=", service)
        self.assertIn(
            "ExecStart=__DOCICH_ROOT__/bin/docich --config __DOCICH_ROOT__/config/docich.soren-live.toml retro-corner tick",
            service,
        )
        self.assertIn("TimeoutStartSec=infinity", service)
        self.assertIn("OnCalendar=*-*-* *:*:00", timer)
        self.assertIn("Persistent=false", timer)


if __name__ == "__main__":
    unittest.main()

class TestActualDuration(RetroCornerTestBase):
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
        self.assertEqual(sleeps, [3600])

class TestProgramBoundary(RetroCornerTestBase):
    def test_waits_for_new_cycle_and_keeps_full_duration(self):
        from dataclasses import replace
        from datetime import timedelta
        from docich.trading.soren_output import resolve_soren_root
        self.cfg = replace(self.cfg, require_program_boundary=True)
        root=resolve_soren_root(self.g)/'tmp/state'
        root.mkdir(parents=True, exist_ok=True)
        # An in-flight prediction is what the boundary must confirm.
        (root/'prediction_worker.pid').write_text(str(os.getpid()))
        (root/'current_prediction.json').write_text(json.dumps({'status':'ACTIVE'}))
        sleeps=[]
        def sleep(seconds):
            sleeps.append(seconds)
            self.now_value += timedelta(seconds=seconds)
            if seconds == 5:
                (root/'corner_boundary_prediction.json').write_text(json.dumps({'completed_at':self.now_value.timestamp()}))
        mgr,_=self.manager([None],sleep)
        mgr.tick()
        self.assertEqual(sleeps,[5,3600])
        self.assertEqual(mgr.status()['status'],'completed')

    def test_stale_waiting_request_from_previous_day_is_not_fired(self):
        from dataclasses import replace
        from datetime import timedelta
        self.cfg = replace(self.cfg, require_program_boundary=True)
        current = ["sorengame"]
        mgr, coordinator = self.manager(current)
        state = mgr._default_state()
        state.update(status="waiting", date="2026-09-05",
                     requested_at=(self.now_value - timedelta(days=1)).timestamp())
        mgr._write_state(state)
        result = mgr.tick()
        self.assertEqual(result.status, "expired")
        self.assertEqual(result.detail, "stale-request")
        self.assertEqual(coordinator.calls, [])
        self.assertEqual(mgr.status()["status"], "idle")

    def test_restart_during_transition_keeps_original_return_target(self):
        from dataclasses import replace
        self.cfg = replace(self.cfg, require_program_boundary=True)
        current=['robots']
        mgr, coordinator=self.manager(current)
        state=mgr._default_state()
        state.update(status='starting',date='2026-09-06',game='robots',previous_game=None)
        mgr._write_state(state)
        mgr.tick()
        self.assertIn(('stop',None),coordinator.calls)
        self.assertEqual(mgr.status()['status'],'completed')


class TestRetroCornerAnnounce(RetroCornerTestBase):
    def _manager_with_chat(self, current, chat):
        coordinator = FakeCoordinator(current)
        mgr = RetroCornerManager(
            self.g,
            config=self.cfg,
            coordinator=coordinator,
            now=lambda: self.now_value,
            sleep=lambda seconds: None,
            active_game_reader=lambda: current[0],
            ensure_runtime=lambda: None,
            chat=chat,
        )
        return mgr, coordinator

    def test_start_posts_intro_and_strategy(self):
        chats = []
        mgr, _ = self._manager_with_chat([None], chats.append)
        self.assertEqual(mgr.start().status, "completed")
        self.assertEqual(len(chats), 1)
        self.assertIn("レトロゲームコーナー", chats[0])
        self.assertIn("Robotsをお送りします", chats[0])
        self.assertIn("最新戦略", chats[0])
        self.assertTrue(mgr.status().get("announced"))

    def test_announce_failure_does_not_fail_corner(self):
        def boom(text):
            raise RuntimeError("sink down")

        mgr, _ = self._manager_with_chat([None], boom)
        self.assertEqual(mgr.start().status, "completed")
        state = mgr.status()
        self.assertNotIn("announced", state)
        self.assertIn("announce_error", state)

    def test_second_announce_is_skipped(self):
        chats = []
        mgr, _ = self._manager_with_chat([None], chats.append)
        mgr.start()
        mgr._locked_announce_again = None
        with mgr._locked():
            state = mgr._read_state()
            mgr._announce_start_locked(state)
        self.assertEqual(len(chats), 1)


class TestRetroCornerTickGuard(RetroCornerTestBase):
    def test_duplicate_tick_is_noop(self):
        import fcntl

        mgr, coordinator = self.manager(["sorengame"])
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

    def test_expired_when_program_busy_past_deadline(self):
        import fcntl
        from dataclasses import replace
        from docich.trading.soren_output import resolve_soren_root

        self.cfg = replace(self.cfg, require_program_boundary=True)
        root = resolve_soren_root(self.g) / 'tmp' / 'state'
        root.mkdir(parents=True, exist_ok=True)
        other = self.root / 'other_corner.json'
        other.write_text(json.dumps({'status': 'active'}), encoding='utf-8')
        (root / 'docich_program_active.json').write_text(
            json.dumps({'owner_state': str(other)}), encoding='utf-8')
        held = (root / 'docich_program.lock').open('a')
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            mgr, _ = self.manager(["sorengame"])
            result = mgr.tick()
        finally:
            fcntl.flock(held.fileno(), fcntl.LOCK_UN)
            held.close()
        #  fixture 時刻 (2026-09-06) の当日末は実時刻より過去のため即時 expired。
        self.assertEqual(result.status, "expired")


class TestRetroCornerImproveSpawn(RetroCornerTestBase):
    def _manager_with_spawn(self, current, spawned, agents):
        from dataclasses import replace
        cfg = replace(self.cfg, improve_agents=agents)
        coordinator = FakeCoordinator(current)
        mgr = RetroCornerManager(
            self.g,
            config=cfg,
            coordinator=coordinator,
            now=lambda: self.now_value,
            sleep=lambda seconds: None,
            active_game_reader=lambda: current[0],
            ensure_runtime=lambda: None,
            chat=lambda text: None,
            spawn=lambda argv, log_path: spawned.append((argv, log_path)),
        )
        return mgr

    def test_finish_spawns_improve_once(self):
        spawned = []
        mgr = self._manager_with_spawn([None], spawned, 'agent-a,agent-b')
        self.assertEqual(mgr.start().status, 'completed')
        self.assertEqual(len(spawned), 1)
        argv, log_path = spawned[0]
        self.assertIn('improve-once', argv)
        self.assertIn('2026-09-06', argv)
        state = mgr.status()
        self.assertEqual(state.get('improve_job', {}).get('spawned'), True)

    def test_no_spawn_without_agents(self):
        spawned = []
        mgr = self._manager_with_spawn([None], spawned, '')
        self.assertEqual(mgr.start().status, 'completed')
        self.assertEqual(spawned, [])


class TestRetroCornerImproveSpawnEnv(RetroCornerTestBase):
    def test_spawn_passes_real_ai_consent_to_child(self):
        import subprocess

        calls = []
        real_popen = subprocess.Popen

        def fake_popen(*args, **kwargs):
            calls.append((args, kwargs))
            return real_popen(['true'], stdout=subprocess.DEVNULL)

        import subprocess as sp_module
        mgr, _ = self.manager(["sorengame"])
        old = sp_module.Popen
        sp_module.Popen = fake_popen
        try:
            mgr._default_spawn_improve_proc(['echo', 'hi'], self.root / 'run' / 'x.log')
        finally:
            sp_module.Popen = old
        assert len(calls) == 1
        _, kwargs = calls[0]
        assert kwargs['env']['DOCICH_ALLOW_REAL_AI'] == '1'
        assert kwargs['start_new_session'] is True

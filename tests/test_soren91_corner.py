import fcntl
import json
import subprocess
import sys
import tempfile
import tomllib
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402
from docich.retro_corner import RetroCornerError  # noqa: E402
from docich.soren91_corner import (  # noqa: E402
    ANNOUNCE_TEXT,
    CHAT_ANNOUNCE_TEXT,
    LICENSE_NOTICE,
    DELIVERY_SOURCE,
    END_ANNOUNCE_TEXT,
    GAME_NAME,
    Soren91CornerConfig,
    Soren91CornerError,
    Soren91CornerManager,
    load_soren91_corner_config,
)

from docich.soren91_corner_manual import ManualSoren91CornerManager  # noqa: E402


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
    def test_default_off_and_live_schedule(self):
        root = Path(__file__).resolve().parents[1]
        default_g = config.load_global(root, root / "config/docich.toml")
        live_g = config.load_global(root, root / "config/docich.soren-live.toml")
        default_cfg = load_soren91_corner_config(default_g)
        live_cfg = load_soren91_corner_config(live_g)
        # 素の既定(config/docich.toml)は無効、live は 18:00/30分で有効。
        self.assertFalse(default_cfg.enabled)
        self.assertTrue(live_cfg.enabled)
        self.assertEqual(live_cfg.start_hour, 18)
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
        clock = [self.now_value]

        def sleep(seconds):
            sleeps.append(seconds)
            clock[0] = clock[0] + timedelta(seconds=seconds)

        mgr, coordinator = self.manager(current, now=lambda: clock[0], sleep=sleep)
        result = mgr.tick()
        self.assertEqual(result.status, "completed")
        self.assertEqual(
            coordinator.calls, [("switch", "soren91"), ("switch", "sorengame")]
        )
        self.assertEqual(current[0], "sorengame")
        # The corner polls the agent window while it holds the slot, so the wait
        # is split into chunks; the intent is that it spans the full 30 minutes.
        self.assertAlmostEqual(sum(sleeps), 30 * 60, delta=1.0)

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

    def test_queued_start_retries_outside_start_hour(self):
        current = ["sorengame"]

        class QueueOnce(FakeCoordinator):
            def __init__(self, current):
                super().__init__(current)
                self.queue_once = True

            def switch(self, game):
                if self.queue_once:
                    self.queue_once = False
                    return SimpleNamespace(
                        status="queued", error_code="queued", detail="queued"
                    )
                return super().switch(game)

        coordinator = QueueOnce(current)
        mgr, _ = self.manager(current, coordinator=coordinator)
        self.assertEqual(mgr.start().status, "queued")

        self.now_value = self.now_value.replace(hour=20)
        result = mgr.tick()

        self.assertEqual(result.status, "completed")
        self.assertEqual(current[0], "sorengame")
        self.assertEqual(coordinator.calls, [("switch", "soren91"), ("switch", "sorengame")])

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
        current = [None]
        sleeps = []
        clock = [self.now_value]

        def sleep(seconds):
            sleeps.append(seconds)
            clock[0] = clock[0] + timedelta(seconds=seconds)

        mgr, coordinator = self.manager(current, now=lambda: clock[0], sleep=sleep)
        original = coordinator.start

        def delayed(game):
            clock[0] += timedelta(minutes=25)
            return original(game)

        coordinator.start = delayed
        mgr.tick()
        # ends_at is fixed after the switch completes, so the 25-minute
        # transition must not shorten the 30-minute slot.
        self.assertAlmostEqual(sum(sleeps), 30 * 60, delta=1.0)

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
        self.assertEqual(self.chats, [CHAT_ANNOUNCE_TEXT, END_ANNOUNCE_TEXT])
        self.assertTrue(ANNOUNCE_TEXT.startswith("ソ連ゲーム91"))
        for banned in ("Mac", "レンダラー", "renderer", "CDP", "SRT", "bot"):
            self.assertNotIn(banned, ANNOUNCE_TEXT)
        self.assertTrue(mgr.status().get("announced"))
        self.assertTrue(mgr.status().get("end_announced"))

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
        self.assertEqual(self.chats, [CHAT_ANNOUNCE_TEXT, END_ANNOUNCE_TEXT])

    def test_announce_is_also_spoken_in_the_meriken_voice(self):
        voices = []
        current = [None]
        mgr, _ = self.manager(current, voice=voices.append)
        self.assertEqual(mgr.start().status, "completed")
        self.assertEqual(voices, [ANNOUNCE_TEXT, END_ANNOUNCE_TEXT])
        self.assertTrue(mgr.status().get("announced"))

    def test_voice_failure_does_not_fail_the_corner(self):
        def boom(text):
            raise RuntimeError("tts down")

        current = [None]
        mgr, _ = self.manager(current, voice=boom)
        self.assertEqual(mgr.start().status, "completed")
        self.assertTrue(mgr.status().get("announced"))
        self.assertIn("voice_error", mgr.status())

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


class TestSoren91LicenseNotice(unittest.TestCase):
    """Exercise both real announce methods without starting the game runtime."""

    managers = (Soren91CornerManager, ManualSoren91CornerManager)

    def manager(self, cls, *, chat=None, voice=None):
        mgr = cls.__new__(cls)
        mgr._chat = chat if chat is not None else self.chats.append
        mgr._voice = voice if voice is not None else self.voices.append
        return mgr

    def setUp(self):
        self.chats = []
        self.voices = []

    def test_notice_matches_requested_credit_exactly(self):
        self.assertEqual(
            LICENSE_NOTICE,
            "【91人対戦】ソ連ゲーム91 - たアケイク https://unityroom.com/games/sorengame91",
        )

    def test_both_start_paths_post_credit_once_without_speaking_it(self):
        for cls in self.managers:
            with self.subTest(manager=cls.__name__):
                self.setUp()
                state = {"game": GAME_NAME}
                self.manager(cls)._announce_start_locked(state)
                self.assertEqual(len(self.chats), 1)
                self.assertEqual(len(self.voices), 1)
                self.assertEqual(self.chats[0], f"{self.voices[0]} {LICENSE_NOTICE}")
                self.assertEqual(self.chats[0].count(LICENSE_NOTICE), 1)
                self.assertNotIn("https://", self.voices[0])
                self.assertTrue(state["announced"])

    def test_repeated_announce_does_not_repeat_credit(self):
        for cls in self.managers:
            with self.subTest(manager=cls.__name__):
                self.setUp()
                mgr = self.manager(cls)
                state = {"game": GAME_NAME}
                mgr._announce_start_locked(state)
                mgr._announce_start_locked(state)
                self.assertEqual(len(self.chats), 1)
                self.assertEqual(len(self.voices), 1)

    def test_missing_game_does_not_post_credit(self):
        for cls in self.managers:
            for game in (None, "", 91):
                with self.subTest(manager=cls.__name__, game=game):
                    self.setUp()
                    state = {"game": game}
                    self.manager(cls)._announce_start_locked(state)
                    self.assertEqual(self.chats, [])
                    self.assertEqual(self.voices, [])
                    self.assertNotIn("announced", state)

    def test_failed_chat_can_retry_the_whole_credited_announcement(self):
        def fail(text):
            raise RuntimeError("sink down")

        for cls in self.managers:
            with self.subTest(manager=cls.__name__):
                self.setUp()
                mgr = self.manager(cls, chat=fail)
                state = {"game": GAME_NAME}
                mgr._announce_start_locked(state)
                self.assertNotIn("announced", state)
                self.assertIn("announce_error", state)
                self.assertEqual(self.voices, [])
                mgr._chat = self.chats.append
                mgr._announce_start_locked(state)
                self.assertEqual(len(self.chats), 1)
                self.assertTrue(self.chats[0].endswith(LICENSE_NOTICE))
                self.assertTrue(state["announced"])
                self.assertNotIn("announce_error", state)

    def test_voice_failure_does_not_duplicate_the_posted_credit(self):
        def fail(text):
            raise RuntimeError("tts down")

        for cls in self.managers:
            with self.subTest(manager=cls.__name__):
                self.setUp()
                mgr = self.manager(cls, voice=fail)
                state = {"game": GAME_NAME}
                mgr._announce_start_locked(state)
                mgr._announce_start_locked(state)
                self.assertEqual(len(self.chats), 1)
                self.assertTrue(state["announced"])
                self.assertIn("voice_error", state)

    def test_new_corner_state_posts_the_credit_again(self):
        for cls in self.managers:
            with self.subTest(manager=cls.__name__):
                self.setUp()
                mgr = self.manager(cls)
                mgr._announce_start_locked({"game": GAME_NAME})
                mgr._announce_start_locked({"game": GAME_NAME})
                self.assertEqual(len(self.chats), 2)
                self.assertTrue(all(text.endswith(LICENSE_NOTICE) for text in self.chats))


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
            "EnvironmentFile=-/home/ubuntu/soren/soren91-macos-agent.env",
            service,
        )
        self.assertIn(
            "ExecStart=__DOCICH_ROOT__/bin/docich --config "
            "__DOCICH_ROOT__/config/docich.soren-live.toml soren91-corner tick",
            service,
        )
        self.assertIn("TimeoutStartSec=infinity", service)
        self.assertIn("OnCalendar=*-*-* *:*:00", timer)
        self.assertIn("Persistent=false", timer)


class TestShippedGameConfig(unittest.TestCase):
    def test_agent_and_cdp_proxy_settings(self):
        root = Path(__file__).resolve().parents[1]
        with (root / "config" / "games" / "soren91.toml").open("rb") as fh:
            data = tomllib.load(fh)
        self.assertTrue(
            data["agent"]["enabled"],
            "Soren91コーナーはOCI botを起動する (表示専用に戻す場合はテストも更新)",
        )
        bot_path = str((data.get("soren91") or {}).get("bot_path") or "").strip()
        self.assertTrue(bot_path, "agent.enabled=true では bot_path が必要")
        self.assertEqual(
            data["soren91"]["cdp_port"],
            19093,
            "cdp_port は OCI から到達できる Mac 側 Tailscale プロキシの port "
            "(Mac 内 Chrome CDP の 9322 ではない)",
        )


class TestSoren91CornerAgentSupervision(Soren91CornerTestBase):
    """A bot that dies mid-slot must not leave dead air until ends_at."""

    def _run(self, current, probe, *, poll_s=60.0, strikes=1):
        clock = [self.now_value]

        def sleep(seconds):
            clock[0] = clock[0] + timedelta(seconds=seconds)

        mgr, coordinator = self.manager(
            current,
            now=lambda: clock[0],
            sleep=sleep,
            agent_alive_probe=probe,
            agent_liveness_poll_s=poll_s,
            agent_liveness_strikes=strikes,
        )
        return mgr, coordinator

    def test_dead_agent_ends_corner_early_and_restores_previous_game(self):
        mgr, coordinator = self._run(["sorengame"], lambda: False)
        result = mgr.start()
        self.assertEqual(result.status, "completed")
        state = mgr.status()
        self.assertEqual(state.get("status"), "completed")
        self.assertEqual(state.get("early_end_reason"), "soren91-agent-not-alive")
        self.assertIn(("switch", "sorengame"), coordinator.calls)

    def test_alive_agent_runs_to_the_scheduled_end(self):
        mgr, coordinator = self._run(["sorengame"], lambda: True)
        self.assertEqual(mgr.start().status, "completed")
        self.assertNotIn("early_end_reason", mgr.status())
        self.assertIn(("switch", "sorengame"), coordinator.calls)

    def test_indeterminate_probe_never_ends_the_slot_early(self):
        mgr, _coordinator = self._run(["sorengame"], lambda: None)
        self.assertEqual(mgr.start().status, "completed")
        self.assertNotIn("early_end_reason", mgr.status())

    def test_a_single_dead_probe_is_debounced(self):
        answers = iter([False, True, True, True, True, True, True, True, True, False])
        mgr, _coordinator = self._run(["sorengame"], lambda: next(answers, True), strikes=2)
        self.assertEqual(mgr.start().status, "completed")
        self.assertNotIn("early_end_reason", mgr.status())

    def test_raising_probe_is_treated_as_indeterminate(self):
        def boom():
            raise RuntimeError("tmux unavailable")

        mgr, _coordinator = self._run(["sorengame"], boom)
        self.assertEqual(mgr.start().status, "completed")
        self.assertNotIn("early_end_reason", mgr.status())

    def test_invalid_liveness_settings_are_rejected(self):
        with self.assertRaises(Soren91CornerError):
            self.manager(["sorengame"], agent_liveness_poll_s=0)
        with self.assertRaises(Soren91CornerError):
            self.manager(["sorengame"], agent_liveness_strikes=0)

    def test_default_liveness_cadence_ends_a_dead_bot_within_two_polls(self):
        # The production default must stay short: the broadcast keeps showing
        # the last captured frame until the corner ends (docich #464 /
        # soviet_now #486, measured 2026-09-22). Two misses 10s apart are
        # conclusive because main.pid is written once at bot startup and only
        # removed by the bot's own cleanup.
        clock = [self.now_value]

        def sleep(seconds):
            clock[0] = clock[0] + timedelta(seconds=seconds)

        mgr, _coordinator = self.manager(
            ["sorengame"],
            now=lambda: clock[0],
            sleep=sleep,
            agent_alive_probe=lambda: False,
        )
        self.assertEqual(mgr._agent_liveness_poll_s, 10.0)
        self.assertEqual(mgr._agent_liveness_strikes, 2)
        result = mgr.start()
        self.assertEqual(result.status, "completed")
        self.assertEqual(mgr.status().get("early_end_reason"), "soren91-agent-not-alive")
        self.assertEqual(clock[0] - self.now_value, timedelta(seconds=20))



class TestSoren91RotationTargetValidation(Soren91CornerTestBase):
    """Same target-override contract as the NetHack corner (#986)."""

    def test_accepts_the_rotation_target_override(self):
        mgr, _ = self.manager([None])
        mgr._validate_games(["soren91"])

    def test_rejects_any_other_target(self):
        mgr, _ = self.manager([None])
        for names in (["ninvaders"], [], ["other", "soren91-manual"]):
            with self.subTest(names=names):
                with self.assertRaises(RetroCornerError):
                    mgr._validate_games(names)

    def test_default_call_still_validates_the_owned_game(self):
        mgr, _ = self.manager([None])
        mgr._validate_games()


class TestManualSoren91RotationTargetValidation(Soren91CornerTestBase):
    """The manual corner must share the exact same contract (#998).

    ``ManualSoren91CornerManager`` used to declare ``_validate_games(self)``
    while ``RetroCornerManager._begin_locked`` passes ``[target]``, so the
    #986 TypeError was one rotation mode away for this class too. Its config
    is built with ``games=[GAME_NAME]``, so the fixed-manager rule applies
    verbatim.
    """

    def _manual(self):
        return ManualSoren91CornerManager(
            self.g,
            now=lambda: self.now_value,
            sleep=lambda seconds: None,
            active_game_reader=lambda: "sorengame",
            ensure_runtime=lambda: None,
        )

    def test_accepts_the_owned_rotation_target(self):
        self._manual()._validate_games(["soren91"])

    def test_rejects_empty_unowned_and_superset_targets(self):
        mgr = self._manual()
        for names in ([], ["nsnake"], ["soren91", "nsnake"]):
            with self.subTest(names=names):
                with self.assertRaises(RetroCornerError):
                    mgr._validate_games(names)

    def test_default_call_still_validates_the_owned_game(self):
        self._manual()._validate_games()


if __name__ == "__main__":
    unittest.main()

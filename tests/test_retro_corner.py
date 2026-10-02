import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from dataclasses import replace
from datetime import datetime, date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import config  # noqa: E402
from docich.retro_corner import (  # noqa: E402
    RetroCornerConfig,
    RetroCornerError,
    RetroCornerManager,
    describe_strategy_change,
    load_retro_corner_config,
    select_game,
)
from docich.game_switch import GameSwitchBusyError  # noqa: E402
from docich.naming import runtime_names  # noqa: E402


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


class TestFailedRotationStartReconciliation(RetroCornerTestBase):
    def _setup_failed_start(self, *, terminal=True, terminal_status="rolled_back",
                            corner_status="starting", error_code="recovery"):
        mgr, coordinator = self.manager(["sorengame"])
        request_id = str(uuid.uuid4())
        state = mgr._default_state()
        state.update(status=corner_status, game="robots", previous_game="sorengame",
                     rotation_request_id=request_id, switch_request_id=request_id,
                     started_at=self.now_value.isoformat())
        if corner_status == "failed":
            state.update(completed_at=self.now_value.isoformat(), last_error_code=error_code)
        mgr._write_state(state)
        canonical = mgr.store.initialize()
        names = runtime_names(1)
        canonical.update(phase="ready", next_generation=2, active={
            "game": "sorengame", "adapter": "browser", "generation": 1,
            "runtime_id": "g1-abcdef", "lease_id": str(uuid.uuid4()),
            "game_window": names.game_window, "agent_window": names.agent_window,
            "adapter_session": names.adapter_session,
            "started_at": "2026-09-23T12:00:00Z",
        })
        mgr.store.canonical.save(canonical)
        accepted = mgr.store.accept_request(request_id, "switch", "robots")
        if terminal:
            result = {
                "request_id": request_id, "operation": "switch",
                "status": terminal_status,
                "from_game": "sorengame", "to_game": "robots",
                "generation": accepted.generation, "error_code": error_code,
            }
            if terminal_status == "rolled_back":
                result["restored_generation"] = accepted.generation + 1
            canonical, _ = mgr.store.canonical.load()
            if terminal_status == "rolled_back":
                restored = accepted.generation + 1
                names = runtime_names(restored)
                canonical["active"].update(
                    generation=restored, runtime_id=f"g{restored}-abcdef",
                    game_window=names.game_window, agent_window=names.agent_window,
                    adapter_session=names.adapter_session,
                )
                canonical["next_generation"] = restored + 1
            canonical.update(phase="ready", operation=None, request_id=None,
                             deadline_at=None, last_result=result)
            mgr.store.canonical.save(canonical)
            mgr.store.finish_request(request_id, terminal_status, result)
        return mgr, coordinator, request_id

    def test_exact_terminal_receipt_and_ready_previous_game_interrupt_start(self):
        mgr, coordinator, request_id = self._setup_failed_start()
        self.assertTrue(mgr.reconcile_failed_rotation_start(request_id))
        result = mgr._read_state()
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(result["end_reason"], "switch-terminal-before-corner-active")
        self.assertEqual(result["rotation_request_id"], request_id)
        self.assertIsNotNone(result["completed_at"])
        self.assertTrue(mgr.reconcile_failed_rotation_start(request_id))
        self.assertEqual(mgr._read_state()["completed_at"], result["completed_at"])
        self.assertEqual(coordinator.calls, [])

    def test_exact_start_failed_receipt_terminalizes_only_the_matching_failed_corner(self):
        mgr, coordinator, request_id = self._setup_failed_start(
            corner_status="failed", error_code="start_failed")
        original_completed_at = mgr._read_state()["completed_at"]

        self.assertTrue(mgr.reconcile_failed_rotation_start(request_id))

        result = mgr._read_state()
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(result["end_reason"], "switch-terminal-before-corner-active")
        self.assertEqual(result["rotation_request_id"], request_id)
        self.assertEqual(result["completed_at"], original_completed_at)
        self.assertIsNone(result["last_error_code"])
        self.assertTrue(mgr.reconcile_failed_rotation_start(request_id))
        self.assertEqual(mgr._read_state()["completed_at"], original_completed_at)
        self.assertEqual(coordinator.calls, [])

    def test_quiesce_failed_receipt_terminalizes_the_rolled_back_failed_corner(self):
        mgr, coordinator, request_id = self._setup_failed_start(
            corner_status="failed", error_code="quiesce_failed")

        self.assertTrue(mgr.reconcile_failed_rotation_start(request_id))

        result = mgr._read_state()
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(result["end_reason"], "switch-terminal-before-corner-active")
        self.assertEqual(result["rotation_request_id"], request_id)
        self.assertIsNone(result["last_error"])
        self.assertIsNone(result["last_error_code"])
        self.assertEqual(coordinator.calls, [])

    def test_readiness_timeout_receipt_terminalizes_the_rolled_back_failed_corner(self):
        # A target that never became ready rolls back to the previous game
        # exactly like the other known start errors; without this case the
        # whole corner rotation latches forever and recover-failed refuses.
        mgr, coordinator, request_id = self._setup_failed_start(
            corner_status="failed", error_code="readiness_timeout")

        self.assertTrue(mgr.reconcile_failed_rotation_start(request_id))

        result = mgr._read_state()
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(result["end_reason"], "switch-terminal-before-corner-active")
        self.assertEqual(result["rotation_request_id"], request_id)
        self.assertIsNone(result["last_error"])
        self.assertIsNone(result["last_error_code"])
        self.assertEqual(coordinator.calls, [])

    def test_failed_corner_requires_known_matching_start_error_in_terminal_receipt(self):
        for state_error, receipt_error in (
            ("start_failed", "agent_start_failed"),
            ("recovery", "recovery"),
            ("quiesce_failed", "start_failed"),
        ):
            with self.subTest(state_error=state_error, receipt_error=receipt_error):
                mgr, _, request_id = self._setup_failed_start(
                    corner_status="failed", error_code=state_error)
                receipt = mgr.store.receipts.load(request_id)
                receipt["result"]["error_code"] = receipt_error
                mgr.store.receipts.save(receipt)

                self.assertFalse(mgr.reconcile_failed_rotation_start(request_id))
                self.assertEqual(mgr._read_state()["status"], "failed")

    def test_failed_corner_stays_failed_when_target_runtime_cleanup_is_pending(self):
        mgr, _, request_id = self._setup_failed_start(
            corner_status="failed", error_code="agent_start_failed")
        receipt = mgr.store.receipts.load(request_id)
        receipt["result"]["cleanup_pending"] = True
        mgr.store.receipts.save(receipt)

        self.assertFalse(mgr.reconcile_failed_rotation_start(request_id))
        self.assertEqual(mgr._read_state()["status"], "failed")

    def test_nonterminal_receipt_and_draining_canonical_keep_start(self):
        mgr, _, request_id = self._setup_failed_start(terminal=False)
        self.assertFalse(mgr.reconcile_failed_rotation_start(request_id))
        self.assertEqual(mgr._read_state()["status"], "starting")
        receipt = mgr.store.receipts.load(request_id)
        mgr.store.finish_request(request_id, "failed", {
            "request_id": request_id, "status": "failed",
            "from_game": "sorengame", "to_game": "robots",
            "generation": receipt["generation"], "error_code": "quiesce_failed",
        })
        canonical, _ = mgr.store.canonical.load()
        canonical.update(phase="draining", operation="restart", request_id=str(uuid.uuid4()),
                         deadline_at="2026-09-24T00:48:10Z")
        mgr.store.canonical.save(canonical)
        self.assertFalse(mgr.reconcile_failed_rotation_start(request_id))
        self.assertEqual(mgr._read_state()["status"], "starting")

    def test_failed_receipt_lacks_restored_generation_and_stays_latched(self):
        mgr, _, request_id = self._setup_failed_start(terminal_status="failed")
        self.assertFalse(mgr.reconcile_failed_rotation_start(request_id))
        self.assertEqual(mgr._read_state()["status"], "starting")

    def test_newer_soren_restart_result_can_reconcile_old_request(self):
        mgr, _, request_id = self._setup_failed_start()
        canonical, _ = mgr.store.canonical.load()
        canonical["last_result"] = {
            "request_id": str(uuid.uuid4()), "operation": "restart",
            "status": "succeeded", "from_game": "sorengame",
            "to_game": "sorengame", "generation": 4,
        }
        canonical["active"]["generation"] = 4
        canonical["active"]["runtime_id"] = "g4-abcdef"
        names = runtime_names(4)
        canonical["active"].update(game_window=names.game_window,
                                    agent_window=names.agent_window,
                                    adapter_session=names.adapter_session)
        canonical["next_generation"] = 5
        mgr.store.canonical.save(canonical)
        self.assertTrue(mgr.reconcile_failed_rotation_start(request_id))
        self.assertEqual(mgr._read_state()["status"], "interrupted")
        completed_at = mgr._read_state()["completed_at"]
        self.assertTrue(mgr.reconcile_failed_rotation_start(request_id))
        self.assertEqual(mgr._read_state()["completed_at"], completed_at)

    def test_old_rolled_back_receipt_does_not_bypass_later_restart_drain(self):
        mgr, _, request_id = self._setup_failed_start()
        canonical, _ = mgr.store.canonical.load()
        canonical.update(phase="draining", operation="restart", request_id=str(uuid.uuid4()),
                         deadline_at="2026-09-24T00:48:10Z")
        mgr.store.canonical.save(canonical)
        self.assertFalse(mgr.reconcile_failed_rotation_start(request_id))
        self.assertEqual(mgr._read_state()["status"], "starting")

    def test_newer_unfinished_cleanup_keeps_start(self):
        mgr, _, request_id = self._setup_failed_start()
        canonical, _ = mgr.store.canonical.load()
        canonical["last_result"] = {"status": "failed", "cleanup_pending": True}
        mgr.store.canonical.save(canonical)
        self.assertFalse(mgr.reconcile_failed_rotation_start(request_id))
        self.assertEqual(mgr._read_state()["status"], "starting")

    def test_restored_generation_not_currently_owned_keeps_start(self):
        mgr, _, request_id = self._setup_failed_start()
        canonical, _ = mgr.store.canonical.load()
        canonical["active"]["generation"] = 1
        canonical["active"]["runtime_id"] = "g1-abcdef"
        names = runtime_names(1)
        canonical["active"].update(game_window=names.game_window,
                                    agent_window=names.agent_window,
                                    adapter_session=names.adapter_session)
        mgr.store.canonical.save(canonical)
        self.assertFalse(mgr.reconcile_failed_rotation_start(request_id))
        self.assertEqual(mgr._read_state()["status"], "starting")

    def test_invalid_active_generation_fails_closed_without_corner_write(self):
        mgr, _, request_id = self._setup_failed_start()
        canonical, _ = mgr.store.canonical.load()
        canonical["active"]["generation"] = "3"
        with patch.object(mgr.store.canonical, "load", return_value=(canonical, False)):
            self.assertFalse(mgr.reconcile_failed_rotation_start(request_id))
        self.assertEqual(mgr._read_state()["status"], "starting")

    def test_canonical_validation_rejects_invalid_generation_on_disk(self):
        from docich.game_switch import StateCorruptError

        mgr, _, request_id = self._setup_failed_start()
        canonical, _ = mgr.store.canonical.load()
        canonical["active"]["generation"] = "3"
        mgr.store.canonical.path.write_text(json.dumps(canonical), encoding="utf-8")
        with self.assertRaises(StateCorruptError):
            mgr.reconcile_failed_rotation_start(request_id)
        self.assertEqual(mgr._read_state()["status"], "starting")

    def test_receipt_result_mismatch_keeps_start(self):
        mgr, _, request_id = self._setup_failed_start()
        receipt = mgr.store.receipts.load(request_id)
        receipt["result"]["from_game"] = "nethack"
        mgr.store.receipts.save(receipt)
        self.assertFalse(mgr.reconcile_failed_rotation_start(request_id))
        self.assertEqual(mgr._read_state()["status"], "starting")

    def test_boolean_result_generation_cannot_match_integer_receipt_generation(self):
        mgr, _, request_id = self._setup_failed_start()
        receipt = mgr.store.receipts.load(request_id)
        receipt["result"]["generation"] = True
        mgr.store.receipts.save(receipt)
        self.assertFalse(mgr.reconcile_failed_rotation_start(request_id))
        self.assertEqual(mgr._read_state()["status"], "starting")

    def test_cleanup_pending_accepts_only_none_or_false(self):
        for field in ("receipt", "last_result"):
            for value, allowed in ((None, True), (False, True), (True, False),
                                   (0, False), ("pending", False)):
                with self.subTest(field=field, value=value):
                    mgr, _, request_id = self._setup_failed_start()
                    if field == "receipt":
                        receipt = mgr.store.receipts.load(request_id)
                        receipt["result"]["cleanup_pending"] = value
                        mgr.store.receipts.save(receipt)
                    else:
                        canonical, _ = mgr.store.canonical.load()
                        canonical["last_result"]["cleanup_pending"] = value
                        mgr.store.canonical.save(canonical)
                    self.assertEqual(mgr.reconcile_failed_rotation_start(request_id), allowed)
                    self.assertEqual(mgr._read_state()["status"],
                                     "interrupted" if allowed else "starting")

    def test_malformed_last_result_keeps_start(self):
        mgr, _, request_id = self._setup_failed_start()
        canonical, _ = mgr.store.canonical.load()
        canonical["last_result"] = "unknown"
        mgr.store.canonical.save(canonical)
        self.assertFalse(mgr.reconcile_failed_rotation_start(request_id))
        self.assertEqual(mgr._read_state()["status"], "starting")

    def test_mismatch_or_unreleased_runtime_keeps_start(self):
        mgr, _, request_id = self._setup_failed_start()
        self.assertFalse(mgr.reconcile_failed_rotation_start(str(uuid.uuid4())))
        canonical, _ = mgr.store.canonical.load()
        canonical["retiring"] = [dict(canonical["active"])]
        canonical["retiring"][0]["generation"] = 4
        canonical["retiring"][0]["runtime_id"] = "g4-abcdef"
        names = runtime_names(4)
        canonical["retiring"][0].update(game_window=names.game_window,
                                         agent_window=names.agent_window,
                                         adapter_session=names.adapter_session)
        canonical["next_generation"] = 5
        mgr.store.canonical.save(canonical)
        self.assertFalse(mgr.reconcile_failed_rotation_start(request_id))
        self.assertEqual(mgr._read_state()["status"], "starting")


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

    def test_rotation_rejects_duplicate_dedicated_nethack_slot(self):
        path = self.root / "config" / "docich.toml"
        path.write_text(
            '[retro_corner]\nmode = "rotation"\ngames = ["nethack"]\n'
            '[nethack_corner]\nenabled = true\n',
            encoding="utf-8",
        )
        self.g = config.load_global(self.root)
        with self.assertRaisesRegex(RetroCornerError, "専用の\\[nethack_corner\\]"):
            load_retro_corner_config(self.g)

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
    def test_only_soren_live_profile_enables_rolling_corner(self):
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
        # Rolling rotation: 24 hours divided by the registered game count.
        self.assertEqual(live_cfg.mode, "rotation")
        self.assertEqual(live_cfg.rotation_period_hours, 24.0)
        self.assertEqual(live_cfg.rotation_wait_minutes, 10)
        self.assertEqual(live_cfg.duration_minutes, 20)
        self.assertEqual(live_cfg.start_hour, 19)  # mode="daily" へ戻すとき用に残す
        self.assertEqual(
            live_cfg.games,
            [
                "ninvaders", "nsnake", "bastet", "moon-buggy", "pacman4console",
                "hanjuku-hero",
            ],
        )
        self.assertTrue(live_cfg.daily_each_game)
        self.assertTrue(live_cfg.randomize_start)
        self.assertEqual(live_cfg.target_matches, 3)


class TestRetroCornerSelection(unittest.TestCase):
    def test_selection_is_deterministic_per_local_date(self):
        games = ["robots", "ninvaders", "nsnake"]
        day = date(2026, 9, 6)
        expected = games[day.toordinal() % len(games)]
        self.assertEqual(select_game(games, day), expected)
        self.assertEqual(select_game(games, day), expected)


class TestRetroCornerRotation(RetroCornerTestBase):
    def _rotation_manager(self):
        games = ["ninvaders", "nsnake", "bastet", "moon-buggy", "pacman4console"]
        self.cfg = replace(
            self.cfg,
            games=games,
            mode="rotation",
            duration_minutes=1,
            rotation_period_hours=24.0,
            rotation_wait_minutes=10,
        )
        current = ["sorengame"]
        coordinator = FakeCoordinator(current)
        mgr = RetroCornerManager(
            self.g,
            config=self.cfg,
            coordinator=coordinator,
            now=lambda: self.now_value,
            sleep=lambda seconds: None,
            active_game_reader=lambda: current[0],
            ensure_runtime=lambda: None,
            chat=lambda text: None,
            stream_game=lambda game: None,
        )
        # The selection contract is under test here; game-file/executable
        # validation is covered by the existing production-profile tests.
        mgr._validate_games = lambda names=None: None
        mgr._playable_games = lambda: list(games)
        mgr._other_corner_busy = lambda: None
        mgr._target_reached = lambda state: True
        return mgr, coordinator, games

    def test_interval_is_24_hours_divided_by_registered_games(self):
        mgr, _coordinator, games = self._rotation_manager()
        self.assertEqual(mgr._rotation_interval_seconds(), 24 * 3600 / len(games))

    def test_first_five_slots_are_random_without_repeating_recent_games(self):
        mgr, _coordinator, games = self._rotation_manager()
        seen = []
        interval = timedelta(seconds=mgr._rotation_interval_seconds())
        for slot in range(6):
            self.now_value = datetime(2026, 9, 6, 20, 0, tzinfo=ZoneInfo("Asia/Tokyo")) + slot * interval
            result = mgr.tick()
            self.assertEqual(result.status, "completed")
            seen.append(mgr.status()["game"])

        self.assertEqual(len(set(seen[: len(games)])), len(games))
        # The first game is exactly 24 hours old at slot six and becomes
        # eligible again; games selected later are still cooling down.
        self.assertEqual(seen[-1], seen[0])

    def test_due_rotation_keeps_a_queued_switch_for_next_tick(self):
        mgr, _coordinator, games = self._rotation_manager()
        current = mgr.coordinator.current

        class QueuingCoordinator(FakeCoordinator):
            def __init__(self, current):
                super().__init__(current)
                self.queue_once = True

            def switch(self, game):
                self.calls.append(("switch", game))
                if self.queue_once:
                    self.queue_once = False
                    return SimpleNamespace(
                        status="queued",
                        request_id="queued-request",
                        error_code="queued",
                        detail="queued",
                    )
                self.current[0] = game
                return SimpleNamespace(
                    status="succeeded", request_id="queued-request", error_code=None, detail=None
                )

        coordinator = QueuingCoordinator(current)
        mgr.coordinator = coordinator

        first = mgr.tick()

        self.assertEqual(first.status, "queued")
        self.assertEqual(mgr.status()["status"], "starting")
        self.assertTrue(mgr.status()["switch_request_id"])

        second = mgr.tick()

        self.assertEqual(second.status, "completed")
        self.assertEqual(current[0], "sorengame")
        self.assertEqual(
            [call[0] for call in coordinator.calls],
            ["switch", "switch", "switch"],
        )
        self.assertIn(coordinator.calls[0][1], games)
        self.assertEqual(coordinator.calls[1][1], coordinator.calls[0][1])
        self.assertEqual(coordinator.calls[2][1], "sorengame")

    def test_active_rotation_tick_repairs_agent_without_switching_game(self):
        mgr, coordinator, _games = self._rotation_manager()
        calls = []
        mgr._agent_repair = lambda game: calls.append(game) or True
        mgr._write_state(
            {
                **mgr._default_state(),
                "status": "active",
                "game": "bastet",
                "started_at": self.now_value.isoformat(),
                "ends_at": (self.now_value + timedelta(minutes=1)).isoformat(),
            }
        )

        result = mgr.tick()

        self.assertEqual(result.status, "noop")
        self.assertEqual(result.detail, "already-active")
        self.assertEqual(calls, ["bastet"])
        self.assertEqual(coordinator.calls, [])

    def test_games_selected_within_24_hours_are_not_fallback_candidates(self):
        mgr, _coordinator, games = self._rotation_manager()
        now = self.now_value
        rotation = {
            "games": games,
            "interval_seconds": mgr._rotation_interval_seconds(),
            "anchor_at": now.isoformat(),
            "next_due_at": now.isoformat(),
            "selection_history": [
                {"game": game, "selected_at": now.isoformat()} for game in games
            ],
        }
        state = mgr._default_state()
        state["rotation"] = rotation
        mgr._write_state(state)

        result = mgr.tick()

        self.assertEqual(result.status, "noop")
        self.assertEqual(result.detail, "rotation-no-eligible-game")

    def _enable_fixed_corner(self, section: str, start_hour: int, duration_minutes: int = 30):
        self.g.config_path.write_text(
            self.g.config_path.read_text(encoding="utf-8")
            + f"\n[{section}]\nenabled = true\nstart_hour = {start_hour}\n"
            + f"duration_minutes = {duration_minutes}\ntimezone = \"Asia/Tokyo\"\n",
            encoding="utf-8",
        )

    def test_due_rotation_defers_before_imminent_fixed_slot(self):
        self._enable_fixed_corner("paper_corner", 22)
        mgr, coordinator, _games = self._rotation_manager()
        self.now_value = self.now_value.replace(hour=21, minute=50)

        result = mgr.tick()

        self.assertEqual(result.status, "noop")
        self.assertEqual(result.detail, "rotation-deferred:fixed-slot-imminent:paper_corner")
        self.assertEqual(coordinator.calls, [])

    def test_due_rotation_defers_while_fixed_slot_is_active(self):
        self._enable_fixed_corner("soren91_corner", 18)
        mgr, coordinator, _games = self._rotation_manager()
        self.now_value = self.now_value.replace(hour=18, minute=5)

        result = mgr.tick()

        self.assertEqual(result.status, "noop")
        self.assertEqual(result.detail, "rotation-deferred:fixed-slot-active:soren91_corner")
        self.assertEqual(coordinator.calls, [])

    def test_due_rotation_can_start_when_it_finishes_before_fixed_slot_buffer(self):
        self._enable_fixed_corner("paper_corner", 22)
        mgr, coordinator, _games = self._rotation_manager()
        self.now_value = self.now_value.replace(hour=21, minute=25)

        result = mgr.tick()

        self.assertEqual(result.status, "completed")
        self.assertEqual(coordinator.calls[0][0], "switch")

    def test_duration_less_fixed_corner_is_not_statically_reserved(self):
        # The content-driven PAPER corner has no fixed duration, so no static
        # window can be reserved for it. The rotation starts and the runtime
        # program-slot exclusivity makes the duration-less corner wait instead.
        self.g.config_path.write_text(
            self.g.config_path.read_text(encoding="utf-8")
            + '\n[paper_corner]\nenabled = true\nstart_hour = 22\ntimezone = "Asia/Tokyo"\n',
            encoding="utf-8",
        )
        mgr, coordinator, _games = self._rotation_manager()
        self.now_value = self.now_value.replace(hour=21, minute=50)

        result = mgr.tick()

        self.assertEqual(result.status, "completed")
        self.assertEqual(coordinator.calls[0][0], "switch")

    def test_recovery_required_retries_only_after_canonical_failed_recovery(self):
        from unittest.mock import patch

        class RecoveringCoordinator(FakeCoordinator):
            def __init__(self, current):
                super().__init__(current)
                self.fail_once = True
                self.recover_calls = 0

            def switch(self, game):
                self.calls.append(("switch", game))
                if self.fail_once:
                    self.fail_once = False
                    return SimpleNamespace(
                        status="failed",
                        error_code="recovery_required",
                        detail="canonical stateの復旧が必要です",
                    )
                self.current[0] = game
                return SimpleNamespace(status="succeeded", error_code=None, detail=None)

            def recover(self, **_kwargs):
                self.recover_calls += 1
                return SimpleNamespace(status="succeeded", error_code=None, detail="recovered")

        current = ["sorengame"]
        coordinator = RecoveringCoordinator(current)
        mgr = RetroCornerManager(
            self.g,
            config=self.cfg,
            coordinator=coordinator,
            now=lambda: self.now_value,
            sleep=lambda seconds: None,
            active_game_reader=lambda: current[0],
            ensure_runtime=lambda: None,
        )
        with patch.object(
            mgr.store.canonical,
            "load",
            return_value=({"phase": "failed", "previous": None, "active": None}, False),
        ):
            result = mgr._transition_to("sorengame", "robots")

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(coordinator.recover_calls, 1)
        self.assertEqual(coordinator.calls, [("switch", "robots"), ("switch", "robots")])
        self.assertEqual(current[0], "robots")

    def test_recovery_required_does_not_touch_live_draining(self):
        from unittest.mock import patch

        class FailingCoordinator(FakeCoordinator):
            def __init__(self, current):
                super().__init__(current)
                self.recover_calls = 0

            def switch(self, game):
                self.calls.append(("switch", game))
                return SimpleNamespace(
                    status="failed", error_code="recovery_required", detail="canonical stateの復旧が必要です"
                )

            def recover(self, **_kwargs):
                self.recover_calls += 1
                return SimpleNamespace(status="succeeded")

        coordinator = FailingCoordinator(["sorengame"])
        mgr, _ = self.manager(["sorengame"])
        mgr.coordinator = coordinator
        with patch.object(mgr.store.canonical, "load", return_value=({"phase": "draining"}, False)):
            with self.assertRaises(RetroCornerError):
                mgr._transition_to("sorengame", "robots")
        self.assertEqual(coordinator.recover_calls, 0)
        self.assertEqual(coordinator.calls, [("switch", "robots")])


class TestRetroCornerFailedRecovery(RetroCornerTestBase):
    def test_recover_failed_preserves_rotation_history_and_retries_same_game(self):
        from unittest.mock import patch

        current = ["sorengame"]
        mgr, coordinator = self.manager(current)
        mgr._target_reached = lambda state: True
        rotation = {
            "selection_history": [{"game": "robots", "selected_at": self.now_value.isoformat()}],
            "next_due_at": (self.now_value + timedelta(hours=4)).isoformat(),
            "last_result": {"result": "fire", "game": "robots"},
        }
        state = mgr._default_state()
        state.update(
            status="failed",
            game="robots",
            previous_game="sorengame",
            last_error="retro start failed: canonical stateの復旧が必要です (`docich recover`)",
            last_error_code="recovery_required",
            rotation=rotation,
        )
        mgr._write_state(state)
        with patch.object(
            mgr.store.canonical,
            "load",
            return_value=({"phase": "ready"}, False),
        ):
            result = mgr.recover_failed()

        self.assertEqual(result.status, "completed")
        self.assertEqual(
            coordinator.calls,
            [("switch", "robots"), ("switch", "sorengame")],
        )
        finished = mgr.status()
        self.assertEqual(finished["status"], "completed")
        self.assertEqual(finished["rotation"]["selection_history"], rotation["selection_history"])
        self.assertEqual(finished["rotation"]["last_result"]["result"], "recovery-retry")

    def test_recover_failed_keeps_boundary_wait_untouched(self):
        current = ["sorengame"]
        mgr, coordinator = self.manager(current)
        state = mgr._default_state()
        state.update(
            status="failed",
            game="robots",
            previous_game="sorengame",
            last_error="canonical stateの復旧が必要です",
            last_error_code="recovery_required",
        )
        mgr._write_state(state)
        mgr.store.canonical.load = lambda: ({"phase": "draining"}, False)

        result = mgr.recover_failed()

        self.assertEqual(result.status, "queued")
        self.assertIn("期限前のdrainingには触れません", result.detail)
        self.assertEqual(coordinator.calls, [])
        self.assertEqual(mgr.status()["status"], "failed")


class TestPrelaunchQuiesceFailureTerminalization(RetroCornerTestBase):
    """#1044: a manual start whose switch died in the boundary step.

    The switch kept the outgoing runtime and reported ``quiesce_failed``, so no
    canonical recovery is outstanding and no retry can succeed: replaying it
    would re-enter the same failed round boundary.  ``recover-failed`` therefore
    terminalizes the slot as ``interrupted`` — but only on the receipt's proof.
    """

    def _setup_quiesce_failed(self, *, error_code="quiesce_failed",
                              receipt_error="quiesce_failed",
                              canonical_patch=None, from_game="sorengame",
                              receipt_status="failed", keep_receipt=True):
        # Each sub-case starts from a clean state dir: a leaked canonical
        # phase or receipt would decide the outcome instead of the case.
        shutil.rmtree(self.g.state_dir, ignore_errors=True)
        mgr, coordinator = self.manager(["sorengame"])
        request_id = str(uuid.uuid4())
        state = mgr._default_state()
        state.update(
            status="failed",
            game="robots",
            previous_game="sorengame",
            rotation_request_id=request_id,
            switch_request_id=request_id,
            started_at=self.now_value.isoformat(),
            completed_at=self.now_value.isoformat(),
            last_error="Soren lifecycleが停止しました: cancelled",
            last_error_code=error_code,
        )
        mgr._write_state(state)
        canonical = mgr.store.initialize()
        canonical.update(phase="ready", next_generation=2, operation=None,
                         request_id=None, deadline_at=None,
                         active=self._runtime("sorengame", 1))
        mgr.store.canonical.save(canonical)
        accepted = mgr.store.accept_request(request_id, "switch", "robots")
        result = {
            "request_id": request_id, "operation": "switch", "status": "failed",
            "from_game": from_game, "to_game": "robots",
            "generation": accepted.generation, "error_code": receipt_error,
            "detail": "Soren lifecycleが停止しました: cancelled",
        }
        mgr.store.finish_request(request_id, receipt_status, result)
        # The switch restored the outgoing runtime and left no candidate
        # behind, exactly like the real quiesce failure it reproduces.
        canonical, _ = mgr.store.canonical.load()
        canonical.update(
            phase="ready", next_generation=max(accepted.generation + 1, 8),
            operation=None, request_id=None, deadline_at=None,
            active=self._runtime("sorengame", 1), candidate=None,
            previous=None, retiring=[], last_result=result,
        )
        if canonical_patch is not None:
            canonical_patch(canonical)
        mgr.store.canonical.save(canonical)
        if not keep_receipt:
            mgr.store.receipts.load = lambda _rid: None
        return mgr, coordinator, request_id

    @staticmethod
    def _runtime(game, generation):
        names = runtime_names(generation)
        return {
            "game": game, "adapter": "browser", "generation": generation,
            "runtime_id": f"g{generation}-abcdef", "lease_id": str(uuid.uuid4()),
            "game_window": names.game_window, "agent_window": names.agent_window,
            "adapter_session": names.adapter_session,
            "started_at": "2026-09-23T12:00:00Z",
        }

    def test_quiesce_failed_receipt_is_terminalized_as_interrupted(self):
        mgr, coordinator, _request_id = self._setup_quiesce_failed()

        result = mgr.recover_failed()

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.game, "robots")
        self.assertEqual(result.previous_game, "sorengame")
        self.assertIn("interrupted", result.detail)
        state = mgr._read_state()
        self.assertEqual(state["status"], "interrupted")
        self.assertEqual(state["end_reason"], "switch-terminal-before-corner-active")
        self.assertIsNone(state["last_error"])
        self.assertIsNone(state["last_error_code"])
        self.assertNotIn("switch_status", state)
        # the failed timestamp is the moment the slot really ended
        self.assertEqual(state["completed_at"], self.now_value.isoformat())
        # the switch is never replayed: that is the whole point of the path
        self.assertEqual(coordinator.calls, [])

    def test_busy_game_switch_writer_leaves_failed_slot_unchanged(self):
        mgr, coordinator, _request_id = self._setup_quiesce_failed()
        before = mgr._read_state()

        with mgr.store.lock(exclusive=True):
            result = mgr.recover_failed()

        self.assertEqual(result.status, "queued")
        self.assertIn("lock", result.detail)
        self.assertEqual(mgr._read_state(), before)
        self.assertEqual(coordinator.calls, [])

    def test_shared_store_lock_covers_canonical_check_through_corner_write(self):
        mgr, coordinator, _request_id = self._setup_quiesce_failed()
        original_write = mgr._write_state
        writer_blocked_at_commit = []

        def write_while_competing(state):
            with self.assertRaises(GameSwitchBusyError):
                with mgr.store.lock(exclusive=True):
                    pass
            writer_blocked_at_commit.append(True)
            original_write(state)

        with patch.object(mgr, "_write_state", side_effect=write_while_competing):
            result = mgr.recover_failed()

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(writer_blocked_at_commit, [True])
        self.assertEqual(mgr._read_state()["status"], "interrupted")
        self.assertEqual(coordinator.calls, [])

    def test_terminalized_slot_is_not_terminalized_twice(self):
        mgr, coordinator, _request_id = self._setup_quiesce_failed()
        mgr.recover_failed()
        completed_at = mgr._read_state()["completed_at"]

        result = mgr.recover_failed()

        self.assertEqual(result.status, "noop")
        self.assertEqual(result.detail, "failed-slot-not-recoverable")
        self.assertEqual(mgr._read_state()["completed_at"], completed_at)
        self.assertEqual(coordinator.calls, [])

    def test_live_draining_is_waited_out_instead_of_terminalized(self):
        def patch(canonical):
            canonical.update(phase="draining", operation="switch",
                             request_id=str(uuid.uuid4()),
                             deadline_at="2026-09-24T00:48:10Z")

        mgr, coordinator, _request_id = self._setup_quiesce_failed(
            canonical_patch=patch)

        result = mgr.recover_failed()

        self.assertEqual(result.status, "queued")
        self.assertEqual(mgr._read_state()["status"], "failed")
        self.assertEqual(coordinator.calls, [])

    def test_unfinished_switch_parts_keep_the_slot_failed(self):
        cases = {
            "candidate still in flight": (
                lambda c: c.update(phase="starting", operation="switch",
                                   request_id=str(uuid.uuid4()),
                                   candidate=self._runtime("robots", 2)),
                "queued"),
            "previous runtime retained": (
                lambda c: c.update(phase="rolling_back", operation="switch",
                                   request_id=str(uuid.uuid4()),
                                   previous=self._runtime("sorengame", 4)),
                "queued"),
            "retiring runtime pending": (
                lambda c: c.update(retiring=[self._runtime("nethack", 3)]),
                "failed"),
            "canonical recovery outstanding": (
                lambda c: c.update(phase="recovery_required"), "failed"),
        }
        for name, (patch, expected) in cases.items():
            with self.subTest(case=name):
                mgr, coordinator, _rid = self._setup_quiesce_failed(
                    canonical_patch=patch)
                result = mgr.recover_failed()
                self.assertEqual(result.status, expected)
                self.assertEqual(mgr._read_state()["status"], "failed")
                self.assertEqual(coordinator.calls, [])

    def test_canonical_owning_the_target_keeps_the_slot_failed(self):
        mgr, coordinator, _rid = self._setup_quiesce_failed()
        canonical, _missing = mgr.store.canonical.load()
        canonical["active"]["game"] = "robots"
        mgr.store.canonical.save(canonical)

        result = mgr.recover_failed()

        # The receipt says the switch failed, canonical says it owns the
        # target: that disagreement is never resolved by rewriting the corner.
        self.assertEqual(result.status, "failed")
        self.assertEqual(mgr._read_state()["status"], "failed")
        self.assertEqual(coordinator.calls, [])

    def test_only_the_exact_quiesce_failed_receipt_is_accepted(self):
        cases = {
            "receipt pruned": dict(keep_receipt=False),
            "receipt error code differs": dict(receipt_error="start_failed"),
            "receipt from_game differs": dict(from_game="nethack"),
            "receipt is not terminal failed": dict(receipt_status="rolled_back"),
        }
        for name, kwargs in cases.items():
            with self.subTest(case=name):
                mgr, coordinator, _rid = self._setup_quiesce_failed(**kwargs)
                result = mgr.recover_failed()
                self.assertEqual(result.status, "noop")
                self.assertEqual(mgr._read_state()["status"], "failed")
                self.assertEqual(coordinator.calls, [])

    def test_other_failed_error_codes_keep_the_existing_recovery_path(self):
        mgr, coordinator, _rid = self._setup_quiesce_failed(
            error_code="recovery_required", receipt_error="recovery_required")
        mgr._target_reached = lambda state: True

        result = mgr.recover_failed()

        # recovery_required still replays the same game, exactly as before
        self.assertEqual(result.status, "completed")
        self.assertEqual(coordinator.calls,
                         [("switch", "robots"), ("switch", "sorengame")])
        self.assertEqual(mgr.status()["status"], "completed")

    def test_missing_switch_request_id_keeps_the_slot_failed(self):
        mgr, coordinator, _rid = self._setup_quiesce_failed()
        state = mgr._read_state()
        state.pop("switch_request_id", None)
        mgr._write_state(state)

        result = mgr.recover_failed()

        self.assertEqual(result.status, "noop")
        self.assertEqual(mgr._read_state()["status"], "failed")
        self.assertEqual(coordinator.calls, [])

    def test_the_timer_never_replays_or_terminalizes_the_slot_on_its_own(self):
        # Only the owner-only operator step may settle this slot. An automatic
        # retry would re-enter the same failed round boundary, and an automatic
        # terminalization would clear a latch the operator still has to see.
        mgr, coordinator, _rid = self._setup_quiesce_failed()
        self.assertIsNone(mgr._retry_failed_tick(self.now_value))

        self.assertEqual(mgr._read_state()["status"], "failed")
        self.assertEqual(coordinator.calls, [])


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

    def test_restore_switch_is_kept_queued_until_a_later_tick(self):
        current = ["sorengame"]

        class QueueRestoreCoordinator(FakeCoordinator):
            def __init__(self, current):
                super().__init__(current)
                self.queue_restore_once = True

            def switch(self, game):
                self.calls.append(("switch", game))
                if game == "sorengame" and self.queue_restore_once:
                    self.queue_restore_once = False
                    return SimpleNamespace(
                        status="queued", error_code="queued", detail="queued"
                    )
                self.current[0] = game
                return SimpleNamespace(status="succeeded", error_code=None, detail=None)

        coordinator = QueueRestoreCoordinator(current)
        mgr = RetroCornerManager(
            self.g,
            config=self.cfg,
            coordinator=coordinator,
            now=lambda: self.now_value,
            sleep=lambda seconds: None,
            active_game_reader=lambda: current[0],
            ensure_runtime=lambda: None,
        )

        first = mgr.start()

        self.assertEqual(first.status, "queued")
        self.assertEqual(mgr.status()["status"], "restoring")
        self.assertTrue(mgr.status()["switch_request_id"])

        second = mgr.tick()

        self.assertEqual(second.status, "completed")
        self.assertEqual(mgr.status()["status"], "completed")
        self.assertEqual(current[0], "sorengame")
        self.assertEqual(
            coordinator.calls,
            [("switch", "robots"), ("switch", "sorengame"), ("switch", "sorengame")],
        )

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
            "ExecStart=__DOCICH_ROOT__/bin/docich --config __DOCICH_ROOT__/config/docich.soren-live.toml corner-rotation tick",
            service,
        )
        self.assertIn("TimeoutStartSec=infinity", service)
        self.assertIn("OnActiveSec=30s", timer)
        self.assertIn("OnBootSec=30s", timer)
        self.assertIn("OnUnitActiveSec=60s", timer)
        self.assertIn("AccuracySec=5s", timer)
        self.assertNotIn("OnCalendar=", timer)
        self.assertIn("Persistent=false", timer)

    def test_canonical_service_and_timer_contract(self):
        root = Path(__file__).resolve().parents[1]
        service = (root / "scripts/systemd/docich-corner-rotation.service").read_text(encoding="utf-8")
        timer = (root / "scripts/systemd/docich-corner-rotation.timer").read_text(encoding="utf-8")
        self.assertIn(
            "ExecStart=__DOCICH_ROOT__/bin/docich --config __DOCICH_ROOT__/config/docich.soren-live.toml corner-rotation tick",
            service,
        )
        self.assertIn("TimeoutStartSec=infinity", service)
        self.assertNotIn("[Install]", service)
        self.assertIn("OnActiveSec=30s", timer)
        self.assertIn("OnBootSec=30s", timer)
        self.assertIn("OnUnitActiveSec=60s", timer)
        self.assertIn("AccuracySec=5s", timer)
        self.assertIn("Persistent=false", timer)
        self.assertIn("Unit=docich-corner-rotation.service", timer)
        self.assertIn("WantedBy=timers.target", timer)
        self.assertNotIn("docich-retro-corner", timer)


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

    def test_start_posts_intro_then_end_result_summary(self):
        chats = []
        mgr, _ = self._manager_with_chat([None], chats.append)
        self.assertEqual(mgr.start().status, "completed")
        self.assertEqual(len(chats), 2)
        self.assertIn("レトロゲームコーナー", chats[0])
        self.assertIn("Robotsをお送りします", chats[0])
        self.assertIn("比較に使える同じゲームの過去戦略記録が見つからない", chats[0])
        self.assertTrue(mgr.status().get("announced"))
        # 終了時は結果まとめが続けて投稿される (ゲーム名＋終了理由＋時間)。
        self.assertIn("Robotsは", chats[1])
        self.assertIn("予定時間になりましたので終了しました", chats[1])
        self.assertIn("約1分間お楽しみいただきました", chats[1])
        self.assertTrue(mgr.status().get("end_announced"))

    def test_strategy_announcement_uses_game_scoped_history_and_all_value_types(self):
        from docich.resolver import strategy_history_dir, strategy_path
        from docich.resolver.robots import DEFAULT_STRATEGY

        previous = dict(DEFAULT_STRATEGY)
        previous["w_collision"] = 10.0
        previous["teleport_when_trapped"] = True
        current = dict(previous)
        current["w_collision"] = 20.0
        current["teleport_when_trapped"] = False

        current_path = strategy_path(self.g.state_dir, "robots")
        current_path.parent.mkdir(parents=True, exist_ok=True)
        current_path.write_text(json.dumps(current), encoding="utf-8")

        history_root = current_path.parent / "history"
        history_root.mkdir(parents=True, exist_ok=True)
        # Legacy unscoped and another game's newer entry must not be treated
        # as the previous Robots strategy.
        (history_root / "20990101.json").write_text(json.dumps(current), encoding="utf-8")
        other_game = strategy_history_dir(self.g.state_dir, "bastet")
        other_game.mkdir(parents=True, exist_ok=True)
        (other_game / "20990102.json").write_text(json.dumps(current), encoding="utf-8")
        own_history = strategy_history_dir(self.g.state_dir, "robots")
        own_history.mkdir(parents=True, exist_ok=True)
        (own_history / "20260101.json").write_text(json.dumps(previous), encoding="utf-8")

        message = describe_strategy_change(self.g.state_dir, "robots")
        self.assertIn("w_collision 10.0→20.0", message)
        self.assertIn("teleport_when_trapped 有効→無効", message)
        self.assertNotIn("前回と同じ戦略", message)

    def test_unscoped_legacy_history_never_claims_same_strategy(self):
        from docich.resolver import strategy_path
        from docich.resolver.robots import DEFAULT_STRATEGY

        current_path = strategy_path(self.g.state_dir, "robots")
        current_path.parent.mkdir(parents=True, exist_ok=True)
        current_path.write_text(json.dumps(DEFAULT_STRATEGY), encoding="utf-8")
        legacy_history = current_path.parent / "history"
        legacy_history.mkdir(parents=True, exist_ok=True)
        (legacy_history / "20990101.json").write_text(
            json.dumps(DEFAULT_STRATEGY), encoding="utf-8"
        )

        message = describe_strategy_change(self.g.state_dir, "robots")
        self.assertIn("比較に使える同じゲームの過去戦略記録が見つからない", message)
        self.assertNotIn("前回と同じ戦略", message)

    def test_same_strategy_message_requires_matching_game_history(self):
        from docich.resolver import strategy_history_dir, strategy_path
        from docich.resolver.robots import DEFAULT_STRATEGY

        current_path = strategy_path(self.g.state_dir, "robots")
        current_path.parent.mkdir(parents=True, exist_ok=True)
        current_path.write_text(json.dumps(DEFAULT_STRATEGY), encoding="utf-8")
        own_history = strategy_history_dir(self.g.state_dir, "robots")
        own_history.mkdir(parents=True, exist_ok=True)
        (own_history / "20260101.json").write_text(
            json.dumps(DEFAULT_STRATEGY), encoding="utf-8"
        )

        self.assertEqual(
            describe_strategy_change(self.g.state_dir, "robots"),
            "前回と同じ戦略でお送りします。",
        )

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
        self.assertEqual(len(chats), 2)
        mgr._locked_announce_again = None
        with mgr._locked():
            state = mgr._read_state()
            mgr._announce_start_locked(state)
            mgr._announce_end_result_locked(state, self.now_value)
        self.assertEqual(len(chats), 2)

    def test_end_announce_failure_does_not_fail_corner(self):
        chats = []

        def flaky(text):
            if chats:  # 2投稿目の結果まとめだけ失敗させる
                raise RuntimeError("chat down")
            chats.append(text)

        mgr, _ = self._manager_with_chat([None], flaky)
        self.assertEqual(mgr.start().status, "completed")
        state = mgr.status()
        self.assertEqual(state.get("status"), "completed")
        self.assertTrue(state.get("end_announced"))
        self.assertIn("end_announce_error", state)

    def test_end_result_labels_cover_every_reason(self):
        mgr, _ = self._manager_with_chat([None], lambda text: None)
        cases = {
            None: "予定時間になりましたので終了しました",
            "game_over": "ゲームオーバーになりました",
            "screen_stalled": "画面停止で終了しました",
            "manual_saved_stop": "セーブして終了しました",
            "manual_forced_stop": "セーブ失敗で強制終了しました",
            # 起動前の切替失敗は予定終了や game over と区別する (#1044)。
            "switch-terminal-before-corner-active": "開始前の切り替えが失敗したため開始できませんでした",
            # 未知コードは内部語彙を出さず既定文面に落とす。
            "invented-reason": "終了しました",
        }
        for reason, clause in cases.items():
            state: dict = {"game": "robots"}
            if reason is not None:
                state["end_reason"] = reason
            text = mgr._end_result_text(state, self.now_value)
            self.assertTrue(text.startswith("Robotsは"), reason)
            self.assertIn(clause, text, reason)

    def test_hanjuku_end_result_uses_grounded_run_numbers(self):
        from docich.naming import runtime_directory

        mgr, _ = self._manager_with_chat([None], lambda text: None)
        identity = {
            "game": "hanjuku-hero",
            "runtime_id": "g1-abcdef",
            "generation": 1,
            "lease_id": "lease-hanjuku-test",
        }
        runtime_dir = runtime_directory(self.g.state_dir, identity["runtime_id"])
        runtime_dir.mkdir(parents=True, exist_ok=True)
        rows = [
            {"event": "decision", "decision": "month_seen", "month": "2-7"},
            {"event": "decision", "decision": "order_launched"},
            {"event": "decision", "decision": "order_launched_unconfirmed"},
            {"event": "decision", "decision": "discharge_general"},
            {"event": "decision", "decision": "castle_owned_observed",
             "resulting_event": "captured:ジョンリギ"},
            {"event": "decision", "chapter": 3},
        ]
        (runtime_dir / "hanjuku_decisions.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
            encoding="utf-8",
        )
        (runtime_dir / "hanjuku_run.json").write_text(
            json.dumps({**identity, "schema": 1, "battles_finished": 7}),
            encoding="utf-8",
        )
        state: dict = {
            "game": "hanjuku-hero",
            "bot_identity": identity,
            "end_reason": "manual_forced_stop",
        }
        # Progress remains grounded in the decision log/run state and the
        # closing still reflects the actual corner end reason.
        result = mgr._end_result_text(state, self.now_value)
        self.assertIn("第3章まで", result)
        self.assertIn("2年7月まで", result)
        self.assertIn("出撃1回", result)
        self.assertIn("戦闘7回", result)
        self.assertIn("1城の獲得記録", result)
        self.assertIn("将軍の解雇1回", result)
        self.assertTrue(result.endswith("セーブ失敗による強制終了で、今回の挑戦はここまでです。"))
        state["end_reason"] = "game_over"
        self.assertTrue(
            mgr._end_result_text(state, self.now_value).endswith(
                "タイトル画面への復帰を確認し、今回の挑戦はここまでです。"
            )
        )
        # 開始前の切替失敗では、開始していないのに挑戦は終わったと
        # 却没有できない (#1044)。
        state["end_reason"] = "switch-terminal-before-corner-active"
        self.assertTrue(
            mgr._end_result_text(state, self.now_value).endswith(
                "開始前の切り替えが失敗したため、今日は挑戦できませんでした。"
            )
        )
        # 記録が読めない場合は generic 文面 (ゲーム名＋理由) へフォールバック。
        fallback = mgr._end_result_text(
            {"game": "hanjuku-hero", "end_reason": "game_over"}, self.now_value
        )
        self.assertIn("タイトル画面への復帰を確認しました", fallback)


class TestHanjukuTerminalDelivery(RetroCornerTestBase):
    def test_corner_waits_after_bounded_outbox_retries_until_receipt_is_committed(self):
        from contextlib import nullcontext
        import time
        from docich import hanjuku_narration
        from docich.naming import runtime_directory

        identity = {
            "game": "hanjuku-hero", "runtime_id": "g17-a1b2c3",
            "generation": 17, "lease_id": "lease-terminal-retry",
        }
        mgr, _ = self.manager(["hanjuku-hero"], sleep=lambda _seconds: None)
        mgr.store.canonical.load = lambda: ({"active": identity}, 0)
        mgr._rotation_stop_result = lambda: None
        mgr._locked = lambda: nullcontext()
        mgr._read_state = lambda: {"status": "active", "game": "hanjuku-hero"}
        mgr._write_state = lambda _state: None
        finished = []
        mgr._finish_locked = lambda state, completed_at: (
            finished.append(state) or SimpleNamespace(status="completed")
        )

        runtime_dir = runtime_directory(self.g.state_dir, identity["runtime_id"])
        runtime_dir.mkdir(parents=True, exist_ok=True)
        recap = {
            "schema": 1, "seq": 1, "at": time.time(), "key": "game_over_recap",
            "text": "タイトル画面への復帰までの記録です。", "terminal_recap": True,
            **identity,
        }
        (runtime_dir / "hanjuku_commentary.jsonl").write_text(
            json.dumps(recap, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        delivered = []
        outcomes = iter(["delivery_failed", "delivery_failed", "delivery_failed", "enqueued"])

        def deliver(_g, _runtime_dir, item, _speaker, _enqueue, _max_age, *, terminal=False):
            self.assertTrue(terminal)
            delivered.append((item["text"], hanjuku_narration._terminal_delivery_key(identity)))
            return next(outcomes)

        observed = []

        class Adapter:
            def observe(self):
                observed.append(True)
                return SimpleNamespace(meta={"hanjuku": {"terminal_reason": "game_over"}})

        game = SimpleNamespace(raw={"hanjuku": {"narration": {"enabled": True}}})
        with (
            patch("docich.adapters.make_adapter", return_value=Adapter()),
            patch("docich.agent.fence.shared_section", side_effect=lambda _path, operation: operation()),
            patch("docich.retro_corner.load_game", return_value=game),
            patch.object(hanjuku_narration, "_deliver", side_effect=deliver),
            patch.object(hanjuku_narration.time, "sleep", lambda _seconds: None),
            patch("docich.hanjuku_predictions.tick", return_value=None),
            patch("docich.hanjuku_run.event", return_value=None),
            patch("docich.hanjuku_run.runtime_identity", return_value=identity),
            patch("docich.hanjuku_run.terminal", return_value={
                "terminal_reason": "game_over", "terminal_evidence": "title-return",
                "generation": identity["generation"],
            }),
            patch("docich.hanjuku_chart_review.review", return_value={}),
        ):
            result = mgr._wait_hanjuku({"bot_identity": identity, "bot_runtime_id": identity["runtime_id"]})

        self.assertEqual(result.status, "completed")
        self.assertEqual(len(observed), 2)
        self.assertEqual(len(delivered), 4)  # 3 failed attempts, then the same run is retried
        self.assertEqual(len({key for _text, key in delivered}), 1)
        self.assertEqual(len(finished), 1)


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

    def test_finish_persists_completed_before_spawning_improve(self):
        spawned = []
        mgr = self._manager_with_spawn([None], spawned, 'agent-a')
        observed = []

        def observe_state(argv, log_path):
            observed.append(mgr._read_state())

        mgr._spawn = observe_state
        self.assertEqual(mgr.start().status, 'completed')
        self.assertEqual(observed[0].get('status'), 'completed')

    def test_no_spawn_without_agents(self):
        spawned = []
        mgr = self._manager_with_spawn([None], spawned, '')
        self.assertEqual(mgr.start().status, 'completed')
        self.assertEqual(spawned, [])

    def test_nethack_records_unsupported_improvement_without_spawning(self):
        spawned = []
        mgr = self._manager_with_spawn([None], spawned, 'agent-a')
        state = {"date": "2026-09-06", "game": "nethack"}
        mgr._spawn_improve_once(state)
        self.assertEqual(spawned, [])
        self.assertEqual(
            state["improve_job"],
            {"spawned": False, "reason": "nethack-improvement-not-supported"},
        )


class TestRetroCornerImproveSpawnEnv(RetroCornerTestBase):
    def test_systemd_spawn_is_independent_and_bounded(self):
        from unittest.mock import patch

        calls = []

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return SimpleNamespace(returncode=0, stderr="")

        mgr, _ = self.manager(["sorengame"])
        # Reproduce the tick service environment that caused the production
        # failures: no user-bus variables at all (#947).
        with patch.object(sys, "platform", "linux"), \
             patch.dict(os.environ, {"INVOCATION_ID": "parent-corner"}, clear=True), \
             patch("subprocess.run", side_effect=fake_run), \
             patch("subprocess.Popen", side_effect=AssertionError("unsafe parent cgroup")):
            mgr._default_spawn_improve_proc(
                ["python3", "-m", "docich", "retro-corner", "improve-once"],
                self.root / "run" / "x.log",
            )

        assert len(calls) == 1
        argv, kwargs = calls[0]
        assert argv[:4] == ["systemd-run", "--user", "--quiet", "--collect"]
        assert any(item.startswith("--unit=docich-retro-improve-") for item in argv)
        assert "--property=Type=exec" in argv
        assert "--property=RuntimeMaxSec=3600" in argv
        assert "--property=TimeoutStopSec=30" in argv
        assert "--setenv=DOCICH_ALLOW_REAL_AI=1" in argv
        assert f"--setenv=PYTHONPATH={self.g.repo_root / 'src'}" in argv
        assert f"--property=StandardOutput=append:{(self.root / 'run' / 'x.log').resolve()}" in argv
        assert f"--property=StandardError=append:{(self.root / 'run' / 'x.log').resolve()}" in argv
        assert kwargs["check"] is False
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["timeout"] == 30
        # A timer-launched tick unit does not reliably carry the user-bus
        # environment; the submission must supply it itself (#947).
        assert kwargs["env"]["XDG_RUNTIME_DIR"] == f"/run/user/{os.getuid()}"

    def test_failed_systemd_submission_is_recorded_without_fallback(self):
        from dataclasses import replace
        from unittest.mock import patch

        mgr, _ = self.manager(["sorengame"])
        mgr.config = replace(mgr.config, improve_agents="test-agent")
        state = {"date": "2026-09-06"}

        def failed_run(argv, **kwargs):
            return SimpleNamespace(
                returncode=1,
                stderr="Failed to start transient service unit: Unit is masked\n",
            )

        with patch.object(sys, "platform", "linux"), \
             patch.dict(os.environ, {"INVOCATION_ID": "parent-corner"}, clear=False), \
             patch("subprocess.run", side_effect=failed_run), \
             patch("subprocess.Popen", side_effect=AssertionError("unsafe parent cgroup")):
            mgr._spawn_improve_once(state)

        assert state["improve_job"]["spawned"] is False
        error = state["improve_job"]["error"]
        # The durable record must carry the exit status and systemd's message,
        # not the argv that previously filled the 240-char limit.
        assert "rc=1" in error
        assert "Unit is masked" in error
        assert "systemd-run" not in error

    def test_systemd_submission_timeout_is_recorded(self):
        from dataclasses import replace
        from unittest.mock import patch

        mgr, _ = self.manager(["sorengame"])
        mgr.config = replace(mgr.config, improve_agents="test-agent")
        state = {"date": "2026-09-06"}

        def timed_out(argv, **kwargs):
            raise subprocess.TimeoutExpired(cmd="systemd-run", timeout=30)

        with patch.object(sys, "platform", "linux"), \
             patch.dict(os.environ, {"INVOCATION_ID": "parent-corner"}, clear=False), \
             patch("subprocess.run", side_effect=timed_out), \
             patch("subprocess.Popen", side_effect=AssertionError("unsafe parent cgroup")):
            mgr._spawn_improve_once(state)

        assert state["improve_job"]["spawned"] is False
        assert "タイムアウト" in state["improve_job"]["error"]

    def test_non_systemd_spawn_passes_real_ai_consent_to_child(self):
        import subprocess
        from unittest.mock import patch

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
            with patch.object(sys, "platform", "darwin"), \
                 patch.dict(os.environ, {"INVOCATION_ID": ""}, clear=False):
                mgr._default_spawn_improve_proc(['echo', 'hi'], self.root / 'run' / 'x.log')
        finally:
            sp_module.Popen = old
        assert len(calls) == 1
        _, kwargs = calls[0]
        assert kwargs['env']['DOCICH_ALLOW_REAL_AI'] == '1'
        assert kwargs['start_new_session'] is True


class TestRetroCornerImproveWindow(RetroCornerTestBase):
    def test_spawn_passes_the_confirmed_corner_window(self):
        from dataclasses import replace
        from unittest.mock import patch

        mgr, _ = self.manager(["sorengame"])
        mgr.config = replace(mgr.config, improve_agents="test-agent")
        state = {
            "date": "2026-09-06",
            "game": "moon-buggy",
            "started_at": "2026-09-06T12:00:00+09:00",
            "ends_at": "2026-09-06T12:20:00+09:00",
            "rotation_request_id": "12345678-1234-5678-1234-567812345678",
        }
        captured = []

        def fake_run(argv, **kwargs):
            captured.append(argv)
            return SimpleNamespace(returncode=0, stderr="")

        with patch.object(sys, "platform", "linux"), \
             patch.dict(os.environ, {"INVOCATION_ID": "parent-corner"}, clear=True), \
             patch("subprocess.run", side_effect=fake_run), \
             patch("subprocess.Popen", side_effect=AssertionError("unsafe parent cgroup")):
            mgr._spawn_improve_once(state)

        assert state["improve_job"]["spawned"] is True
        argv = captured[0]
        start = datetime.fromisoformat(state["started_at"]).timestamp()
        end = datetime.fromisoformat(state["ends_at"]).timestamp()
        assert argv[argv.index("--started-at") + 1] == f"{start:.6f}"
        assert argv[argv.index("--ends-at") + 1] == f"{end:.6f}"
        assert argv[argv.index("--request-id") + 1] == state["rotation_request_id"]

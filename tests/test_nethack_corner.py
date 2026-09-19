import json
import sys
import tempfile
import unittest
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
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
from docich.nethack_run import NethackRunError  # noqa: E402
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
run_boundary = false
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
                run_boundary=False,
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
            'run_boundary = "yes"\n',
            "stall_timeout_minutes = 0\n",
            "poll_interval_s = 0.1\n",
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

    def test_tick_outside_window_does_not_join_program_queue(self):
        self.now_value = self.now_value.replace(hour=21)
        current = [None]
        mgr, coordinator = self.manager(current)
        with patch(
            "docich.nethack_corner.program_slot",
            side_effect=AssertionError("outside-window tick must not queue"),
        ):
            result = mgr.tick()
        self.assertEqual(result.status, "noop")
        self.assertEqual(result.detail, "outside-window")
        self.assertEqual(coordinator.calls, [])

    def test_tick_rechecks_schedule_after_program_slot_wait(self):
        current = [None]
        mgr, coordinator = self.manager(current)

        @contextmanager
        def delayed_slot(*args, **kwargs):
            self.now_value = self.now_value.replace(hour=23)
            yield self.g.state_dir

        with patch("docich.nethack_corner.program_slot", delayed_slot):
            result = mgr.tick()
        self.assertEqual(result.status, "noop")
        self.assertEqual(result.detail, "outside-window")
        self.assertEqual(coordinator.calls, [])

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


class TestNethackRunBoundary(NethackCornerTestBase):
    def _boundary_manager(self, screens, *, stall_minutes=10):
        cfg = replace(
            self.cfg,
            run_boundary=True,
            stall_timeout_minutes=stall_minutes,
            poll_interval_s=1.0,
        )
        current = ["robots"]
        clock = {"now": self.now_value}
        index = {"i": 0}

        def screen():
            value = screens[min(index["i"], len(screens) - 1)]
            index["i"] += 1
            return value

        def sleep(seconds):
            clock["now"] = clock["now"] + timedelta(seconds=seconds)

        mgr, coordinator = self.manager(
            current,
            config=cfg,
            now=lambda: clock["now"],
            sleep=sleep,
            runtime_screen=screen,
        )
        return mgr, coordinator, clock

    def test_terminal_screen_ends_the_run_boundary(self):
        screens = ["a map", "a map", "You die...\nDo you want your possessions identified?"]
        mgr, coordinator, _clock = self._boundary_manager(screens)
        result = mgr.start()
        self.assertEqual(result.status, "completed")
        self.assertEqual(
            coordinator.calls,
            [("switch", "nethack"), ("switch", "robots")],
        )
        self.assertEqual(mgr.status().get("finish_reason"), "terminal")

    def test_unchanged_screen_ends_after_stall_timeout(self):
        mgr, coordinator, clock = self._boundary_manager(["frozen screen"], stall_minutes=10)
        result = mgr.start()
        self.assertEqual(result.status, "completed")
        self.assertEqual(mgr.status().get("finish_reason"), "stalled")
        self.assertGreaterEqual(
            (clock["now"] - self.now_value).total_seconds(),
            10 * 60,
        )
        self.assertEqual(
            coordinator.calls,
            [("switch", "nethack"), ("switch", "robots")],
        )

    def test_terminal_markers_are_specific(self):
        from docich.nethack_corner import _is_terminal_screen

        for text in (
            "You die...",
            "You have died.",
            "Do you want your possessions identified? [ynq]",
            "You ascend to the status of Demigod",
        ):
            with self.subTest(text=text):
                self.assertTrue(_is_terminal_screen(text))
        self.assertFalse(_is_terminal_screen("Dlvl:3 HP:18(18) --More--"))

    def test_run_boundary_is_the_default(self):
        root = Path(__file__).resolve().parents[1]
        default_g = config.load_global(root, root / "config/docich.toml")
        self.assertTrue(load_nethack_corner_config(default_g).run_boundary)


class TestNethackStrandedRunReconcile(NethackCornerTestBase):
    def test_unreachable_leftover_run_is_reconciled_then_started(self):
        current = [None]
        mgr, _ = self.manager(current)
        store = Mock()
        store.prepare_start.side_effect = [
            NethackRunError("active runなのにNetHack runtime/saveがありません"),
            {"kind": "new", "run_id": "r1", "expected_expedition": 2},
        ]
        store.record_finished.return_value = {"status": "ended_unknown"}
        mgr._run_store = store

        probe = mgr._prepare_start_with_reconcile(None)

        self.assertEqual(probe["kind"], "new")
        self.assertEqual(store.prepare_start.call_count, 2)
        store.record_finished.assert_called_once()
        self.assertEqual(store.record_finished.call_args.kwargs["nethack_still_active"], False)

    def test_reconcile_is_skipped_while_nethack_is_still_active(self):
        current = ["nethack"]
        mgr, _ = self.manager(current)
        store = Mock()
        store.prepare_start.side_effect = NethackRunError("boom")
        mgr._run_store = store

        with self.assertRaises(NethackRunError):
            mgr._prepare_start_with_reconcile("nethack")
        store.record_finished.assert_not_called()

    def test_reconcile_failure_stays_fail_closed(self):
        current = [None]
        mgr, _ = self.manager(current)
        store = Mock()
        store.prepare_start.side_effect = NethackRunError("active runなのにNetHack runtime/saveがありません")
        store.record_finished.side_effect = NethackRunError("終了対象のcurrent NetHack runがありません")
        mgr._run_store = store

        with self.assertRaises(NethackRunError):
            mgr._prepare_start_with_reconcile(None)


# Production window layout observed on generation 244 (2026-09-19): the NetHack
# TTY is in the birth window, while game-g<N> only runs the xterm that mirrors
# the session for video, so its pane text is xterm's own startup noise.
XTERM_PANE = (
    "> Warning: Could not resolve keysym XF86Sos\n"
    "> Warning: Could not resolve keysym XF86NavChart\n"
)
MAP_PANE = (
    "------------\n|......@...|\n------------\n"
    "[Docich the Stripling ] St:17 Dx:12\nDlvl:1 $:0 HP:16(16) Pw:2(2) AC:6 Xp:1\n"
)


class WatchTmux:
    """Fake tmux exposing both windows so a capture of the wrong one is visible."""

    def __init__(self, session, *, ownership=None, windows=None, panes=None):
        self.session = session
        from docich.tmux import TmuxOwnership

        self.ownership = ownership or TmuxOwnership("g7-abcdef", 7, "game")
        self.windows = ["nethack-console", "game-g7", "agent-g7"] if windows is None else windows
        self.panes = panes or {
            "docich-game-g7:nethack-console": MAP_PANE,
            "docich-game-g7:game-g7": XTERM_PANE,
            "docich-game-g7:agent-g7": "[agent] nethack: 1\n",
        }
        self.captured: list[str] = []

    def read_window_ownership(self, target):
        return self.ownership

    def list_windows(self):
        return list(self.windows)

    def capture_pane_checked(self, target):
        self.captured.append(target)
        return self.panes[target]


class TestDefaultRuntimeScreen(NethackCornerTestBase):
    """The stall/terminal watcher must read the game TTY, not the xterm window."""

    def _state(self, *, phase="ready", game="nethack"):
        return {
            "phase": phase,
            "active": {
                "game": game,
                "adapter": "cli",
                "generation": 7,
                "runtime_id": "g7-abcdef",
                "lease_id": None,
                "game_window": "game-g7",
                "agent_window": "agent-g7",
                "adapter_session": "docich-game-g7",
                "started_at": "2026-09-19T07:00:00Z",
            },
        }

    def _manager(self, state, tmux):
        coordinator = FakeCoordinator(["nethack"])
        coordinator.store = SimpleNamespace(
            canonical=SimpleNamespace(load=lambda: (state, False))
        )
        mgr, _ = self.manager(["nethack"], coordinator=coordinator)
        patcher = patch("docich.tmux.Tmux", lambda session: tmux)
        patcher.start()
        self.addCleanup(patcher.stop)
        return mgr

    def test_capture_comes_from_the_birth_window_not_the_xterm(self):
        tmux = WatchTmux("docich-game-g7")
        mgr = self._manager(self._state(), tmux)

        text = mgr._default_runtime_screen()

        self.assertEqual(text, MAP_PANE)
        self.assertNotIn("keysym", text)
        self.assertEqual(tmux.captured, ["docich-game-g7:nethack-console"])

    def test_a_played_game_is_not_mistaken_for_a_stall(self):
        # The production symptom: the xterm pane never changes, so watching it
        # made every corner end at exactly stall_timeout after it started.
        tmux = WatchTmux("docich-game-g7")
        mgr = self._manager(self._state(), tmux)
        first = mgr._default_runtime_screen()
        tmux.panes["docich-game-g7:nethack-console"] = MAP_PANE.replace("|......@...|", "|.......@..|")
        self.assertNotEqual(mgr._default_runtime_screen(), first)

    def test_a_death_screen_in_the_birth_window_is_seen(self):
        from docich.nethack_corner import _is_terminal_screen

        tmux = WatchTmux("docich-game-g7")
        tmux.panes["docich-game-g7:nethack-console"] = "You die...\nDo you want your possessions identified?"
        mgr = self._manager(self._state(), tmux)
        self.assertTrue(_is_terminal_screen(mgr._default_runtime_screen()))
        self.assertFalse(_is_terminal_screen(XTERM_PANE))

    def test_ownership_mismatch_captures_nothing(self):
        from docich.tmux import TmuxOwnership

        tmux = WatchTmux("docich-game-g7", ownership=TmuxOwnership("g6-abcdef", 6, "game"))
        mgr = self._manager(self._state(), tmux)
        self.assertIsNone(mgr._default_runtime_screen())
        self.assertEqual(tmux.captured, [])

    def test_ambiguous_or_torn_window_listing_captures_nothing(self):
        for windows in (
            ["game-g7", "agent-g7"],                                   # no birth window
            ["game-g7", "agent-g7", "nethack-console", "stray"],       # ambiguous
            [],                                                        # torn listing
            ["nethack-console"],                                       # presentation gone
        ):
            with self.subTest(windows=windows):
                tmux = WatchTmux("docich-game-g7", windows=windows)
                mgr = self._manager(self._state(), tmux)
                self.assertIsNone(mgr._default_runtime_screen())
                self.assertEqual(tmux.captured, [])

    def test_uncommitted_or_foreign_runtime_captures_nothing(self):
        for state in (
            self._state(phase="draining"),
            self._state(phase="idle"),
            self._state(game="sorengame"),
        ):
            with self.subTest(state=state["phase"] + "/" + state["active"]["game"]):
                tmux = WatchTmux("docich-game-g7")
                mgr = self._manager(state, tmux)
                self.assertIsNone(mgr._default_runtime_screen())
                self.assertEqual(tmux.captured, [])

    def test_a_tmux_failure_is_no_progress_not_a_crash(self):
        class Broken(WatchTmux):
            def list_windows(self):
                raise RuntimeError("tmux gone")

        mgr = self._manager(self._state(), Broken("docich-game-g7"))
        self.assertIsNone(mgr._default_runtime_screen())


class TestStreamCategoryFollowsTheCorner(NethackCornerTestBase):
    """While NetHack is on, the stream should say NetHack -- and stop saying it after."""

    def _manager(self, current, **kwargs):
        announced: list[str] = []
        mgr, coordinator = self.manager(current, stream_game=announced.append, **kwargs)
        return mgr, coordinator, announced

    def test_the_corner_announces_nethack_and_then_the_game_it_restores(self):
        mgr, coordinator, announced = self._manager(["sorengame"])

        result = mgr.start()

        self.assertEqual(result.status, "completed")
        self.assertEqual(
            coordinator.calls, [("switch", "nethack"), ("switch", "sorengame")]
        )
        # ...and the announcements follow the same order, so the category is
        # never left on NetHack after the corner ended.
        self.assertEqual(announced, ["nethack", "sorengame"])

    def test_announcement_happens_only_after_the_switch_succeeded(self):
        order: list[str] = []
        current = ["sorengame"]

        class RecordingCoordinator(FakeCoordinator):
            def switch(self, game):
                order.append(f"switch:{game}")
                return super().switch(game)

        mgr, _, _announced = self._manager(
            current, coordinator=RecordingCoordinator(current)
        )
        mgr._stream_game = lambda game: order.append(f"announce:{game}")

        mgr.start()

        self.assertEqual(
            order,
            ["switch:nethack", "announce:nethack", "switch:sorengame", "announce:sorengame"],
        )

    def test_a_failing_switch_is_never_announced(self):
        current = ["sorengame"]

        class FailingCoordinator(FakeCoordinator):
            def switch(self, game):
                self.calls.append(("switch", game))
                return SimpleNamespace(status="failed", error_code="timeout", detail="boom")

        mgr, _, announced = self._manager(current, coordinator=FailingCoordinator(current))

        with self.assertRaises(RetroCornerError):
            mgr.start()

        self.assertEqual(announced, [])

    def test_a_broken_announcement_never_breaks_the_corner(self):
        def broken(game):
            raise RuntimeError("twitch unreachable")

        mgr, coordinator = self.manager(["sorengame"], stream_game=broken)

        result = mgr.start()

        self.assertEqual(result.status, "completed")
        self.assertEqual(
            coordinator.calls, [("switch", "nethack"), ("switch", "sorengame")]
        )

    def test_nothing_is_announced_when_no_switch_was_needed(self):
        # NetHack was already the canonical game: no switch, so no announcement.
        mgr, coordinator, announced = self._manager(["nethack"])
        mgr._transition_to("nethack", "nethack")
        self.assertEqual(announced, [])
        self.assertEqual(coordinator.calls, [])

    def test_an_empty_game_name_is_ignored(self):
        mgr, _coordinator, announced = self._manager(["sorengame"])
        for value in (None, "", 7):
            mgr._announce_stream_game(value)
        self.assertEqual(announced, [])


if __name__ == "__main__":
    unittest.main()

"""A. 日次レトロコーナー: 決定的ランダム時刻・ゲーム別試合数検知のテスト。

既存の「1日1ゲーム・start_hourちょうど」挙動は tests/test_retro_corner.py
が守る。このファイルは daily_each_game / randomize_start / target_matches
の新しい振る舞いのみを検証する。
"""
import json
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from test_retro_corner import FakeCoordinator, RetroCornerTestBase  # noqa: E402

from docich.retro_corner import (  # noqa: E402
    RetroCornerConfig,
    RetroCornerError,
    RetroCornerManager,
    load_retro_corner_config,
    scheduled_start,
    select_game,
)


class _G:
    def __init__(self, state_dir, config_path=None):
        self.state_dir = state_dir
        self.config_path = config_path


def _write_config(root: Path, body: str) -> None:
    (root / "config" / "games").mkdir(parents=True, exist_ok=True)
    (root / "config" / "docich.toml").write_text(
        '[paths]\nstate_dir = "run"\ngames_dir = "config/games"\n'
        "[retro_corner]\n" + body,
        encoding="utf-8",
    )
    (root / "config" / "games" / "robots.toml").write_text(
        '[game]\nname = "robots"\ntitle = "Robots"\nadapter = "cli"\n'
        '[agent]\nenabled = true\nbrain = "resolver"\n',
        encoding="utf-8",
    )


class TestScheduledStart(unittest.TestCase):
    def test_same_day_and_game_is_stable_across_restarts(self):
        cfg = RetroCornerConfig(
            enabled=True, randomize_start=True, start_hour=19,
            start_window_minutes=30, games=["ninvaders"],
        )
        day = date(2026, 9, 18)
        first = scheduled_start(cfg, "ninvaders", day)
        self.assertEqual(first, scheduled_start(cfg, "ninvaders", day))
        self.assertEqual(first.date(), day)
        self.assertEqual(first.hour, 19)

    def test_offset_stays_inside_window_and_differs_by_game_or_day(self):
        cfg = RetroCornerConfig(
            enabled=True, randomize_start=True, start_hour=19,
            start_window_minutes=30, games=["ninvaders", "nsnake"],
        )
        self.assertNotEqual(
            scheduled_start(cfg, "ninvaders", date(2026, 9, 18)),
            scheduled_start(cfg, "nsnake", date(2026, 9, 18)),
        )
        self.assertNotEqual(
            scheduled_start(cfg, "ninvaders", date(2026, 9, 18)),
            scheduled_start(cfg, "ninvaders", date(2026, 9, 19)),
        )
        for day in (date(2026, 9, 18), date(2026, 9, 19)):
            for game in ("ninvaders", "nsnake"):
                start = datetime.combine(day, datetime.min.time()).replace(
                    hour=19, tzinfo=ZoneInfo(cfg.timezone))
                offset = (scheduled_start(cfg, game, day) - start).total_seconds()
                self.assertGreaterEqual(offset, 0)
                self.assertLess(offset, 30 * 60)

    def test_disabled_randomize_starts_exactly_at_start_hour(self):
        cfg = RetroCornerConfig(enabled=True, start_hour=19, games=["ninvaders"])
        self.assertEqual(
            scheduled_start(cfg, "ninvaders", date(2026, 9, 18)),
            datetime(2026, 9, 18, 19, 0, tzinfo=ZoneInfo(cfg.timezone)),
        )

    def test_window_is_clamped_before_midnight(self):
        cfg = RetroCornerConfig(
            enabled=True, randomize_start=True, start_hour=23,
            start_window_minutes=90, games=["ninvaders"],
        )
        start = scheduled_start(cfg, "ninvaders", date(2026, 9, 18))
        self.assertLess(start, datetime(2026, 9, 19, 0, 0, tzinfo=ZoneInfo(cfg.timezone)))


class TestConfigValidation(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_new_fields_load_with_defaults(self):
        _write_config(self.root, 'games = ["robots"]\n')
        cfg = load_retro_corner_config(_G(self.root / "run", self.root / "config" / "docich.toml"))
        self.assertIs(cfg.daily_each_game, False)
        self.assertIs(cfg.randomize_start, False)
        self.assertEqual(cfg.start_window_minutes, 30)
        self.assertEqual(cfg.target_matches, 3)

    def test_invalid_types_are_rejected(self):
        for body in (
            'daily_each_game = "yes"\ngames = ["robots"]\n',
            "randomize_start = 1\ngames = [\"robots\"]\n",
            "target_matches = 0\ngames = [\"robots\"]\n",
            "start_window_minutes = 0\ngames = [\"robots\"]\n",
            "start_window_minutes = 1441\ngames = [\"robots\"]\n",
        ):
            with self.subTest(body=body):
                _write_config(self.root, body)
                g = _G(self.root / "run", self.root / "config" / "docich.toml")
                with self.assertRaises(RetroCornerError):
                    load_retro_corner_config(g)


class TestSelectGameDeterminism(unittest.TestCase):
    def test_selection_is_deterministic_per_local_date(self):
        games = ["ninvaders", "nsnake", "gnurobots"]
        day = date(2026, 9, 18)
        expected = games[day.toordinal() % len(games)]
        self.assertEqual(select_game(games, day), expected)
        self.assertEqual(select_game(games, day), expected)


class TestDailyEachGameLifecycle(RetroCornerTestBase):
    """daily_each_game=True の起動・早期終了・時間フォールバック。

    sleep が呼ばれたぶんだけ偽時計を進める (TestProgramBoundary と同じ方式)。
    """

    def _daily_cfg(self):
        from dataclasses import replace

        return replace(
            self.cfg,
            daily_each_game=True,
            randomize_start=True,
            start_window_minutes=30,
            target_matches=3,
            duration_minutes=60,
        )

    def _manager(self, current, sleeps, prepared=None):
        coordinator = FakeCoordinator(current)
        test = self

        def advance(seconds):
            sleeps.append(seconds)
            test.now_value += timedelta(seconds=seconds)

        mgr = RetroCornerManager(
            self.g,
            config=self._daily_cfg(),
            coordinator=coordinator,
            now=lambda: test.now_value,
            sleep=advance,
            active_game_reader=lambda: current[0],
            ensure_runtime=(lambda: prepared.append(True)) if prepared is not None else (lambda: None),
            chat=lambda text: None,
        )
        return mgr, coordinator

    def _write_scores(self, count: int = 3) -> None:
        log = self.root / "run" / "scores" / "robots.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        ts = self.now_value.timestamp()
        log.write_text(
            "\n".join(
                json.dumps({"ts": ts + i, "game": "robots", "score": 5})
                for i in range(count)
            )
            + "\n",
            encoding="utf-8",
        )

    def test_before_window_is_noop_and_does_not_prepare_runtime(self):
        sleeps = []
        prepared = []
        mgr, coordinator = self._manager(["sorengame"], sleeps, prepared=prepared)
        result = mgr.tick()
        self.assertEqual(result.status, "noop")
        self.assertEqual(result.detail, "start-window-not-due")
        self.assertEqual(prepared, [])
        self.assertEqual(coordinator.calls, [])

    def test_early_finish_after_three_matches_before_ends_at(self):
        sleeps = []
        mgr, coordinator = self._manager(["sorengame"], sleeps)
        self.now_value += timedelta(minutes=25)  # random start window 内
        self._write_scores(3)
        result = mgr.tick()
        self.assertEqual(result.status, "completed")
        state = mgr.status()
        self.assertLess(state["completed_at"], state["ends_at"])
        self.assertEqual(state["daily_attempts"]["2026-09-06"], ["robots"])
        self.assertEqual(
            coordinator.calls, [("switch", "robots"), ("switch", "sorengame")]
        )

    def test_finishes_at_ends_at_when_matches_never_reach_target(self):
        sleeps = []
        mgr, coordinator = self._manager(["sorengame"], sleeps)
        self.now_value += timedelta(minutes=25)
        result = mgr.tick()
        self.assertEqual(result.status, "completed")
        state = mgr.status()
        # 時間フォールバック: 3試合来なくても ends_at で終了する
        self.assertEqual(state["completed_at"], state["ends_at"])
        self.assertEqual(sleeps[:-1], [5.0] * (len(sleeps) - 1))
        self.assertEqual(sleeps[-1], 0.0)
        self.assertEqual(
            coordinator.calls, [("switch", "robots"), ("switch", "sorengame")]
        )

    def test_second_tick_same_day_is_noop(self):
        sleeps = []
        mgr, coordinator = self._manager(["sorengame"], sleeps)
        self.now_value += timedelta(minutes=25)
        self._write_scores(3)
        self.assertEqual(mgr.tick().status, "completed")
        self.now_value += timedelta(minutes=1)
        second = mgr.tick()
        self.assertEqual(second.status, "noop")
        self.assertEqual(second.detail, "already-ran-today")
        self.assertEqual(
            coordinator.calls, [("switch", "robots"), ("switch", "sorengame")]
        )

    def test_restarted_process_keeps_same_random_start(self):
        from docich.retro_corner import scheduled_start as fn

        self.now_value += timedelta(minutes=25)
        sleeps = []
        mgr, _ = self._manager(["sorengame"], sleeps)
        self._write_scores(3)
        self.assertEqual(mgr.tick().status, "completed")
        # 決定性: 再起動後も同一 (日付, ゲーム) なら同一開始時刻
        self.assertEqual(
            scheduled_start(self._daily_cfg(), "robots", date(2026, 9, 6)),
            scheduled_start(self._daily_cfg(), "robots", date(2026, 9, 6)),
        )
        self.assertNotEqual(
            scheduled_start(self._daily_cfg(), "robots", date(2026, 9, 6)),
            scheduled_start(self._daily_cfg(), "robots", date(2026, 9, 7)),
        )

    def test_missing_scorelog_falls_back_to_ends_at(self):
        sleeps = []
        mgr, coordinator = self._manager(["sorengame"], sleeps)
        self.now_value += timedelta(minutes=25)
        result = mgr.tick()
        self.assertEqual(result.status, "completed")
        state = mgr.status()
        self.assertEqual(state["completed_at"], state["ends_at"])
        self.assertEqual(
            coordinator.calls, [("switch", "robots"), ("switch", "sorengame")]
        )


class TestRandomizedSingleGame(RetroCornerTestBase):
    """daily_each_game=False + randomize_start=True (後方互換モード)。"""

    def _random_cfg(self):
        from dataclasses import replace

        return replace(
            self.cfg,
            randomize_start=True,
            start_window_minutes=30,
        )

    def test_tick_before_random_offset_is_noop_then_runs(self):
        from docich.retro_corner import scheduled_start

        test = self
        test.current_holder = ["sorengame"]
        sleeps = []
        coordinator = FakeCoordinator(test.current_holder)

        def advance(seconds):
            sleeps.append(seconds)
            test.now_value += timedelta(seconds=seconds)

        mgr = RetroCornerManager(
            self.g,
            config=self._random_cfg(),
            coordinator=coordinator,
            now=lambda: test.now_value,
            sleep=advance,
            active_game_reader=lambda: test.current_holder[0],
            ensure_runtime=lambda: None,
            chat=lambda text: None,
        )
        start = scheduled_start(self._random_cfg(), "robots", self.now_value.date())
        # start_hour(20:00)ちょうどではまだ開始しない
        self.assertGreater(start, self.now_value)
        result = mgr.tick()
        self.assertEqual(result.status, "noop")
        self.assertEqual(result.detail, "start-window-not-due")
        self.assertEqual(coordinator.calls, [])
        # 開始時刻以降に進める
        test.now_value = start + timedelta(seconds=1)
        result = mgr.tick()
        self.assertEqual(result.status, "completed")
        self.assertEqual(
            coordinator.calls, [("switch", "robots"), ("switch", "sorengame")]
        )


if __name__ == "__main__":
    unittest.main()

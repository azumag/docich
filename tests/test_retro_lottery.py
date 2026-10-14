"""毎時抽選モード: 固定枠を持たず、毎時1回抽選し、当たれば遊べるゲームを選んで遊んで戻る。

日次モード (既定) の挙動は tests/test_retro_corner.py / test_retro_daily_schedule.py が守る。
ここでは mode="lottery" だけを検証する。
"""
import fcntl
import json
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from test_retro_corner import FakeCoordinator, RetroCornerTestBase  # noqa: E402

from docich.retro_corner import (  # noqa: E402
    RetroCornerError,
    RetroCornerManager,
    load_retro_corner_config,
)
from docich.trading.soren_output import resolve_soren_root  # noqa: E402

TZ = ZoneInfo("Asia/Tokyo")


class FakeRng:
    """roll を順に返し、choice は常に先頭を選ぶ (呼び出しを記録する)。"""

    def __init__(self, *rolls):
        self.rolls = list(rolls)
        self.calls = 0
        self.candidates = []

    def random(self):
        self.calls += 1
        return self.rolls.pop(0) if self.rolls else 0.99

    def choice(self, seq):
        self.candidates.append(list(seq))
        return seq[0]


class LotteryBase(RetroCornerTestBase):
    GAMES = ("ninvaders", "nsnake", "bastet")

    def setUp(self):
        super().setUp()
        self.now_value = datetime(2026, 9, 19, 14, 5, tzinfo=TZ)
        for name in self.GAMES:
            self.write_game(name)
        self.cfg = replace(
            self.cfg, enabled=True, mode="lottery", games=list(self.GAMES),
            duration_minutes=20, target_matches=3, lottery_probability=0.3,
            lottery_minute=5, lottery_wait_minutes=10, lottery_avoid_repeat=True,
        )
        self.current = ["sorengame"]
        self.texts = []

    def write_game(self, name, body="", adapter="cli", requires=None):
        extra = "" if requires is None else f'[retro_corner]\nrequires = {json.dumps(requires)}\n'
        (self.root / "config" / "games" / f"{name}.toml").write_text(
            f'[game]\nname = "{name}"\ntitle = "{name}"\nadapter = "{adapter}"\n'
            "[agent]\nenabled = false\n[corner]\nself_play = true\n" + extra + body,
            encoding="utf-8",
        )

    def make(self, rng, cfg=None, reader=None, sleep=None):
        coordinator = FakeCoordinator(self.current)
        test = self

        def advance(seconds):
            test.now_value += timedelta(seconds=seconds)
            if sleep:
                sleep(seconds)

        mgr = RetroCornerManager(
            self.g, config=cfg or self.cfg, coordinator=coordinator,
            now=lambda: test.now_value, sleep=advance,
            active_game_reader=reader or (lambda: test.current[0]),
            ensure_runtime=lambda: None, chat=self.texts.append, rng=rng,
        )
        return mgr, coordinator

    def program_root(self):
        root = resolve_soren_root(self.g) / "tmp" / "state"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def lottery(self, mgr):
        return mgr.status().get("lottery")


class TestLotteryConfig(LotteryBase):
    def load(self, body):
        (self.root / "config" / "docich.toml").write_text(
            '[paths]\nstate_dir = "run"\ngames_dir = "config/games"\n[retro_corner]\n' + body,
            encoding="utf-8",
        )
        return load_retro_corner_config(__import__("docich.config", fromlist=["x"]).load_global(self.root))

    def test_defaults_keep_the_daily_schedule(self):
        cfg = self.load('games = ["ninvaders"]\n')
        self.assertEqual(cfg.mode, "daily")
        self.assertEqual((cfg.lottery_probability, cfg.lottery_minute, cfg.lottery_wait_minutes), (0.3, 5, 10))
        self.assertIs(cfg.lottery_avoid_repeat, True)

    def test_lottery_fields_load(self):
        cfg = self.load(
            'mode = "lottery"\nlottery_probability = 0.5\nlottery_minute = 10\n'
            'lottery_wait_minutes = 3\nlottery_avoid_repeat = false\nduration_minutes = 20\n'
            'games = ["ninvaders"]\n'
        )
        self.assertEqual(
            (cfg.mode, cfg.lottery_probability, cfg.lottery_minute, cfg.lottery_wait_minutes,
             cfg.lottery_avoid_repeat),
            ("lottery", 0.5, 10, 3, False),
        )

    def test_invalid_values_are_rejected(self):
        for body in (
            'mode = "weekly"\ngames = ["ninvaders"]\n',
            'lottery_probability = 0\ngames = ["ninvaders"]\n',
            'lottery_probability = 1.5\ngames = ["ninvaders"]\n',
            'lottery_probability = true\ngames = ["ninvaders"]\n',
            'lottery_probability = "half"\ngames = ["ninvaders"]\n',
            'lottery_minute = 51\ngames = ["ninvaders"]\n',
            'lottery_minute = -1\ngames = ["ninvaders"]\n',
            'lottery_wait_minutes = 31\ngames = ["ninvaders"]\n',
            'lottery_avoid_repeat = "yes"\ngames = ["ninvaders"]\n',
        ):
            with self.subTest(body=body), self.assertRaises(RetroCornerError):
                self.load(body)

    def test_a_lottery_corner_must_end_before_the_next_hour(self):
        # 抽選が :30 で上限30分だと次の正時に食い込み、毎正時に始まる固定枠を塞ぐ。
        with self.assertRaises(RetroCornerError):
            self.load('mode = "lottery"\nlottery_minute = 30\nlottery_wait_minutes = 0\n'
                      'duration_minutes = 30\ngames = ["ninvaders"]\n')
        # 境界待ち (最大 lottery_wait_minutes) を含めて数える: 5 + 30 + 25 = 60 は不可、5 + 10 + 25 = 40 は可。
        with self.assertRaises(RetroCornerError):
            self.load('mode = "lottery"\nlottery_minute = 5\nlottery_wait_minutes = 30\n'
                      'duration_minutes = 25\ngames = ["ninvaders"]\n')
        ok = self.load('mode = "lottery"\nlottery_minute = 30\nlottery_wait_minutes = 0\n'
                       'duration_minutes = 25\ngames = ["ninvaders"]\n')
        self.assertEqual(ok.mode, "lottery")
        self.assertEqual(self.load('mode = "lottery"\nduration_minutes = 25\ngames = ["ninvaders"]\n').mode, "lottery")
        # 日次モードには制約を課さない (既存の 19:00/30分・60分などを壊さない)。
        self.assertEqual(self.load('duration_minutes = 60\ngames = ["ninvaders"]\n').mode, "daily")


class TestDrawWindow(LotteryBase):
    def test_only_the_first_minutes_after_lottery_minute_draw(self):
        rng = FakeRng(0.99)
        mgr, coordinator = self.make(rng)
        for minute in (0, 4, 15, 40):
            self.now_value = datetime(2026, 9, 19, 14, minute, tzinfo=TZ)
            self.assertEqual(mgr.tick().detail, "outside-window", minute)
        self.assertEqual((rng.calls, coordinator.calls), (0, []))
        self.assertIsNone(self.lottery(mgr))
        self.now_value = datetime(2026, 9, 19, 14, 14, tzinfo=TZ)
        self.assertEqual(mgr.tick().detail, "lottery-miss")
        self.assertEqual(rng.calls, 1)

    def test_disabled_never_draws(self):
        rng = FakeRng(0.0)
        mgr, coordinator = self.make(rng, cfg=replace(self.cfg, enabled=False))
        self.assertEqual(mgr.tick().detail, "disabled")
        self.assertEqual((rng.calls, coordinator.calls), (0, []))

    def test_ignores_the_daily_start_hour(self):
        # 固定枠 (start_hour=20) が無い: 14時台でも抽選される。
        rng = FakeRng(0.99)
        mgr, _ = self.make(rng, cfg=replace(self.cfg, start_hour=20))
        self.assertEqual(self.now_value.hour, 14)
        self.assertEqual(mgr.tick().detail, "lottery-miss")


class TestDrawOncePerHour(LotteryBase):
    def test_a_miss_is_recorded_and_not_redrawn_within_the_hour(self):
        rng = FakeRng(0.9, 0.0)
        mgr, coordinator = self.make(rng)
        self.assertEqual(mgr.tick().detail, "lottery-miss")
        record = self.lottery(mgr)
        self.assertEqual((record["slot"], record["result"], record["roll"]), ("2026-09-19T14", "miss", 0.9))
        # 再 tick (別プロセス相当) でも、後続の roll が当たりでも引き直さない。
        self.now_value += timedelta(minutes=1)
        self.assertEqual(mgr.tick().detail, "already-drawn")
        self.assertEqual(rng.calls, 1)
        self.assertEqual(coordinator.calls, [])

    def test_a_new_hour_draws_again(self):
        rng = FakeRng(0.9, 0.9)
        mgr, _ = self.make(rng)
        mgr.tick()
        self.now_value = datetime(2026, 9, 19, 15, 6, tzinfo=TZ)
        self.assertEqual(mgr.tick().detail, "lottery-miss")
        self.assertEqual((rng.calls, self.lottery(mgr)["slot"]), (2, "2026-09-19T15"))

    def test_the_draw_survives_a_restart(self):
        rng = FakeRng(0.9, 0.0)
        first, _ = self.make(rng)
        first.tick()
        second, coordinator = self.make(rng)  # 新しいプロセス相当
        self.assertEqual(second.tick().detail, "already-drawn")
        self.assertEqual(coordinator.calls, [])


class TestFireAndReturn(LotteryBase):
    def test_a_hit_plays_a_random_playable_game_then_restores_the_previous_one(self):
        rng = FakeRng(0.1)
        mgr, coordinator = self.make(rng)
        result = mgr.tick()
        self.assertEqual(result.status, "completed")
        game = result.game
        self.assertIn(game, self.GAMES)
        self.assertEqual(coordinator.calls, [("switch", game), ("switch", "sorengame")])
        self.assertEqual(self.current, ["sorengame"])  # もとに戻る
        state = mgr.status()
        self.assertEqual((state["status"], state["game"], state["previous_game"]), ("completed", game, "sorengame"))
        self.assertEqual(state["target_matches"], 3)
        self.assertEqual(state["lottery"]["result"], "fire")
        self.assertEqual(state["lottery"]["game"], game)
        # 上限時間 (duration_minutes=20) で終わる: 試合が記録されなければ時間で終了する。
        self.assertEqual(self.now_value, datetime(2026, 9, 19, 14, 25, tzinfo=TZ))

    def test_it_stops_early_once_the_target_matches_are_recorded(self):
        scores = Path(self.g.state_dir) / "scores"
        scores.mkdir(parents=True, exist_ok=True)
        test = self
        written = []

        def record_a_match_each_minute(seconds):
            elapsed = (test.now_value - datetime(2026, 9, 19, 14, 5, tzinfo=TZ)).total_seconds()
            while len(written) < int(elapsed // 60):
                written.append(1)
                with (scores / "ninvaders.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(
                        {"ts": test.now_value.timestamp(), "game": "ninvaders", "score": 100}) + "\n")

        mgr, coordinator = self.make(FakeRng(0.1), sleep=record_a_match_each_minute)
        result = mgr.tick()
        self.assertEqual((result.status, result.game), ("completed", "ninvaders"))
        self.assertEqual(coordinator.calls, [("switch", "ninvaders"), ("switch", "sorengame")])
        # 3試合を記録した約3分後に終了 (20分の上限より十分早い)。
        self.assertLess(self.now_value, datetime(2026, 9, 19, 14, 12, tzinfo=TZ))

    def test_announcement_says_this_time_not_today(self):
        mgr, _ = self.make(FakeRng(0.1))
        mgr.tick()
        self.assertTrue(self.texts[0].startswith("レトロゲームコーナーです。今回は"), self.texts)

    def test_nothing_is_played_while_a_corner_is_already_active(self):
        rng = FakeRng(0.1)
        mgr, coordinator = self.make(rng)
        mgr._write_state({**mgr._default_state(), "status": "active", "game": "nsnake",
                          "previous_game": "sorengame", "date": "2026-09-19",
                          "started_at": self.now_value.isoformat(),
                          "ends_at": (self.now_value + timedelta(minutes=10)).isoformat()})
        self.current[0] = "nsnake"
        self.assertEqual(mgr.tick().detail, "already-active")
        self.assertEqual((rng.calls, coordinator.calls), (0, []))

    def test_a_stale_starting_state_does_not_block_the_lottery_forever(self):
        mgr, _ = self.make(FakeRng(0.9))
        old = (self.now_value - timedelta(minutes=30)).isoformat()
        mgr._write_state({**mgr._default_state(), "status": "starting", "game": "nsnake",
                          "started_at": old, "date": "2026-09-19"})
        self.assertEqual(mgr.tick().detail, "lottery-miss")
        fresh = (self.now_value - timedelta(minutes=1)).isoformat()
        mgr2, _ = self.make(FakeRng(0.9))
        mgr2._write_state({**mgr2._default_state(), "status": "starting", "game": "nsnake",
                           "started_at": fresh, "date": "2026-09-19"})
        self.assertEqual(mgr2.tick().detail, "already-starting")


class TestCancelWhenAnotherCornerIsBusy(LotteryBase):
    def assert_cancelled(self, reason_prefix, rng=None):
        rng = rng or FakeRng(0.0)
        mgr, coordinator = self.make(rng)
        result = mgr.tick()
        self.assertEqual(result.status, "noop")
        self.assertTrue(result.detail.startswith(f"lottery-cancelled:{reason_prefix}"), result.detail)
        self.assertEqual((rng.calls, coordinator.calls), (0, []), "抽選も切替も行わない")
        record = self.lottery(mgr)
        self.assertEqual(record["result"], "cancelled")
        self.assertTrue(record["reason"].startswith(reason_prefix))
        # その時の抽選は消化済み: 同じ時に後から空いても引き直さない。
        self.now_value += timedelta(minutes=1)
        self.assertEqual(mgr.tick().detail, "already-drawn")
        return mgr

    def test_program_lock_held_by_a_running_corner(self):
        root = self.program_root()
        with (root / "docich_program.lock").open("a") as held:
            fcntl.flock(held.fileno(), fcntl.LOCK_EX)
            self.assert_cancelled("program-locked")

    def test_registered_owner_is_starting_or_active(self):
        root = self.program_root()
        other = root / "paper_corner_state.json"
        other.write_text(json.dumps({"status": "active"}), encoding="utf-8")
        (root / "docich_program_active.json").write_text(
            json.dumps({"owner_state": str(other)}), encoding="utf-8")
        self.assert_cancelled("owner-busy")

    def test_another_corner_is_queued_for_the_slot(self):
        root = self.program_root()
        queue = root / "docich_program_queue"
        queue.mkdir(parents=True, exist_ok=True)
        (queue / "paper_corner_state.json").write_text(json.dumps(
            {"status": "waiting_turn", "requested_at": self.now_value.timestamp() - 60}), encoding="utf-8")
        self.assert_cancelled("queued:paper_corner_state")

    def test_a_stale_queue_entry_or_our_own_entry_does_not_block(self):
        root = self.program_root()
        queue = root / "docich_program_queue"
        queue.mkdir(parents=True, exist_ok=True)
        (queue / "paper_corner_state.json").write_text(json.dumps(
            {"status": "waiting", "requested_at": self.now_value.timestamp() - 5 * 3600}), encoding="utf-8")
        (queue / "retro_corner.json").write_text(json.dumps(
            {"status": "waiting_turn", "requested_at": self.now_value.timestamp() - 60}), encoding="utf-8")
        (queue / "soren91_corner_state.json").write_text(json.dumps(
            {"status": "done", "requested_at": self.now_value.timestamp() - 60}), encoding="utf-8")
        rng = FakeRng(0.9)
        mgr, _ = self.make(rng)
        self.assertEqual(mgr.tick().detail, "lottery-miss")
        self.assertEqual(rng.calls, 1)

    def test_game_switch_in_progress(self):
        def unstable():
            raise RetroCornerError("ゲーム切替が安定phaseではありません: 'switching'")

        rng = FakeRng(0.0)
        mgr, coordinator = self.make(rng, reader=unstable)
        result = mgr.tick()
        self.assertEqual(result.detail, "lottery-cancelled:game-switch-in-progress")
        self.assertEqual((rng.calls, coordinator.calls), (0, []))


class TestWhichGameIsPlayable(LotteryBase):
    def test_games_missing_a_required_executable_or_invalid_are_never_picked(self):
        self.write_game("ninvaders", requires=["/no/such/game/binary"])
        self.write_game("nsnake", requires=[sys.executable])  # 実在する実行ファイル
        self.write_game("bastet", adapter="browser")           # retro corner 対象外の種別
        rng = FakeRng(0.1)
        mgr, _ = self.make(rng)
        mgr.tick()
        self.assertEqual(rng.candidates, [["nsnake"]])

    def test_the_previous_game_is_skipped_unless_it_is_the_only_one(self):
        rng = FakeRng(0.1)
        mgr, _ = self.make(rng)
        mgr._write_state({**mgr._default_state(), "status": "completed", "game": "ninvaders",
                          "date": "2026-09-19"})
        mgr.tick()
        self.assertEqual(rng.candidates, [["nsnake", "bastet"]])

        self.now_value = datetime(2026, 9, 19, 16, 5, tzinfo=TZ)
        only = FakeRng(0.1)
        mgr2, _ = self.make(only, cfg=replace(self.cfg, games=["nsnake"]))
        mgr2._write_state({**mgr2._default_state(), "status": "completed", "game": "nsnake",
                           "date": "2026-09-19", "lottery": {"slot": "2026-09-19T15"}})
        mgr2.tick()
        self.assertEqual(only.candidates, [["nsnake"]])

    def test_avoid_repeat_can_be_turned_off(self):
        rng = FakeRng(0.1)
        mgr, _ = self.make(rng, cfg=replace(self.cfg, lottery_avoid_repeat=False))
        mgr._write_state({**mgr._default_state(), "status": "completed", "game": "ninvaders",
                          "date": "2026-09-19"})
        mgr.tick()
        self.assertEqual(rng.candidates, [["ninvaders", "nsnake", "bastet"]])

    def test_no_playable_game_cancels_the_hit(self):
        for name in self.GAMES:
            self.write_game(name, requires=["/no/such/game/binary"])
        rng = FakeRng(0.1)
        mgr, coordinator = self.make(rng)
        result = mgr.tick()
        self.assertEqual(result.detail, "lottery-cancelled:no-playable-game")
        self.assertEqual(coordinator.calls, [])
        self.assertEqual(self.lottery(mgr)["result"], "cancelled")


class TestThroughTheProductionProgramSlot(LotteryBase):
    """本番契約 (require_program_boundary=True): 抽選コーナーも program slot を通る。"""

    def setUp(self):
        super().setUp()
        self.cfg = replace(self.cfg, require_program_boundary=True)

    def prediction_in_flight(self):
        import os

        root = self.program_root()
        (root / "prediction_worker.pid").write_text(str(os.getpid()))
        (root / "current_prediction.json").write_text(json.dumps({"status": "ACTIVE"}), encoding="utf-8")
        return root

    def test_a_hit_runs_through_program_slot_and_releases_it(self):
        root = self.program_root()
        mgr, coordinator = self.make(FakeRng(0.1))
        result = mgr.tick()
        self.assertEqual(result.status, "completed")
        self.assertEqual(coordinator.calls[-1], ("switch", "sorengame"))
        queue = json.loads((root / "docich_program_queue" / "retro_corner.json").read_text())
        self.assertEqual(queue["status"], "done")
        # 終了後は枠が空き、他コーナー視点で busy ではない。
        from docich.corner_boundary import other_corner_busy

        self.assertIsNone(other_corner_busy(self.g, root / "paper_corner_state.json"))

    def test_it_waits_a_bounded_time_for_a_prediction_boundary(self):
        root = self.prediction_in_flight()
        test = self
        state = {"first": True}

        def complete_after_a_minute(seconds):
            if (test.now_value - datetime(2026, 9, 19, 14, 5, tzinfo=TZ)) >= timedelta(minutes=1) and state["first"]:
                state["first"] = False
                (root / "corner_boundary_prediction.json").write_text(
                    json.dumps({"completed_at": test.now_value.timestamp()}), encoding="utf-8")

        mgr, coordinator = self.make(FakeRng(0.1), sleep=complete_after_a_minute)
        self.assertEqual(mgr.tick().status, "completed")
        self.assertEqual(coordinator.calls[-1], ("switch", "sorengame"))

    def test_it_gives_up_if_the_boundary_never_comes(self):
        self.prediction_in_flight()
        mgr, coordinator = self.make(FakeRng(0.1))
        result = mgr.tick()
        self.assertEqual(result.detail, "lottery-cancelled:wait-expired")
        self.assertEqual(coordinator.calls, [])
        record = self.lottery(mgr)
        self.assertEqual((record["result"], record["reason"]), ("cancelled", "wait-expired"))
        # 10分待って諦めた (lottery_wait_minutes=10)。20分の corner 上限までは走っていない。
        self.assertLessEqual(self.now_value, datetime(2026, 9, 19, 14, 16, tzinfo=TZ))


if __name__ == "__main__":
    unittest.main()

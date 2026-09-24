"""B. 改善のゲーム別 dispatch テスト (gnurobots 以外が skipped にならない)。

実 tmux/実ゲームは使わない: bot_eval.run_bot_matches をモンキーパッチし、
env 経由の候補重み注入 (DOCICH_BRAIN_WEIGHTS) と margin gate を決定的に検証する。
"""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import docich.corner_improve as corner_improve  # noqa: E402
from docich.corner_improve import run_corner_improve  # noqa: E402


class _G:
    def __init__(self, state_dir):
        self.state_dir = state_dir


def _setup_completed(tmp_path: Path, game: str, scores: list[int]) -> Path:
    state_dir = tmp_path / "run"
    (state_dir / "scores").mkdir(parents=True, exist_ok=True)
    log = state_dir / "scores" / f"{game}.jsonl"
    base = 1789034400  # 2026-09-10T19:00:00+09:00
    log.write_text(
        "\n".join(
            json.dumps({"ts": str(base + i * 60), "game": game, "score": s, "source": "wrapper"})
            for i, s in enumerate(scores)
        )
        + "\n",
        encoding="utf-8",
    )
    (state_dir / "retro_corner.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "completed",
                "date": "2026-09-10",
                "game": game,
                "previous_game": "sorengame",
                "started_at": "2026-09-10T19:00:00+09:00",
                "ends_at": "2026-09-10T19:30:00+09:00",
                "completed_at": "2026-09-10T19:30:00+09:00",
            }
        ),
        encoding="utf-8",
    )
    return state_dir


class TestDispatch(unittest.TestCase):
    def test_unsupported_game_is_still_skipped(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = _setup_completed(Path(tmp), "robots", [10])
            result = run_corner_improve(_G(state_dir), game="robots", date_str="2026-09-10", agents="a")
            self.assertEqual(result["status"], "skipped")
            self.assertEqual(result["reason"], "unsupported-game:robots")

    def test_nsnake_and_ninvaders_are_not_unsupported(self):
        import tempfile

        for game in ("nsnake", "ninvaders"):
            with self.subTest(game=game):
                with tempfile.TemporaryDirectory() as tmp:
                    state_dir = _setup_completed(Path(tmp), game, [10, 20])
                    result = run_corner_improve(
                        _G(state_dir), game=game, date_str="2026-09-10", agents="a", dry_run=True
                    )
                    self.assertEqual(result["status"], "dry-run")

    def test_nsnake_uses_bot_eval_with_weights_env_and_promotes(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state_dir = _setup_completed(tmp_path, "nsnake", [10, 20])
            calls = []

            def fake_run(**kwargs):
                # 評価は注入された重みファイルの中身に依存する (env注入の実検証)。
                weights = json.loads(
                    Path(kwargs["env"]["DOCICH_BRAIN_WEIGHTS"]).read_text()
                )
                score = 100.0 if weights.get("min_free") == 6 else 10.0
                calls.append({"weights": weights, "score": score})
                return {"game": "nsnake",
                        "matches": [{"score": int(score), "turns": 5, "maxed": False}],
                        "mean_score": score}

            self.assertEqual(corner_improve.run_bot_matches.__name__, "run_bot_matches")
            corner_improve.run_bot_matches = fake_run
            # live hot-swap 先を tmp へ退避 (リポジトリの run/ を汚さない)。
            old_brain_dir = os.environ.get("DOCICH_BOT_BRAIN_DIR")
            os.environ["DOCICH_BOT_BRAIN_DIR"] = str(tmp_path / "live-brain")
            try:
                result = run_corner_improve(
                    _G(state_dir), game="nsnake", date_str="2026-09-10", agents="a",
                    llm=lambda prompt: '```json\n{"min_free": 6}\n```',
                    margin_pct=10.0,
                )
            finally:
                corner_improve.run_bot_matches = __import__(
                    "docich.resolver.bot_eval", fromlist=["run_bot_matches"]
                ).run_bot_matches
                if old_brain_dir is None:
                    os.environ.pop("DOCICH_BOT_BRAIN_DIR", None)
                else:
                    os.environ["DOCICH_BOT_BRAIN_DIR"] = old_brain_dir
            self.assertEqual(result["status"], "promoted", result)
            self.assertEqual(result["baseline_mean"], 10.0)
            self.assertEqual(result["candidate_mean"], 100.0)
            self.assertEqual(len(calls), 2)  # baseline + candidate
            self.assertEqual(calls[0]["weights"]["min_free"], 8)   # default baseline
            self.assertEqual(calls[1]["weights"]["min_free"], 6)   # candidate fed to brain
            strategy = json.loads(
                (state_dir / "resolver" / "nsnake_strategy.json").read_text(encoding="utf-8")
            )
            self.assertEqual(strategy["min_free"], 6)
            # live hot-swap: 昇格済み全文が tmp の live brain へ出る
            live = tmp_path / "live-brain" / "nsnake" / "weights.json"
            self.assertEqual(json.loads(live.read_text(encoding="utf-8")), strategy)

    def test_ninvaders_policy_uses_six_samples_and_skips_incomplete_incumbent(self):
        import tempfile
        from unittest.mock import patch
        from docich.ninvaders import arena, improve

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = _setup_completed(Path(tmp), "ninvaders", [30])
            calls = []

            def fake_eval(path, matches, **kwargs):
                calls.append(matches)
                items = [{"end": "title", "score": 0, "ticks": 1000,
                          "policy": {"timeouts": 0, "errors": 0}}]
                return {"summary": arena.summarize(items), "matches": items}

            with patch.object(improve._arena, "evaluate", side_effect=fake_eval):
                result = run_corner_improve(
                    _G(state_dir), game="ninvaders", date_str="2026-09-10", agents="a",
                    llm=lambda prompt: (_ for _ in ()).throw(AssertionError("LLM must not run")),
                )
            self.assertEqual(result["status"], "skipped")
            self.assertEqual(result["reason_code"], "policy-incomplete")
            self.assertEqual(calls, [6])

    def test_ninvaders_structural_candidate_promotes_to_live_policy_store(self):
        import tempfile
        from unittest.mock import patch
        from docich.ninvaders import arena, improve
        from docich.ninvaders.sandbox import policy_sha
        from docich.ninvaders.store import PolicyStore

        candidate = "# CHANGE: track target motion\ndef decide(obs, state):\n    return ['Space']\n"
        calls = []

        def fake_eval(path, matches, parallel=1, max_seconds=1):
            calls.append((matches, parallel, max_seconds))
            score = 7000 if "track target motion" in Path(path).read_text(encoding="utf-8") else 5000
            items = [{"end": "title", "score": score + i, "ticks": 900,
                      "cause": "invasion", "last_frames": ["Score: 0005000"],
                      "policy": {"timeouts": 0, "errors": 0}} for i in range(matches)]
            return {"summary": arena.summarize(items), "matches": items}

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = _setup_completed(Path(tmp), "ninvaders", [30])
            with patch.object(improve._arena, "evaluate", side_effect=fake_eval):
                result = run_corner_improve(
                    _G(state_dir), game="ninvaders", date_str="2026-09-10", agents="a",
                    llm=lambda prompt: f"```python\n{candidate}```",
                )
            self.assertEqual(result["status"], "promoted", result)
            self.assertEqual(result["reason_code"], "policy-promoted")
            self.assertEqual(result["phase"], "eval")
            self.assertEqual(calls, [(improve.CORNER_EVAL_MATCHES,
                                      improve.CORNER_EVAL_PARALLEL,
                                      improve.CORNER_EVAL_MAX_SECONDS)] * 2)
            store = PolicyStore(state_dir / "resolver" / "ninvaders")
            self.assertEqual(store.current()["sha"], policy_sha(candidate))

    def test_bounded_nsnake_accepts_scored_maxed_matches(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state_dir = _setup_completed(tmp_path, "nsnake", [30])
            old_brain_dir = os.environ.get("DOCICH_BOT_BRAIN_DIR")
            os.environ["DOCICH_BOT_BRAIN_DIR"] = str(tmp_path / "live-brain")

            def fake_run(**kwargs):
                weights = json.loads(Path(kwargs["env"]["DOCICH_BRAIN_WEIGHTS"]).read_text())
                score = 100 if weights.get("min_free") == 6 else 10
                return {
                    "game": "nsnake",
                    "matches": [{"score": score, "turns": 1500, "maxed": True}],
                    "mean_score": score,
                }

            corner_improve.run_bot_matches = fake_run
            try:
                result = run_corner_improve(
                    _G(state_dir), game="nsnake", date_str="2026-09-10", agents="a",
                    llm=lambda prompt: '{"min_free": 6}',
                )
            finally:
                corner_improve.run_bot_matches = __import__(
                    "docich.resolver.bot_eval", fromlist=["run_bot_matches"]
                ).run_bot_matches
                if old_brain_dir is None:
                    os.environ.pop("DOCICH_BOT_BRAIN_DIR", None)
                else:
                    os.environ["DOCICH_BOT_BRAIN_DIR"] = old_brain_dir
            self.assertEqual(result["status"], "promoted", result)
            self.assertEqual(result["baseline_played"], 1)
            self.assertEqual(result["candidate_played"], 1)

    def test_nsnake_boolean_weight_is_not_proposable(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = _setup_completed(Path(tmp), "nsnake", [10])
            corner_improve.run_bot_matches = lambda **kwargs: {
                "matches": [{"score": 1, "maxed": False}], "mean_score": 1.0}
            try:
                with self.assertRaises(corner_improve.CornerImproveError):
                    run_corner_improve(
                        _G(state_dir), game="nsnake", date_str="2026-09-10", agents="a",
                        llm=lambda prompt: '{"tail_passable": false}',
                    )
            finally:
                corner_improve.run_bot_matches = __import__(
                    "docich.resolver.bot_eval", fromlist=["run_bot_matches"]
                ).run_bot_matches

    def test_gnurobots_path_unchanged(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = _setup_completed(Path(tmp), "gnurobots", [10, 20])
            result = run_corner_improve(
                _G(state_dir), game="gnurobots", date_str="2026-09-10", agents="a", dry_run=True
            )
            self.assertEqual(result["status"], "dry-run")


class TestLiveBrainHotSwap(unittest.TestCase):
    """昇格時の live brain 重み hot-swap (次tickの load_weights() で反映)。"""

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.brain = Path(self._tmp.name) / "brain"
        self._old_dir = os.environ.get("DOCICH_BOT_BRAIN_DIR")
        os.environ["DOCICH_BOT_BRAIN_DIR"] = str(self.brain)

    def tearDown(self):
        if self._old_dir is None:
            os.environ.pop("DOCICH_BOT_BRAIN_DIR", None)
        else:
            os.environ["DOCICH_BOT_BRAIN_DIR"] = self._old_dir
        self._tmp.cleanup()

    def test_promote_writes_candidate_weights_to_live_brain(self):
        import tempfile

        for game, delta in (("nsnake", {"min_free": 6}),):
            with self.subTest(game=game):
                with tempfile.TemporaryDirectory() as tmp:
                    state_dir = _setup_completed(Path(tmp), game, [10, 20])

                    def fake_run(_game=game, _delta=delta, **kwargs):
                        # 評価は注入重みの中身に依存する (env注入の実検証)。
                        weights = json.loads(
                            Path(kwargs["env"]["DOCICH_BRAIN_WEIGHTS"]).read_text()
                        )
                        score = 100.0 if all(
                            weights.get(k) == v for k, v in _delta.items()
                        ) else 10.0
                        return {"game": _game,
                                "matches": [{"score": int(score), "maxed": False}],
                                "mean_score": score}

                    corner_improve.run_bot_matches = fake_run
                    try:
                        result = run_corner_improve(
                            _G(state_dir), game=game, date_str="2026-09-10", agents="a",
                            llm=lambda prompt, _d=delta: json.dumps(_d),
                            margin_pct=10.0,
                        )
                    finally:
                        corner_improve.run_bot_matches = __import__(
                            "docich.resolver.bot_eval", fromlist=["run_bot_matches"]
                        ).run_bot_matches
                    self.assertEqual(result["status"], "promoted", result)
                    live = self.brain / game / "weights.json"
                    self.assertTrue(live.is_file())
                    written = json.loads(live.read_text(encoding="utf-8"))
                    strategy = json.loads(
                        (state_dir / "resolver" / f"{game}_strategy.json").read_text(encoding="utf-8")
                    )
                    # 昇格済み戦略 (既定値マージ済み+delta) の全文が live へ出る
                    self.assertEqual(written, strategy)
                    for key, value in delta.items():
                        self.assertEqual(written[key], value)

    def test_kept_does_not_touch_live_weights(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = _setup_completed(Path(tmp), "nsnake", [10])
            corner_improve.run_bot_matches = lambda **kwargs: {
                "game": "nsnake",
                "matches": [{"score": 1, "maxed": False}],
                "mean_score": 1.0,
            }
            try:
                # baseline と同点の候補 -> kept (昇格しないので live は不変)
                result = run_corner_improve(
                    _G(state_dir), game="nsnake", date_str="2026-09-10", agents="a",
                    llm=lambda prompt: '{"min_free": 8}',
                    margin_pct=10.0,
                )
            finally:
                corner_improve.run_bot_matches = __import__(
                    "docich.resolver.bot_eval", fromlist=["run_bot_matches"]
                ).run_bot_matches
            self.assertEqual(result["status"], "kept")
            self.assertFalse((self.brain / "nsnake" / "weights.json").exists())

    def test_bastet_zero_weight_candidate_is_evaluated_and_promoted(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = _setup_completed(Path(tmp), "bastet", [0, 0])
            prompts = []
            evaluated = []

            def fake_run(**kwargs):
                weights = json.loads(
                    Path(kwargs["env"]["DOCICH_BRAIN_WEIGHTS"]).read_text()
                )
                evaluated.append(weights)
                score = 100.0 if weights["hard_drop"] == 0 else 10.0
                return {
                    "game": "bastet",
                    "matches": [{"score": int(score), "turns": 5, "maxed": False}],
                    "mean_score": score,
                }

            corner_improve.run_bot_matches = fake_run
            try:
                result = run_corner_improve(
                    _G(state_dir), game="bastet", date_str="2026-09-10", agents="a",
                    llm=lambda prompt: prompts.append(prompt) or '{"hard_drop": 0}',
                    margin_pct=10.0,
                )
            finally:
                corner_improve.run_bot_matches = __import__(
                    "docich.resolver.bot_eval", fromlist=["run_bot_matches"]
                ).run_bot_matches

            self.assertEqual(result["status"], "promoted", result)
            self.assertEqual([weights["hard_drop"] for weights in evaluated], [1.0, 0])
            self.assertIn("0.0 は有効な候補", prompts[0])
            self.assertIn("ソフトドロップ", prompts[0])
            live = self.brain / "bastet" / "weights.json"
            self.assertEqual(json.loads(live.read_text(encoding="utf-8"))["hard_drop"], 0)

    def test_moon_buggy_stages_lower_headless_candidate_without_live_promotion(self):
        import tempfile

        from docich import moon_buggy_ab

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = _setup_completed(Path(tmp), "moon-buggy", [12, 20])

            def evaluator(weights):
                return {
                    "mean_score": 100.0 if weights["laser_period"] == 7.0 else 10.0,
                    "played": 2,
                }

            result = run_corner_improve(
                _G(state_dir), game="moon-buggy", date_str="2026-09-10", agents="a",
                llm=lambda _prompt: '{"laser_period": 8.0}', evaluator=evaluator,
                margin_pct=50.0,
            )

            self.assertEqual(result["status"], "ab-staged", result)
            self.assertLess(result["candidate_mean"], result["baseline_mean"])
            experiment = moon_buggy_ab.read_experiment(state_dir)
            self.assertEqual(experiment["status"], "staged")
            self.assertEqual(experiment["candidate"], {"laser_period": 8.0})
            self.assertFalse((self.brain / "moon-buggy" / "weights.json").exists())

    def test_moon_buggy_promotes_only_after_candidate_wins_live_abba(self):
        import tempfile

        from docich import moon_buggy_ab

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = _setup_completed(Path(tmp), "moon-buggy", [12, 20])
            baseline = corner_improve._game_defaults("moon-buggy")
            candidate = {**baseline, "laser_period": 8.0}
            moon_buggy_ab.stage(
                state_dir, baseline, candidate, source_date="2026-09-10",
                headless_baseline_mean=100.0, headless_candidate_mean=10.0,
            )
            scorelog = state_dir / "scores" / "moon-buggy.jsonl"
            for score in [10, 100, 90, 20]:
                moon_buggy_ab.select_arm(state_dir)
                moon_buggy_ab.record_score(state_dir, scorelog, score)

            result = run_corner_improve(
                _G(state_dir), game="moon-buggy", date_str="2026-09-10", agents="a",
            )

            self.assertEqual(result["status"], "promoted", result)
            self.assertEqual(result["ab_winner"], "B")
            self.assertEqual(result["ab_baseline_mean"], 15.0)
            self.assertEqual(result["ab_candidate_mean"], 95.0)
            live = self.brain / "moon-buggy" / "weights.json"
            self.assertEqual(json.loads(live.read_text()), candidate)
            self.assertEqual(moon_buggy_ab.read_experiment(state_dir)["status"], "promoted")

    def test_moon_buggy_ab_does_not_overwrite_concurrent_strategy_change(self):
        import tempfile

        from docich import moon_buggy_ab

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = _setup_completed(Path(tmp), "moon-buggy", [12, 20])
            baseline = corner_improve._game_defaults("moon-buggy")
            candidate = {**baseline, "laser_period": 8.0}
            moon_buggy_ab.stage(
                state_dir, baseline, candidate, source_date="2026-09-10",
                headless_baseline_mean=100.0, headless_candidate_mean=10.0,
            )
            scorelog = state_dir / "scores" / "moon-buggy.jsonl"
            for score in [10, 100, 90, 20]:
                moon_buggy_ab.select_arm(state_dir)
                moon_buggy_ab.record_score(state_dir, scorelog, score)
            concurrent = {**baseline, "laser_period": 9.0}
            strategy = state_dir / "resolver" / "moon-buggy_strategy.json"
            strategy.parent.mkdir(parents=True, exist_ok=True)
            strategy.write_text(json.dumps(concurrent), encoding="utf-8")

            result = run_corner_improve(
                _G(state_dir), game="moon-buggy", date_str="2026-09-10", agents="a",
            )

            self.assertEqual(result["status"], "kept")
            self.assertEqual(result["reason_code"], "ab-baseline-changed")
            self.assertEqual(json.loads(strategy.read_text()), concurrent)
            self.assertFalse((self.brain / "moon-buggy" / "weights.json").exists())

    def test_gnurobots_promotion_does_not_write_bot_weights(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = _setup_completed(Path(tmp), "gnurobots", [10, 20])
            import docich.resolver.improve as improve

            improve.GNUROBOTS_RESOLVER = str(Path(tmp) / "resolver.scm")
            run_corner_improve(
                _G(state_dir), game="gnurobots", date_str="2026-09-10", agents="a",
                llm=lambda prompt: '```json\n{"flee_retries": 2.5}\n```',
                evaluator=lambda strat: {"mean_score": 100.0, "played": 1},
            )
            self.assertEqual(list(self.brain.glob("**/*")), [])


if __name__ == "__main__":
    unittest.main()

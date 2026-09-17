"""B. 改善のゲーム別 dispatch テスト (gnurobots 以外が skipped にならない)。

実 tmux/実ゲームは使わない: bot_eval.run_bot_matches をモンキーパッチし、
env 経由の候補重み注入 (DOCICH_BRAIN_WEIGHTS) と margin gate を決定的に検証する。
"""
import json
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

    def test_all_maxed_matches_fail_closed_and_keep(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            state_dir = _setup_completed(Path(tmp), "ninvaders", [30])

            def fake_run(**kwargs):
                return {"game": "ninvaders",
                        "matches": [{"score": 0, "turns": 3000, "maxed": True}],
                        "mean_score": None}

            corner_improve.run_bot_matches = fake_run
            try:
                result = run_corner_improve(
                    _G(state_dir), game="ninvaders", date_str="2026-09-10", agents="a",
                    llm=lambda prompt: '{"dodge_radius": 3}',
                )
            finally:
                corner_improve.run_bot_matches = __import__(
                    "docich.resolver.bot_eval", fromlist=["run_bot_matches"]
                ).run_bot_matches
            self.assertEqual(result["status"], "kept")
            self.assertFalse(result["promoted"])
            self.assertEqual(result["candidate_played"], 0)

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


if __name__ == "__main__":
    unittest.main()

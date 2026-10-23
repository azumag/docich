import importlib.util
import json
import tarfile
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "soren_strategy_performance_export.py"
SPEC = importlib.util.spec_from_file_location("soren_strategy_performance_export", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class SorenStrategyPerformanceExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "tmp/state").mkdir(parents=True)
        (self.root / "soren91/tmp/state").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, current, rolling):
        (self.root / "tmp/state/current_strategy_run.json").write_text(json.dumps(current))
        (self.root / "tmp/state/rolling_scores.json").write_text(json.dumps(rolling))

    def test_build_report_splits_fresh_current_sample_and_recent_strategies(self):
        h1 = "a" * 64
        h2 = "b" * 64
        self._write(
            {
                "hash": h1,
                "scores": [100, 200, 300, 400],
                "games_total": 14,
                "_seeded_score_count": 2,
                "_fresh_score_count": 2,
                "_recent_archives": [
                    "game_history/20261023_160000_score0300.jsonl",
                    "game_history/20261023_170000_score0400.jsonl",
                ],
                "max_types": [13, 14, 15, 16],
                "progress": [
                    {"raw": 10, "turns": 80, "t": 13, "r": 0, "v": 0, "d": 0},
                    {"raw": 20, "turns": 90, "t": 14, "r": 0, "v": 0, "d": 0},
                    {"raw": 30, "turns": 100, "t": 15, "r": 1, "v": 0, "d": 0},
                    {"raw": 40, "turns": 110, "t": 16, "r": 1, "v": 1, "d": 0},
                ],
            },
            {
                h1: {
                    "scores": [100, 200, 300, 400], "games_total": 14,
                    "max_types": [13, 14, 15, 16], "russia_count": 2, "soviet_count": 1,
                    "best_max_type": 16,
                    "_recent_archives": ["game_history/20261023_170000_score0400.jsonl"],
                },
                h2: {
                    "scores": [50, 60, 70], "games_total": 3,
                    "max_types": [10, 11, 12], "russia_count": 0, "soviet_count": 0,
                    "best_max_type": 12,
                    "_recent_archives": ["game_history/20261023_150000_score0070.jsonl"],
                },
            },
        )
        report = MODULE.build_report(self.root)
        self.assertEqual(report["current"]["fresh_n"], 2)
        self.assertEqual(report["current"]["fresh_metrics"]["score_p50"], 350.0)
        self.assertEqual(report["current"]["fresh_metrics"]["t15"], 2)
        self.assertEqual(report["current"]["fresh_metrics"]["t16"], 1)
        self.assertEqual(report["current"]["fresh_metrics"]["russia"], 2)
        self.assertEqual(report["current"]["fresh_metrics"]["soviet"], 1)
        self.assertEqual(report["current"]["fresh_metrics"]["turns_p50"], 105.0)
        self.assertEqual(report["recent_strategies"][0]["hash"], h1)
        self.assertEqual(report["recent_strategies"][1]["hash"], h2)

    def test_prepare_exports_only_one_fixed_performance_json_member(self):
        h = "c" * 64
        self._write(
            {"hash": h, "scores": [1], "_fresh_score_count": 1, "progress": [None], "max_types": [9]},
            {h: {"scores": [1], "max_types": [9]}},
        )
        MODULE.prepare(self.root, now_ms=1_800_000_000_000)
        bundle = self.root / "soren91/tmp/state/soren91_manual_evidence_export.tar.gz"
        state = json.loads((self.root / "soren91/tmp/state/soren91_manual_evidence_export.json").read_text())
        self.assertEqual(state["chunkCount"], 1)
        self.assertEqual(state["games"], [0])
        with tarfile.open(bundle, "r:gz") as tf:
            self.assertEqual(tf.getnames(), ["performance.json"])
            payload = json.loads(tf.extractfile("performance.json").read())
        self.assertEqual(payload["schema"], 1)
        self.assertEqual(payload["current"]["hash"], h)
        self.assertNotIn("files", json.dumps(payload))
        self.assertNotIn("archive_file", json.dumps(payload))

    def test_rejects_non_hex_current_hash(self):
        self._write({"hash": "../../secret", "scores": []}, {})
        with self.assertRaises(RuntimeError):
            MODULE.build_report(self.root)


if __name__ == "__main__":
    unittest.main()

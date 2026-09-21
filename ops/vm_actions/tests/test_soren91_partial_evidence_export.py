import importlib.util
import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "ops" / "vm_actions" / "soren91_evidence_export.py"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PartialEvidenceExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="soren91-partial-evidence-")
        self.root = Path(self.tmp.name) / "soren"
        self.runtime = self.root / "soren91"
        for rel in (
            "game_history",
            "tmp/summaries",
            "tmp/game_screenshots",
            "tmp/strategy_snapshots",
            "tmp/state",
        ):
            (self.runtime / rel).mkdir(parents=True, exist_ok=True)
        self.mod = load(HELPER, "soren91_partial_evidence_export_test")
        self.now_ms = 1_800_000_000_000

    def tearDown(self):
        self.tmp.cleanup()

    def write_loop_metrics(self, *, age_minutes: int = 5):
        path = self.runtime / "tmp" / "state" / "soren91_loop_metrics.json"
        path.write_text(
            json.dumps(
                {
                    "updatedAtMs": self.now_ms - age_minutes * 60_000,
                    "game": 9,
                    "turn": 2,
                    "dropProfile": {"records": [{"captureStageMs": {"screenshot": 9631.3}}]},
                }
            )
            + "\n"
        )
        mtime = (self.now_ms - age_minutes * 60_000) / 1000
        os.utime(path, (mtime, mtime))
        return path

    def test_fresh_telemetry_exports_without_completed_game(self):
        self.write_loop_metrics()

        state = self.mod.prepare_export(self.root, game_count=2, now_ms=self.now_ms)
        self.assertEqual(state["games"], [0])
        self.assertEqual(state["evidenceMode"], "telemetry_only")

        bundle = self.runtime / "tmp" / "state" / self.mod.BUNDLE_NAME
        with tarfile.open(bundle, "r:gz") as archive:
            names = set(archive.getnames())
            manifest = json.load(archive.extractfile("manifest.json"))

        self.assertEqual(
            names,
            {"manifest.json", "telemetry/soren91_loop_metrics.json"},
        )
        self.assertEqual(manifest["evidenceMode"], "telemetry_only")
        self.assertEqual(manifest["games"], [])
        self.assertFalse(any(name.startswith("game_") for name in names))

    def test_stale_telemetry_does_not_bypass_completed_game_gate(self):
        self.write_loop_metrics(age_minutes=72 * 60 + 1)

        with self.assertRaisesRegex(
            self.mod.EvidenceError,
            "no completed Soren91 evidence in the last 72 hours",
        ):
            self.mod.prepare_export(self.root, game_count=2, now_ms=self.now_ms)

    def test_completed_game_keeps_completed_mode_and_real_game_identity(self):
        token = "0042"
        history = self.runtime / "game_history" / f"game_{token}.jsonl"
        summary = self.runtime / "tmp" / "summaries" / f"game_{token}.json"
        history.write_text('{"turn":1}\n')
        summary.write_text('{"game":42,"turns":1}\n')
        mtime = (self.now_ms - 60_000) / 1000
        os.utime(summary, (mtime, mtime))
        self.write_loop_metrics()

        state = self.mod.prepare_export(self.root, game_count=1, now_ms=self.now_ms)
        self.assertEqual(state["games"], [42])
        self.assertEqual(state["evidenceMode"], "completed_games")

        bundle = self.runtime / "tmp" / "state" / self.mod.BUNDLE_NAME
        with tarfile.open(bundle, "r:gz") as archive:
            manifest = json.load(archive.extractfile("manifest.json"))
        self.assertEqual(manifest["games"], [42])
        self.assertEqual(manifest["evidenceMode"], "completed_games")


if __name__ == "__main__":
    unittest.main()

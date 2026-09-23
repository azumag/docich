"""Offline evidence for rotation waits; no VM operations or recovery."""
import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from test_collect_diagnostics import load_collector  # noqa: E402


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


class RotationReleaseEvidenceTests(unittest.TestCase):
    def temp_state(self):
        # Resolve first: macOS exposes its temp dir through /var, whose
        # symlinked ancestor the collector's guard must reject.
        base = Path(tempfile.gettempdir()).resolve()
        temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(temp.cleanup)
        return Path(temp.name)

    def test_terminal_failed_improvement_releases_after_corner_completion(self):
        from docich.corner_adapters import GameCornerAdapter
        from docich.corner_catalog import Corner

        tmp_path = self.temp_state()
        module = load_collector()
        state_path = tmp_path / "retro_corner.json"
        status_path = tmp_path / "corner_improve_nsnake.json"
        write(state_path, {"game": "nsnake", "status": "completed", "completed_at": 100,
                           "improve_job": {"spawned": True}})
        write(status_path,
              {"status": "failed", "started_at": 101, "completed_at": 102})
        adapter = GameCornerAdapter.__new__(GameCornerAdapter)
        adapter.g = SimpleNamespace(state_dir=tmp_path)
        adapter.corner = Corner("nsnake", "game", "nsnake")
        adapter.manager = SimpleNamespace(state_path=state_path)
        # The production wait source: this run's own terminal failure with a
        # free lock releases the next corner, and the record stays visible to
        # diagnostics instead of being deleted or overwritten.
        self.assertTrue(adapter.resources_released())
        output = module._collect_rotation_evidence(tmp_path)
        self.assertEqual(output["corners"]["retro_corner"]["status"], "completed")
        self.assertEqual(output["improvements"]["nsnake"], {
            "present": True, "readable": True, "lock": "absent", "status": "failed",
            "started_at": 101, "completed_at": 102,
            "reason_code": "unknown", "phase": "unknown",
        })
        # The failure taxonomy is a fixed enum: valid codes are preserved and
        # anything else is reported as unknown instead of free text.
        write(status_path, {"status": "failed", "started_at": 101, "completed_at": 102,
                            "reason_code": "eval", "phase": "eval"})
        row = module._collect_rotation_evidence(tmp_path)["improvements"]["nsnake"]
        self.assertEqual(row["reason_code"], "eval")
        self.assertEqual(row["phase"], "eval")
        write(tmp_path / "corner_improve_ninvaders.json", {
            "status": "kept", "started_at": 101, "completed_at": 102,
            "reason_code": "policy-below-margin", "phase": "eval",
        })
        policy_row = module._collect_rotation_evidence(tmp_path)["improvements"]["ninvaders"]
        self.assertEqual(policy_row["reason_code"], "policy-below-margin")
        self.assertEqual(policy_row["phase"], "eval")
        write(status_path, {"status": "failed", "started_at": 101, "completed_at": 102,
                            "reason_code": "DO-NOT-EMIT", "phase": 7})
        row = module._collect_rotation_evidence(tmp_path)["improvements"]["nsnake"]
        self.assertEqual(row["reason_code"], "unknown")
        self.assertEqual(row["phase"], "unknown")
        self.assertNotIn("DO-NOT-EMIT", json.dumps(row))

        # A stale failure cannot prove that this corner's job already ended.
        write(status_path,
              {"status": "failed", "started_at": 99, "completed_at": 102})
        self.assertFalse(adapter.resources_released())

        # A partially written/forged failed state is not sufficient evidence.
        write(status_path, {"status": "failed", "started_at": 101})
        self.assertFalse(adapter.resources_released())

    def test_per_game_improvement_evidence_is_fixed_and_read_only(self):
        for status in ("running", "failed", "kept", "skipped"):
            with self.subTest(status=status):
                tmp_path = self.temp_state()
                module = load_collector()
                write(tmp_path / "corner_improve_nsnake.json", {
                    "status": status, "started_at": 99, "completed_at": 101,
                    "prompt": "DO-NOT-EMIT", "save": "DO-NOT-EMIT", "pid": 999999,
                })
                before = {p: (p.read_bytes(), p.stat().st_mtime_ns)
                          for p in tmp_path.rglob("*") if p.is_file()}
                output = module._collect_rotation_evidence(tmp_path)
                row = output["improvements"]["nsnake"]
                self.assertEqual(row["status"], status)
                self.assertEqual(row["started_at"], 99)
                self.assertNotIn("DO-NOT-EMIT", json.dumps(output))
                self.assertNotIn("999999", json.dumps(output))
                self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns)
                                          for p in tmp_path.rglob("*") if p.is_file()})
                self.assertFalse((tmp_path / "locks").exists())

    def test_held_lock_overrides_no_assumptions_about_terminal_status(self):
        tmp_path = self.temp_state()
        module = load_collector()
        path = tmp_path / "locks/corner-improve-nsnake.lock"
        path.parent.mkdir()
        path.touch()
        with path.open("rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(
                module._collect_rotation_evidence(tmp_path)["improvements"]["nsnake"]["lock"],
                "held",
            )
        self.assertEqual(
            module._collect_rotation_evidence(tmp_path)["improvements"]["nsnake"]["lock"],
            "free",
        )

    def test_missing_corrupt_and_legacy_sources_are_distinct(self):
        tmp_path = self.temp_state()
        module = load_collector()
        (tmp_path / "corner_improve_nsnake.json").write_text("broken")
        write(tmp_path / "soren91_corner_manual.json", {"status": "failed", "completed_at": 100})
        write(tmp_path / "retro_corner_manual.json", {"game": "bastet", "status": "restoring"})
        output = module._collect_rotation_evidence(tmp_path)
        self.assertTrue(output["improvements"]["nsnake"]["present"])
        self.assertFalse(output["improvements"]["nsnake"]["readable"])
        self.assertFalse(output["improvements"]["bastet"]["present"])
        self.assertEqual(output["corners"]["soren91_corner_manual"]["status"], "failed")
        self.assertEqual(output["corners"]["retro_corner_manual"]["status"], "restoring")

    def test_malformed_values_cannot_become_free_text_or_crash(self):
        for value in ("DO-NOT-EMIT", [], {}, True, -1, float("inf")):
            with self.subTest(value=repr(value)):
                tmp_path = self.temp_state()
                module = load_collector()
                write(tmp_path / "retro_corner.json", {
                    "status": value, "game": value, "completed_at": value,
                    "recovery_required": value, "improve_job": {"spawned": value},
                })
                write(tmp_path / "corner_improve_nsnake.json", {
                    "status": value, "started_at": value, "completed_at": value,
                })
                output = module._collect_rotation_evidence(tmp_path)
                self.assertEqual(output["improvements"]["nsnake"]["status"], "unknown")
                self.assertIsNone(output["improvements"]["nsnake"]["started_at"])
                self.assertNotIn("DO-NOT-EMIT", json.dumps(output))

    def test_fixed_paths_reject_links_nonregular_and_oversized_files(self):
        tmp_path = self.temp_state()
        module = load_collector()
        outside = tmp_path / "unrelated.json"
        write(outside, {"status": "kept", "started_at": 99})
        (tmp_path / "corner_improve_nsnake.json").symlink_to(outside)
        os.mkfifo(tmp_path / "corner_improve_bastet.json")
        (tmp_path / "corner_improve_ninvaders.json").write_text(" " * 65537)
        (tmp_path / "locks").symlink_to(tmp_path)
        output = module._collect_rotation_evidence(tmp_path)
        for game in ("nsnake", "bastet", "ninvaders"):
            with self.subTest(game=game):
                self.assertFalse(output["improvements"][game]["readable"])
                self.assertEqual(output["improvements"][game]["lock"], "unknown")

    def test_full_corner_projection_includes_new_evidence(self):
        tmp_path = self.temp_state()
        module = load_collector()
        output = {}
        module._collect_corner_files(tmp_path, output, 100)
        self.assertEqual(set(output["rotation_evidence"]), {"corners", "improvements"})
        self.assertLess(len(json.dumps(output["rotation_evidence"])), 8192)


if __name__ == "__main__":
    unittest.main()

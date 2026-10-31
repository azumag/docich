import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

VM_ACTIONS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VM_ACTIONS))

import hanjuku_evidence as evidence  # noqa: E402
import runtime_bulky_reclaim as reclaim  # noqa: E402

DONE = "g1-aaaaaaaa"
ACTIVE = "g2-bbbbbbbb"
UNFINISHED = "g3-cccccccc"
LINKED = "g4-dddddddd"


def _run(name, reason="game_over"):
    gen = int(evidence.RUN_ID.fullmatch(name)[1])
    return {
        "schema": 1, "game": "hanjuku-hero", "runtime_id": name, "generation": gen,
        "lease_id": "lease", "frame_sha256": "a" * 64, "terminal_reason": reason,
        "name_entered": True, "gameplay_seen": True, "phase": "title", "title_count": 3,
        "observed_monotonic": 10, "title_since": 5,
    }


class RuntimeBulkyReclaimTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.state = Path(self.tempdir.name) / "run-soren-live"
        (self.state / "locks").mkdir(parents=True)
        (self.state / "locks" / "game-switch.lock").write_bytes(b"")
        self.canonical = {
            "schema_version": 2, "phase": "idle", "active": None, "candidate": None,
            "previous": None, "retiring": [], "operation": None, "request_id": None,
            "next_generation": 5, "revision": 1,
        }
        (self.state / "game_switch.json").write_text(json.dumps(self.canonical))
        runtimes = self.state / "runtimes"
        runtimes.mkdir()
        self.make(DONE, _run(DONE))
        self.make(ACTIVE, _run(ACTIVE))
        self.make(UNFINISHED, _run(UNFINISHED, reason="not_completed"))
        self.make(LINKED, _run(LINKED))
        (runtimes / LINKED / "hanjuku_narration.jsonl").unlink()
        (runtimes / LINKED / "hanjuku_narration.jsonl").symlink_to(runtimes / DONE / "hanjuku_run.json")
        self.canonical["active"] = {"runtime_id": ACTIVE, "generation": 2}
        self.canonical["phase"] = "ready"
        (self.state / "game_switch.json").write_text(json.dumps(self.canonical))

    def tearDown(self):
        self.tempdir.cleanup()

    def make(self, name, run):
        directory = self.state / "runtimes" / name
        directory.mkdir()
        (directory / "hanjuku_run.json").write_text(json.dumps(run))
        for file_name in ("hanjuku_commentary.jsonl", "hanjuku_narration.jsonl",
                          "hanjuku_decisions.jsonl"):
            (directory / file_name).write_bytes(b"x" * 16)

    def dry_run(self, batch=20):
        return reclaim.run(self.state, apply=False, batch=batch, expect_plan="",
                           target_free_bytes=1 << 62)

    def test_bulky_names_exclude_evidence_inputs(self):
        self.assertFalse(reclaim.BULKY_NAMES & set(evidence.FILES))

    def test_dry_run_selects_only_verified_completed_generation(self):
        result = self.dry_run()
        self.assertEqual([entry["runtime_id"] for entry in result["plan"]], [DONE])
        self.assertEqual(result["bytes_planned"], 32)
        self.assertEqual(result["bytes_removed"], 0)
        self.assertTrue((self.state / "runtimes" / DONE / "hanjuku_commentary.jsonl").exists())

    def test_apply_with_wrong_digest_removes_nothing(self):
        with self.assertRaises(evidence.EvidenceError) as caught:
            reclaim.run(self.state, apply=True, batch=20, expect_plan="0" * 64,
                        target_free_bytes=1 << 62)
        self.assertEqual(str(caught.exception), "plan_mismatch")
        self.assertTrue((self.state / "runtimes" / DONE / "hanjuku_narration.jsonl").exists())

    def test_apply_with_reviewed_digest_removes_only_bulky_logs(self):
        digest = self.dry_run()["plan_sha256"]
        result = reclaim.run(self.state, apply=True, batch=20, expect_plan=digest,
                             target_free_bytes=1 << 62)
        self.assertEqual(result["bytes_removed"], 32)
        done = self.state / "runtimes" / DONE
        self.assertFalse((done / "hanjuku_commentary.jsonl").exists())
        self.assertFalse((done / "hanjuku_narration.jsonl").exists())
        self.assertTrue((done / "hanjuku_decisions.jsonl").exists())
        self.assertTrue((done / "hanjuku_run.json").exists())
        self.assertTrue((self.state / "runtimes" / ACTIVE / "hanjuku_commentary.jsonl").exists())

    def test_rewrite_with_restored_size_and_mtime_invalidates_reviewed_plan(self):
        digest = self.dry_run()["plan_sha256"]
        target = self.state / "runtimes" / DONE / "hanjuku_commentary.jsonl"
        before = target.stat()
        target.write_bytes(b"y" * before.st_size)
        os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
        after = target.stat()
        self.assertEqual(after.st_size, before.st_size)
        self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
        self.assertNotEqual(after.st_ctime_ns, before.st_ctime_ns)
        with self.assertRaises(evidence.EvidenceError) as caught:
            reclaim.run(self.state, apply=True, batch=20, expect_plan=digest,
                        target_free_bytes=1 << 62)
        self.assertEqual(str(caught.exception), "plan_mismatch")
        self.assertTrue(target.exists())
        self.assertEqual(target.read_bytes(), b"y" * before.st_size)

    def test_batch_bounds_are_enforced(self):
        for batch in (0, reclaim.MAX_BATCH + 1):
            with self.assertRaises(evidence.EvidenceError):
                self.dry_run(batch=batch)

    def test_rejected_guard_when_switch_is_busy(self):
        import fcntl
        with open(self.state / "locks" / "game-switch.lock", "rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with self.assertRaises(evidence.EvidenceError) as caught:
                self.dry_run()
        self.assertEqual(str(caught.exception), "switch_busy")


if __name__ == "__main__":
    unittest.main()

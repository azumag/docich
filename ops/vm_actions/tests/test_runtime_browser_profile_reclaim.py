import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

VM_ACTIONS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VM_ACTIONS))

import hanjuku_evidence as evidence  # noqa: E402
import runtime_browser_profile_reclaim as reclaim  # noqa: E402

DONE = "g1-aaaaaaaa"
ACTIVE = "g2-bbbbbbbb"
UNFINISHED = "g3-cccccccc"
LINKED = "g4-dddddddd"
NEWEST = "g5-eeeeeeee"


def _run(name, reason="game_over"):
    gen = int(evidence.RUN_ID.fullmatch(name)[1])
    return {
        "schema": 1, "game": "hanjuku-hero", "runtime_id": name, "generation": gen,
        "lease_id": "lease", "frame_sha256": "a" * 64, "terminal_reason": reason,
        "name_entered": True, "gameplay_seen": True, "phase": "title", "title_count": 3,
        "observed_monotonic": 10, "title_since": 5,
    }


class RuntimeBrowserProfileReclaimTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.state = Path(self.tempdir.name) / "run-soren-live"
        (self.state / "locks").mkdir(parents=True)
        (self.state / "locks" / "game-switch.lock").write_bytes(b"")
        self.canonical = {
            "schema_version": 2, "phase": "idle", "active": None, "candidate": None,
            "previous": None, "retiring": [], "operation": None, "request_id": None,
            "next_generation": 6, "revision": 1,
        }
        (self.state / "game_switch.json").write_text(json.dumps(self.canonical))
        runtimes = self.state / "runtimes"
        runtimes.mkdir()
        for name in (DONE, ACTIVE, UNFINISHED, LINKED, NEWEST):
            self.make(name, _run(name if name != UNFINISHED else name,
                                 reason="game_over" if name != UNFINISHED else "not_completed"))
        # A symlinked profile entry must make its whole generation skipped.
        (runtimes / LINKED / "mystery-browser-profile" / "Cache" / "f").unlink()
        (runtimes / LINKED / "mystery-browser-profile" / "Cache" / "f").symlink_to(
            runtimes / DONE / "hanjuku_run.json")
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
        for prof in ("mystery-browser-profile", "weather-browser-profile"):
            (directory / prof / "Cache" / "deep").mkdir(parents=True)
            (directory / prof / "Cache" / "f").write_bytes(b"p" * 100)
            (directory / prof / "Cache" / "deep" / "g").write_bytes(b"p" * 50)

    def dry_run(self, batch=20, keep_recent=3, min_age_hours=0):
        return reclaim.run(self.state, apply=False, batch=batch, expect_plan="",
                           target_free_bytes=1 << 62, keep_recent=keep_recent,
                           min_age_hours=min_age_hours)

    def planned_ids(self, result):
        return [entry["runtime_id"] for entry in result["plan"]]

    def test_dry_run_selects_only_verified_completed_generation(self):
        # DONE is the only completed+inactive+terminal generation outside the
        # newest-3 protection window (g3/g4/g5 are the three newest).
        result = self.dry_run()
        self.assertEqual(result["mode"], "dry-run")
        self.assertEqual(self.planned_ids(result), [DONE])
        profiles = [p["name"] for p in result["plan"][0]["profiles"]]
        self.assertEqual(sorted(profiles), ["mystery-browser-profile",
                                            "weather-browser-profile"])
        # Planned bytes cover both nested profile trees.
        self.assertGreaterEqual(result["bytes_planned"], 300)

    def test_keep_recent_protects_newest_generations(self):
        # Widening the protection window to 5 newest excludes g1 as well.
        result = self.dry_run(keep_recent=5)
        self.assertEqual(self.planned_ids(result), [])

    def test_active_and_unfinished_generations_are_never_selected(self):
        self.assertNotIn(ACTIVE, self.planned_ids(self.dry_run()))
        self.assertNotIn(UNFINISHED, self.planned_ids(self.dry_run()))

    def test_apply_with_wrong_digest_removes_nothing(self):
        plan = self.dry_run()
        with self.assertRaises(evidence.EvidenceError) as ctx:
            reclaim.run(self.state, apply=True, batch=20, expect_plan="0" * 64,
                        target_free_bytes=1 << 62,
                        keep_recent=3, min_age_hours=0)
        self.assertEqual(str(ctx.exception), "plan_mismatch")
        self.assertTrue((self.state / "runtimes" / DONE / "mystery-browser-profile").is_dir())

    def test_apply_with_reviewed_digest_removes_only_profiles(self):
        plan = self.dry_run()
        result = reclaim.run(self.state, apply=True, batch=20,
                             expect_plan=plan["plan_sha256"],
                             target_free_bytes=1 << 62,
                             keep_recent=3, min_age_hours=0)
        gen = self.state / "runtimes" / DONE
        self.assertFalse((gen / "mystery-browser-profile").exists())
        self.assertFalse((gen / "weather-browser-profile").exists())
        # Evidence files and the generation dir itself survive.
        self.assertTrue((gen / "hanjuku_run.json").is_file())
        self.assertTrue((gen / "hanjuku_commentary.jsonl").is_file())
        self.assertTrue(gen.is_dir())
        self.assertGreaterEqual(result["bytes_removed"], 300)
        # Other generations remain intact (incl. symlinked LINKED).
        self.assertTrue((self.state / "runtimes" / LINKED
                         / "mystery-browser-profile").is_dir())

    def test_symlink_inside_profile_skips_generation(self):
        # The symlinked LINKED generation is completed but excluded by the
        # unsafe_file gate, so it never appears in any plan.
        self.assertNotIn(LINKED, self.planned_ids(self.dry_run()))

    def test_min_age_gate_skips_fresh_profiles(self):
        # All fixture entries are fresh, so a nonzero age gate selects none.
        result = self.dry_run(min_age_hours=24)
        self.assertEqual(self.planned_ids(result), [])

    def test_batch_bounds_are_enforced(self):
        for bad in (0, -1, reclaim.MAX_BATCH + 1, "x"):
            with self.assertRaises(evidence.EvidenceError):
                self.dry_run(batch=bad)

    def test_rejected_guard_when_switch_is_busy(self):
        import fcntl
        lock_path = self.state / "locks" / "game-switch.lock"
        fd = os.open(lock_path, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(evidence.EvidenceError) as ctx:
                self.dry_run()
            self.assertEqual(str(ctx.exception), "switch_busy")
        finally:
            os.close(fd)


if __name__ == "__main__":
    unittest.main()

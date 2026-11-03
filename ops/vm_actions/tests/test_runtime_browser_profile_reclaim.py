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

    def dry_run(self, batch=20, keep_recent=3, min_age_hours=0,
                allow_unmarked_backlog=False):
        return reclaim.run(self.state, apply=False, batch=batch, expect_plan="",
                           target_free_bytes=1 << 62, keep_recent=keep_recent,
                           min_age_hours=min_age_hours,
                           allow_unmarked_backlog=allow_unmarked_backlog)

    def make_unmarked(self, name):
        """A browser-profile generation as production writes it: no run record."""
        directory = self.state / "runtimes" / name
        (directory / "weather-browser-profile" / "Cache").mkdir(parents=True)
        (directory / "weather-browser-profile" / "Cache" / "f").write_bytes(b"p" * 100)

    def write_marker(self, name, generation):
        marker = {"schema": 1, "runtime_id": name, "generation": generation,
                  "retired_at": "2026-11-04T00:00:00Z"}
        (self.state / "runtimes" / name / reclaim.RETIRE_MARKER).write_text(
            json.dumps(marker))

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

    def test_retire_marker_is_accepted_without_hanjuku_run_record(self):
        name = "g6-abcdef12"
        self.make_unmarked(name)
        self.write_marker(name, 6)
        self.canonical["next_generation"] = 10
        (self.state / "game_switch.json").write_text(json.dumps(self.canonical))
        result = self.dry_run(keep_recent=0)
        self.assertIn(name, self.planned_ids(result))
        self.assertEqual(result["selection"]["retire-marker"], 1)
        self.assertEqual(result["selection"]["unmarked-backlog"], 0)

    def test_generation_without_proof_is_skipped_and_counted(self):
        name = "g7-abcdef12"
        self.make_unmarked(name)
        self.canonical["next_generation"] = 10
        (self.state / "game_switch.json").write_text(json.dumps(self.canonical))
        result = self.dry_run(keep_recent=0)
        self.assertNotIn(name, self.planned_ids(result))
        self.assertGreaterEqual(result["skipped_counts"].get("no_terminal_proof", 0), 1)

    def test_marker_for_another_generation_is_not_a_proof(self):
        name = "g8-abcdef12"
        self.make_unmarked(name)
        self.write_marker(name, 9)
        self.canonical["next_generation"] = 10
        (self.state / "game_switch.json").write_text(json.dumps(self.canonical))
        result = self.dry_run(keep_recent=0)
        self.assertNotIn(name, self.planned_ids(result))

    def test_unmarked_backlog_flag_selects_but_never_applies(self):
        name = "g9-abcdef12"
        self.make_unmarked(name)
        self.canonical["next_generation"] = 10
        (self.state / "game_switch.json").write_text(json.dumps(self.canonical))
        result = self.dry_run(keep_recent=0, allow_unmarked_backlog=True)
        self.assertIn(name, self.planned_ids(result))
        self.assertGreaterEqual(result["selection"]["unmarked-backlog"], 1)
        with self.assertRaises(evidence.EvidenceError) as ctx:
            reclaim.run(self.state, apply=True, batch=20,
                        expect_plan=result["plan_sha256"],
                        target_free_bytes=1 << 62, keep_recent=0,
                        min_age_hours=0, allow_unmarked_backlog=True)
        self.assertEqual(str(ctx.exception), "unmarked_backlog_dry_run_only")

    def test_invalid_terminal_record_with_marker_is_accepted(self):
        name = "g6-abcdef12"
        self.make_unmarked(name)
        (self.state / "runtimes" / name / "hanjuku_run.json").write_text(
            json.dumps(_run(name, reason="not_completed")))
        self.write_marker(name, 6)
        self.canonical["next_generation"] = 10
        (self.state / "game_switch.json").write_text(json.dumps(self.canonical))
        result = self.dry_run(keep_recent=0)
        self.assertIn(name, self.planned_ids(result))
        self.assertEqual(result["selection"]["retire-marker"], 1)

    def test_unsafe_run_record_does_not_fall_back_to_marker(self):
        name = "g6-abcdef12"
        self.make_unmarked(name)
        target = self.state / "game_switch.json"
        (self.state / "runtimes" / name / "hanjuku_run.json").symlink_to(target)
        self.write_marker(name, 6)
        self.canonical["next_generation"] = 10
        (self.state / "game_switch.json").write_text(json.dumps(self.canonical))
        result = self.dry_run(keep_recent=0)
        self.assertNotIn(name, self.planned_ids(result))

    def test_marker_contract_matches_teardown_writer(self):
        root = Path(__file__).resolve().parents[3]
        sys.path.insert(0, str(root / "src"))
        from docich import runtime_retirement
        self.assertEqual(reclaim.RETIRE_MARKER, runtime_retirement.RETIRE_MARKER)
        self.assertEqual(reclaim.RETIRE_MARKER_SCHEMA, runtime_retirement.SCHEMA)
        self.assertEqual(reclaim.MAX_MARKER_BYTES, runtime_retirement.MAX_MARKER_BYTES)
        name = "g6-abcdef12"
        self.make_unmarked(name)
        self.canonical["next_generation"] = 10
        (self.state / "game_switch.json").write_text(json.dumps(self.canonical))
        runtime_retirement.write_retire_marker(
            self.state, {"runtime_id": name, "generation": 6},
            retired_at="2026-11-04T00:00:00Z")
        result = self.dry_run(keep_recent=0)
        self.assertIn(name, self.planned_ids(result))
        self.assertEqual(result["selection"]["retire-marker"], 1)

    def test_skip_counts_include_keep_recent_and_no_profile(self):
        result = self.dry_run()
        # g3/g4/g5 are protected and the active g2 is never a candidate.
        self.assertGreaterEqual(result["skipped_counts"].get("keep_recent", 0), 3)
        self.assertGreaterEqual(result["skipped_counts"].get("active_or_retiring", 0), 1)

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

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "ops/vm_actions/prune_bundles.py"
SPEC = importlib.util.spec_from_file_location("vm_bundle_retention", SCRIPT)
retention = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(retention)


class BundleRetentionTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="vmops-bundle-retention-"))
        self.state = self.base / "state"
        self.state.mkdir()
        self.production = self.base / "production"
        self.production.mkdir()
        self.cfg = {
            "state": str(self.state),
            "repos": {"docich": {"production": str(self.production), "mode": "git"}},
        }
        self.now = 1_800_000_000
        self.bundle_root = self.state / "bundles" / "docich"
        self.bundle_root.mkdir(parents=True)
        (self.state / "current").mkdir()
        self.write_current(self.sha(1))

    @staticmethod
    def sha(value):
        return f"{value:040x}"

    def write_current(self, current, **extra):
        data = {"mode": "git", "sha": current, "previous_head": None, "pending_repairs": []}
        data.update(extra)
        (self.state / "current" / "docich.json").write_text(json.dumps(data) + "\n")

    def make_bundle(self, value, *, age_days=30, size=16):
        sha = self.sha(value)
        path = self.bundle_root / f"{sha}.bundle"
        path.write_bytes(bytes([value % 251]) * size)
        stamp = self.now - age_days * 24 * 60 * 60 + value
        os.utime(path, (stamp, stamp))
        return sha, path

    def make_release(self, sha):
        root = self.state / "releases" / "docich"
        root.mkdir(parents=True, exist_ok=True)
        path = root / sha
        path.mkdir()
        return path

    def test_rotates_only_old_unreferenced_bundles_beyond_newest_eight(self):
        current, current_path = self.make_bundle(1)
        previous, previous_path = self.make_bundle(2)
        intent_from, intent_from_path = self.make_bundle(3)
        intent_to, intent_to_path = self.make_bundle(4)
        repair, repair_path = self.make_bundle(5)
        preview, preview_path = self.make_bundle(6)
        self.write_current(
            current,
            previous_head=previous,
            deployment_intent={"from": intent_from, "to": intent_to},
            pending_repairs=[{"candidate_sha": repair}],
        )
        self.make_release(preview)

        old = [self.make_bundle(value) for value in range(100, 112)]
        result = retention.rotate_bundles(self.cfg, now=self.now)

        self.assertEqual(result["deleted_count"], 4)
        self.assertEqual(result["referenced_count"], 6)
        for path in (current_path, previous_path, intent_from_path, intent_to_path, repair_path, preview_path):
            self.assertTrue(path.exists(), "referenced bundle must always be preserved")
        oldest_four = [path for _, path in old[:4]]
        newest_eight = [path for _, path in old[4:]]
        self.assertTrue(all(not path.exists() for path in oldest_four))
        self.assertTrue(all(path.exists() for path in newest_eight))

    def test_keeps_recent_unreferenced_bundles_even_beyond_generation_cap(self):
        current, _ = self.make_bundle(1)
        self.write_current(current)
        recent = [self.make_bundle(value, age_days=1) for value in range(100, 112)]

        result = retention.rotate_bundles(self.cfg, now=self.now)

        self.assertEqual(result["deleted_count"], 0)
        self.assertEqual(result["retained_recent_count"], 12)
        self.assertTrue(all(path.exists() for _, path in recent))

    def test_dry_run_reports_deletions_without_unlinking(self):
        current, _ = self.make_bundle(1)
        self.write_current(current)
        old = [self.make_bundle(value) for value in range(100, 112)]

        result = retention.rotate_bundles(self.cfg, now=self.now, dry_run=True)

        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["deleted_count"], 4)
        self.assertTrue(all(path.exists() for _, path in old))

    def test_refuses_all_deletion_when_current_state_is_malformed(self):
        old = [self.make_bundle(value) for value in range(100, 112)]
        (self.state / "current" / "docich.json").write_text("not-json\n")

        with self.assertRaises(retention.UnsafeRetentionState):
            retention.rotate_bundles(self.cfg, now=self.now)

        self.assertTrue(all(path.exists() for _, path in old))

    def test_refuses_all_deletion_when_bundle_scan_has_unexpected_entry(self):
        current, _ = self.make_bundle(1)
        self.write_current(current)
        old = [self.make_bundle(value) for value in range(100, 112)]
        (self.bundle_root / "unexpected.tmp").write_text("evidence\n")

        with self.assertRaises(retention.UnsafeRetentionState):
            retention.rotate_bundles(self.cfg, now=self.now)

        self.assertTrue(all(path.exists() for _, path in old))

    def test_refuses_all_deletion_when_release_scan_has_unexpected_entry(self):
        current, _ = self.make_bundle(1)
        self.write_current(current)
        old = [self.make_bundle(value) for value in range(100, 112)]
        releases = self.state / "releases" / "docich"
        releases.mkdir(parents=True)
        (releases / "unexpected").mkdir()

        with self.assertRaises(retention.UnsafeRetentionState):
            retention.rotate_bundles(self.cfg, now=self.now)

        self.assertTrue(all(path.exists() for _, path in old))

    def test_refuses_all_deletion_if_candidate_changes_after_scan(self):
        current, _ = self.make_bundle(1)
        self.write_current(current)
        old = [self.make_bundle(value) for value in range(100, 112)]
        original = retention._read_preview_references

        def mutate_after_reference_scan(state, repo, max_entries):
            refs = original(state, repo, max_entries)
            old[0][1].write_bytes(b"changed")
            return refs

        with mock.patch.object(retention, "_read_preview_references", side_effect=mutate_after_reference_scan):
            with self.assertRaises(retention.UnsafeRetentionState):
                retention.rotate_bundles(self.cfg, now=self.now)

        self.assertTrue(all(path.exists() for _, path in old))


if __name__ == "__main__":
    unittest.main()

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"
SUMMARIZER = ROOT / "ops" / "vm_actions" / "summarize_storage.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


collector = load_module("storage_breakdown_tested", COLLECTOR)
summary = load_module("summarize_storage_tested", SUMMARIZER)


class StorageBreakdownTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.home = self.base / "home"
        self.soren = self.home / "soren"
        self.prod = self.home / "docich"
        self.voicevox = self.base / "voicevox"
        for path in (self.soren, self.prod, self.voicevox):
            path.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, path, size):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
        return path

    def collect(self, max_entries=1000):
        return collector._collect_storage_breakdown(
            self.soren,
            self.prod,
            home_root=self.home,
            voicevox_root=self.voicevox,
            max_entries=max_entries,
        )

    def test_collects_fixed_db_wal_shm_and_tree_categories(self):
        default = self.home / ".local/share/opencode/opencode.db"
        worker = self.soren / "tmp/state/xdg_data/opencode/opencode.db"
        self.write(default, 5000)
        self.write(Path(str(default) + "-wal"), 9000)
        self.write(worker, 7000)
        self.write(self.soren / "logs/soren_loop.log", 6000)
        self.write(self.soren / "strategy_versions_archive/by_hash/a.py", 8000)
        self.write(self.voicevox / "voicevox.7z.001", 10000)
        (self.prod / ".git").mkdir()

        result = self.collect()

        self.assertEqual(result["version"], 1)
        self.assertTrue(result["categories_overlap"])
        self.assertEqual(result["opencode_default"]["present_count"], 2)
        self.assertTrue(result["opencode_default"]["db"]["present"])
        self.assertTrue(result["opencode_default"]["wal"]["present"])
        self.assertFalse(result["opencode_default"]["shm"]["present"])
        self.assertGreater(result["opencode_default"]["allocated_bytes"], 0)
        self.assertEqual(result["opencode_worker"]["present_count"], 1)
        self.assertTrue(result["soren_logs"]["scan_complete"])
        self.assertGreater(result["soren_logs"]["allocated_bytes"], 0)
        self.assertTrue(result["strategy_archive"]["present"])
        self.assertTrue(result["voicevox_archive"]["present"])

        encoded = json.dumps(result)
        self.assertNotIn(str(self.base), encoded)
        self.assertNotIn("soren_loop.log", encoded)

    def test_symlink_root_is_not_followed_and_fails_closed(self):
        outside = self.base / "outside"
        outside.mkdir()
        self.write(outside / "large.bin", 1024 * 1024)
        profile = self.soren / "tmp/soviet_local_chromium_profile"
        profile.parent.mkdir(parents=True)
        profile.symlink_to(outside, target_is_directory=True)

        result = self.collect()
        item = result["browser_profile"]

        self.assertTrue(item["present"])
        self.assertFalse(item["scan_complete"])
        self.assertEqual(item["symlink_entries"], 1)
        self.assertLess(item["allocated_bytes"], 1024 * 1024)
        self.assertNotIn(str(outside), json.dumps(result))

    def test_entry_bound_marks_scan_incomplete_instead_of_complete_partial(self):
        logs = self.soren / "logs"
        logs.mkdir()
        for i in range(10):
            self.write(logs / f"{i}.log", 100)

        result = self.collect(max_entries=3)
        item = result["soren_logs"]

        self.assertFalse(item["scan_complete"])
        self.assertLessEqual(item["count"], 3)

    def test_hardlinks_are_not_double_charged(self):
        logs = self.soren / "logs"
        logs.mkdir()
        first = self.write(logs / "a.log", 8192)
        os.link(first, logs / "b.log")

        result = self.collect()
        item = result["soren_logs"]

        self.assertTrue(item["scan_complete"])
        self.assertEqual(item["hardlink_duplicates"], 1)


class StorageSummaryTests(unittest.TestCase):
    def test_renders_fixed_numeric_context_only(self):
        leaf = {"present": True, "scan_complete": True, "count": 1, "allocated_bytes": 123}
        family = {
            "scan_complete": True,
            "present_count": 3,
            "allocated_bytes": 666,
            "db": {**leaf, "allocated_bytes": 111},
            "wal": {**leaf, "allocated_bytes": 222},
            "shm": {**leaf, "allocated_bytes": 333},
        }
        breakdown = {
            "version": 1,
            "opencode_default": family,
            "opencode_worker": family,
            "soren_logs": leaf,
        }

        available, incomplete, context = summary.render({"storage_breakdown": breakdown})

        self.assertEqual(available, 1)
        self.assertEqual(incomplete, 1)
        self.assertIn("opencode_default_db_bytes=111", context)
        self.assertIn("opencode_default_wal_bytes=222", context)
        self.assertIn("soren_logs_bytes=123", context)
        self.assertNotIn("/", context)
        self.assertNotIn("home", context)

    def test_missing_breakdown_is_explicitly_unavailable(self):
        available, incomplete, context = summary.render({})
        self.assertEqual((available, incomplete), (0, 1))
        self.assertEqual(context, "storage_breakdown_unavailable=1")


if __name__ == "__main__":
    unittest.main()

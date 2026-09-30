import importlib.util
import json
import os
import sqlite3
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



class OpenCodeSessionAttributionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "opencode.db"
        self.now = 1_800_000_000
        con = sqlite3.connect(self.db)
        con.executescript(
            """
            create table session(
                id text primary key,
                title text not null,
                time_created integer not null
            );
            create table message(
                id text primary key,
                session_id text not null,
                data text not null
            );
            create table part(
                id text primary key,
                session_id text not null,
                data text not null
            );
            create table event(
                id text primary key,
                aggregate_id text not null,
                data text not null
            );
            """
        )
        fresh = int((self.now - 60) * 1000)
        old = int((self.now - 2 * 86400) * 1000)
        rows = (
            ("s-radio", "docich:radio_prepass", fresh),
            ("s-comment", "docich:comment", fresh),
            ("s-soren91", "docich:soren91", fresh),
            ("s-secret", "SECRET_DYNAMIC_TITLE", fresh),
            ("s-old", "docich:radio_main", old),
        )
        con.executemany("insert into session values (?,?,?)", rows)
        con.executemany(
            "insert into message values (?,?,?)",
            (
                ("m1", "s-radio", "x" * 11),
                ("m2", "s-radio", "y" * 13),
                ("m3", "s-comment", "z" * 7),
                ("m4", "s-soren91", "s" * 19),
                ("m-secret", "s-secret", "SECRET" * 100),
                ("m-old", "s-old", "o" * 999),
            ),
        )
        con.executemany(
            "insert into part values (?,?,?)",
            (
                ("p1", "s-radio", "p" * 5),
                ("p2", "s-comment", "q" * 3),
            ),
        )
        con.executemany(
            "insert into event values (?,?,?)",
            (
                ("e1", "s-radio", "e" * 101),
                ("e2", "s-radio", "f" * 103),
                ("e3", "s-comment", "g" * 17),
            ),
        )
        con.commit()
        con.close()

    def test_collects_fixed_bucket_metadata_only(self):
        result = collector._collect_opencode_session_attribution(self.db, self.now)
        self.assertTrue(result["scan_complete"])
        self.assertTrue(result["schema_supported"])
        radio = result["buckets"]["radio_prepass"]
        self.assertEqual(radio["sessions"], 1)
        self.assertEqual(radio["messages"], 2)
        self.assertEqual(radio["message_data_chars"], 24)
        self.assertEqual(radio["message_max_chars"], 13)
        self.assertEqual(radio["parts"], 1)
        self.assertEqual(radio["part_data_chars"], 5)
        self.assertEqual(radio["events"], 2)
        self.assertEqual(radio["event_data_chars"], 204)
        self.assertEqual(radio["event_max_chars"], 103)

        comment = result["buckets"]["comment"]
        self.assertEqual(comment["sessions"], 1)
        self.assertEqual(comment["message_data_chars"], 7)
        self.assertEqual(comment["event_data_chars"], 17)
        self.assertEqual(result["buckets"]["soren91"]["sessions"], 1)
        self.assertEqual(result["buckets"]["soren91"]["message_data_chars"], 19)
        self.assertEqual(result["buckets"]["radio_main"]["sessions"], 0)
        encoded = json.dumps(result)
        self.assertNotIn("SECRET_DYNAMIC_TITLE", encoded)
        self.assertNotIn("SECRETSECRET", encoded)

    def test_schema_mismatch_fails_closed(self):
        broken = Path(self.tmp.name) / "broken.db"
        con = sqlite3.connect(broken)
        con.execute("create table session(id text primary key)")
        con.commit()
        con.close()
        result = collector._collect_opencode_session_attribution(broken, self.now)
        self.assertFalse(result["scan_complete"])
        self.assertFalse(result["schema_supported"])
        self.assertEqual(
            sum(item["sessions"] for item in result["buckets"].values()),
            0,
        )


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

    def test_renders_fixed_opencode_caller_attribution_only(self):
        buckets = {
            bucket: {
                "sessions": 0,
                "messages": 0,
                "message_data_chars": 0,
                "message_max_chars": 0,
                "parts": 0,
                "part_data_chars": 0,
                "part_max_chars": 0,
                "events": 0,
                "event_data_chars": 0,
                "event_max_chars": 0,
            }
            for bucket in summary.OPENCODE_CALLER_BUCKETS
        }
        buckets["radio_prepass"].update(
            sessions=2,
            messages=3,
            message_data_chars=100,
            message_max_chars=60,
            events=5,
            event_data_chars=900,
            event_max_chars=500,
        )
        buckets["SECRET_DYNAMIC_BUCKET"] = {"sessions": 999}
        leaf = {"present": True, "scan_complete": True, "count": 0, "allocated_bytes": 0}
        family = {
            "scan_complete": True,
            "present_count": 1,
            "allocated_bytes": 0,
            "db": leaf,
            "wal": leaf,
            "shm": leaf,
        }
        data = {
            "storage_breakdown": {
                "version": 1,
                "opencode_default": family,
                "opencode_worker": family,
            },
            "opencode_session_attribution": {
                "version": 1,
                "scan_complete": True,
                "schema_supported": True,
                "window_sec": 86400,
                "buckets": buckets,
            },
        }
        available, _incomplete, context = summary.render(data)
        self.assertEqual(available, 1)
        self.assertIn("opencode_attr_complete=1", context)
        self.assertIn("opencode_attr_radio_prepass_sessions=2", context)
        self.assertIn("opencode_attr_radio_prepass_event_data_chars=900", context)
        self.assertNotIn("SECRET_DYNAMIC_BUCKET", context)

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

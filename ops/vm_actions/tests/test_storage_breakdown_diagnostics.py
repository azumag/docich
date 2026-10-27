import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
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

        coverage = result["coverage"]
        self.assertTrue(coverage["scan_complete"])
        self.assertEqual(coverage["all_sessions"], 4)
        self.assertEqual(coverage["attributed_sessions"], 3)
        self.assertEqual(coverage["unattributed_sessions"], 1)
        self.assertEqual(coverage["unattributed_15m_sessions"], 1)
        self.assertEqual(coverage["unattributed_1h_sessions"], 1)
        self.assertEqual(coverage["unattributed_2h_sessions"], 1)
        self.assertEqual(coverage["unattributed_6h_sessions"], 1)
        self.assertEqual(coverage["unattributed_latest_age_sec"], 60)

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
            sys_tmp=self.base / "tmp",
            var_log=self.base / "varlog",
            swap_file=self.base / "swapfile",
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

        self.assertEqual(result["version"], 2)
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

    # ---- new: strategy permanent archive format split ------------------

    def archive_dir(self):
        path = self.soren / "strategy_versions_archive" / "by_hash"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def test_strategy_archive_formats_split_counts_plain_and_compressed(self):
        archive = self.archive_dir()
        self.write(archive / "a1b2c3.py", 8000)
        self.write(archive / "d4e5f6.py.gz", 1000)
        # A suffix outside the fixed vocabulary must never be attributed.
        self.write(archive / "notes.txt", 4096)

        result = self.collect()
        item = result["strategy_archive_formats"]

        self.assertTrue(item["present"])
        self.assertTrue(item["scan_complete"])
        self.assertEqual(item["formats"]["plain"]["count"], 1)
        self.assertEqual(item["formats"]["compressed"]["count"], 1)
        self.assertGreater(item["formats"]["plain"]["allocated_bytes"], 0)
        self.assertGreater(item["formats"]["compressed"]["allocated_bytes"], 0)
        encoded = json.dumps(result)
        self.assertNotIn("a1b2c3", encoded)
        self.assertNotIn("d4e5f6", encoded)
        self.assertNotIn("notes.txt", encoded)

    def test_strategy_archive_formats_marks_scan_incomplete_on_entry_bound(self):
        archive = self.archive_dir()
        for index in range(4):
            self.write(archive / f"{index:064x}.py", 100)

        result = self.collect(max_entries=1)
        item = result["strategy_archive_formats"]

        self.assertFalse(item["scan_complete"])
        self.assertLessEqual(
            item["formats"]["plain"]["count"] + item["formats"]["compressed"]["count"], 1
        )

    def test_strategy_archive_formats_symlink_root_is_not_followed(self):
        outside = self.base / "outside"
        outside.mkdir()
        self.write(outside / "leak.py", 4096)
        (self.soren / "strategy_versions_archive").mkdir(parents=True)
        (self.soren / "strategy_versions_archive" / "by_hash").symlink_to(
            outside, target_is_directory=True
        )

        result = self.collect()
        item = result["strategy_archive_formats"]

        self.assertTrue(item["present"])
        self.assertFalse(item["scan_complete"])
        self.assertEqual(item["formats"]["plain"]["count"], 0)
        self.assertEqual(item["formats"]["compressed"]["count"], 0)
        self.assertNotIn(str(outside), json.dumps(result))


class StorageArchiveFormatContractTests(unittest.TestCase):
    """Format accounting contracts with exclusively virtual filesystem input."""

    ROOT = "/virtual-archive"

    @staticmethod
    def metadata(mode, inode, blocks=0):
        return SimpleNamespace(
            st_mode=mode, st_dev=1, st_ino=inode, st_blocks=blocks, st_size=0
        )

    def entry(self, name, mode, inode, blocks=0, parent=None):
        metadata = self.metadata(mode, inode, blocks)

        def entry_stat(*, follow_symlinks):
            self.assertFalse(follow_symlinks)
            return metadata

        return SimpleNamespace(
            name=name, path=f"{parent or self.ROOT}/{name}", stat=entry_stat
        )

    def collect_virtual(self, directories, *, max_entries=100):
        visited = []

        @contextmanager
        def scandir(path):
            visited.append(path)
            entries = directories[path]
            if isinstance(entries, OSError):
                raise entries
            yield iter(entries)

        virtual_os = SimpleNamespace(
            lstat=lambda path: self.metadata(0o040000, 1), scandir=scandir
        )
        with patch.object(collector, "os", virtual_os), patch.object(
            collector, "Path", lambda path: path
        ):
            result = collector._storage_suffix_usage(
                self.ROOT, collector.STRATEGY_ARCHIVE_FORMATS, max_entries=max_entries
            )
        self.assertNotIn(self.ROOT, json.dumps(result))
        return result, visited

    def test_nested_formats_are_counted(self):
        child = f"{self.ROOT}/shard"
        directories = {
            self.ROOT: [self.entry("shard", 0o040000, 2)],
            child: [
                self.entry("alpha.py", 0o100000, 3, 16, child),
                self.entry("beta.py.gz", 0o100000, 4, 8, child),
                self.entry("notes.txt", 0o100000, 5, 8, child),
            ],
        }
        result, visited = self.collect_virtual(directories)
        self.assertTrue(result["scan_complete"])
        self.assertEqual(visited, [self.ROOT, child])
        self.assertEqual(result["formats"]["plain"], {"count": 1, "allocated_bytes": 8192})
        self.assertEqual(result["formats"]["compressed"], {"count": 1, "allocated_bytes": 4096})

    def test_unknown_suffix_hardlink_never_consumes_matched_inode(self):
        entries = [
            self.entry("notes.txt", 0o100000, 3, 16),
            self.entry("alpha.py", 0o100000, 3, 16),
        ]
        for order in (entries, list(reversed(entries))):
            with self.subTest(first=order[0].name):
                result, _visited = self.collect_virtual({self.ROOT: order})
                self.assertTrue(result["scan_complete"])
                self.assertEqual(result["formats"]["plain"], {"count": 1, "allocated_bytes": 8192})
                self.assertEqual(result["formats"]["compressed"], {"count": 0, "allocated_bytes": 0})

    def test_nested_bound_marks_partial_scan_incomplete(self):
        child = f"{self.ROOT}/shard"
        result, _visited = self.collect_virtual({
            self.ROOT: [self.entry("shard", 0o040000, 2)],
            child: [self.entry("alpha.py", 0o100000, 3, 16, child)],
        }, max_entries=1)
        self.assertFalse(result["scan_complete"])
        self.assertEqual(result["formats"]["plain"], {"count": 0, "allocated_bytes": 0})

    def test_child_symlink_directory_is_not_followed(self):
        result, visited = self.collect_virtual({
            self.ROOT: [self.entry("outside", 0o120000, 2)],
        })
        self.assertTrue(result["scan_complete"])
        self.assertEqual(visited, [self.ROOT])
        self.assertEqual(result["formats"]["plain"], {"count": 0, "allocated_bytes": 0})

    def test_nested_scandir_failure_is_incomplete(self):
        child = f"{self.ROOT}/shard"
        result, _visited = self.collect_virtual({
            self.ROOT: [self.entry("shard", 0o040000, 2)],
            child: PermissionError("virtual scan denied"),
        })
        self.assertFalse(result["scan_complete"])


class StorageBreakdownV2Tests(unittest.TestCase):
    """Version 2 fixed roots (azumag/docich#2583).

    Each new root must stay bounded, symlink-safe and identity-free, and a
    root that does not exist on the scanned host must report present=false
    with a complete scan instead of pretending zero bytes were verified.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.home = self.base / "home"
        self.soren = self.home / "soren"
        self.prod = self.home / "docich"
        self.voicevox = self.base / "voicevox"
        self.sys_tmp = self.base / "tmp"
        self.var_log = self.base / "varlog"
        self.swapfile = self.base / "swapfile"
        for path in (self.soren, self.prod, self.voicevox, self.sys_tmp, self.var_log):
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
            sys_tmp=self.sys_tmp,
            var_log=self.var_log,
            swap_file=self.swapfile,
            max_entries=max_entries,
        )

    def test_version_is_2_and_absent_fixed_roots_report_present_false(self):
        # The var_log root always exists on a real host, so it is redirected
        # to a directory that itself stays empty here; only its journal child
        # is asserted absent below.
        result = self.collect()

        self.assertEqual(result["version"], 2)
        # Nothing exists under the fixture home beyond soren/docich/voicevox,
        # so every version 2 root is honestly absent instead of zero-looking.
        for category in (
            "gateway_state",
            "alt_checkout_tmp_sync",
            "alt_checkout_soren_src",
            "system_journal",
            "docich_git_objects",
            "docich_git_objects_pack",
        ):
            item = result[category]
            self.assertFalse(item["present"], category)
            self.assertTrue(item["scan_complete"], category)
            self.assertEqual(item["count"], 0, category)
            self.assertEqual(item["allocated_bytes"], 0, category)
        # The var_log and sys_tmp roots themselves exist (setUp) but stay
        # empty here: present, complete, and only the directory's own blocks.
        # The allocated size of an empty directory is filesystem-specific
        # (ext4 reports 4096, APFS reports 0), so the exact byte total is not
        # asserted; what must hold is that the tree is complete and counted.
        for category in ("system_log", "sys_tmp"):
            item = result[category]
            self.assertTrue(item["present"], category)
            self.assertTrue(item["scan_complete"], category)
            self.assertEqual(item["count"], 1, category)
            self.assertLess(item["allocated_bytes"], 8192, category)
        self.assertFalse(result["swapfile"]["present"])
        self.assertTrue(result["swapfile"]["scan_complete"])

    def test_gateway_state_system_roots_and_swapfile_are_counted(self):
        self.write(self.home / ".local/state/github-vm-ops/bundles/x.bundle", 5000)
        self.write(self.var_log / "journal/abc/system.journal", 3000)
        self.write(self.var_log / "soren-loop.log", 2000)
        self.swapfile.write_bytes(b"s" * 4096)
        self.write(self.sys_tmp / "scratch.bin", 1000)

        result = self.collect()

        gateway = result["gateway_state"]
        self.assertTrue(gateway["present"])
        self.assertTrue(gateway["scan_complete"])
        self.assertGreaterEqual(gateway["allocated_bytes"], 5000)
        journal = result["system_journal"]
        self.assertTrue(journal["present"])
        self.assertGreaterEqual(journal["allocated_bytes"], 3000)
        system_log = result["system_log"]
        self.assertTrue(system_log["present"])
        # system_log contains the journal tree (overlap by design).
        self.assertGreater(system_log["allocated_bytes"], journal["allocated_bytes"])
        self.assertTrue(result["swapfile"]["present"])
        self.assertGreaterEqual(result["swapfile"]["allocated_bytes"], 4096)
        self.assertTrue(result["sys_tmp"]["present"])
        self.assertGreaterEqual(result["sys_tmp"]["allocated_bytes"], 1000)

    def test_alternative_checkouts_are_counted_when_present(self):
        self.write(self.sys_tmp / "opencode/docich-sync/.git/objects/x", 4000)
        self.write(self.home / "soren-src/README.md", 500)

        result = self.collect()

        self.assertTrue(result["alt_checkout_tmp_sync"]["present"])
        self.assertTrue(result["alt_checkout_tmp_sync"]["scan_complete"])
        self.assertGreaterEqual(result["alt_checkout_tmp_sync"]["allocated_bytes"], 4000)
        self.assertTrue(result["alt_checkout_soren_src"]["present"])
        self.assertGreaterEqual(result["alt_checkout_soren_src"]["allocated_bytes"], 500)

    def test_docich_git_subcategories_stay_within_the_git_tree(self):
        git_dir = self.prod / ".git"
        self.write(git_dir / "objects/pack/pack-abc.pack", 6000)
        self.write(git_dir / "objects/loose-xyz", 1000)
        self.write(git_dir / "HEAD", 10)

        result = self.collect()

        objects = result["docich_git_objects"]
        pack = result["docich_git_objects_pack"]
        self.assertTrue(objects["present"])
        self.assertTrue(pack["present"])
        self.assertGreater(objects["allocated_bytes"], pack["allocated_bytes"])
        self.assertGreater(objects["count"], pack["count"])
        git_total = result["docich_git"]
        self.assertGreater(git_total["allocated_bytes"], objects["allocated_bytes"])

    def test_symlink_version_2_root_is_not_followed_and_fails_closed(self):
        outside = self.base / "outside"
        outside.mkdir()
        self.write(outside / "large.bin", 1024 * 1024)
        gateway = self.home / ".local/state/github-vm-ops"
        gateway.parent.mkdir(parents=True)
        gateway.symlink_to(outside, target_is_directory=True)

        result = self.collect()
        item = result["gateway_state"]

        self.assertTrue(item["present"])
        self.assertFalse(item["scan_complete"])
        self.assertEqual(item["symlink_entries"], 1)
        self.assertLess(item["allocated_bytes"], 1024 * 1024)
        self.assertNotIn(str(outside), json.dumps(result))

    def test_serialized_v2_breakdown_stays_bounded(self):
        # The gateway caps the whole diagnostics JSON at 65,536 bytes
        # (#2537); the storage section must stay a small fixed fraction of
        # that even with the version 2 categories added.
        self.write(self.home / ".local/state/github-vm-ops/bundles/x.bundle", 5000)
        self.write(self.var_log / "journal/abc/system.journal", 3000)
        self.swapfile.write_bytes(b"s" * 4096)

        result = self.collect()
        encoded = json.dumps(result)

        self.assertLess(len(encoded.encode()), 8192)
        self.assertNotIn(str(self.base), encoded)


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
                "coverage": {
                    "scan_complete": True,
                    "all_sessions": 5,
                    "attributed_sessions": 2,
                    "unattributed_sessions": 3,
                    "unattributed_latest_age_sec": 45,
                    "unattributed_15m_sessions": 1,
                    "unattributed_1h_sessions": 2,
                    "unattributed_2h_sessions": 3,
                    "unattributed_6h_sessions": 3,
                },
            },
        }
        available, _incomplete, context = summary.render(data)
        self.assertEqual(available, 1)
        self.assertIn("opencode_attr_complete=1", context)
        self.assertIn("opencode_attr_radio_prepass_sessions=2", context)
        self.assertIn("opencode_attr_radio_prepass_event_data_chars=900", context)
        self.assertIn("opencode_attr_coverage_complete=1", context)
        self.assertIn("opencode_attr_all_sessions=5", context)
        self.assertIn("opencode_attr_attributed_sessions=2", context)
        self.assertIn("opencode_attr_unattributed_sessions=3", context)
        self.assertIn("opencode_attr_unattributed_latest_age_sec=45", context)
        self.assertIn("opencode_attr_unattributed_15m_sessions=1", context)
        self.assertIn("opencode_attr_unattributed_2h_sessions=3", context)
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

    def test_context_keys_are_unique_across_db_family_and_tree_categories(self):
        # #866: the db/wal/shm family sum and the `<family>_total` tree
        # category are different byte totals, so their flat context keys must
        # not collide (the previous `{family}_total_bytes` did).
        leaf = {"present": True, "scan_complete": True, "count": 1, "allocated_bytes": 10}
        family = {
            "scan_complete": True,
            "present_count": 3,
            "allocated_bytes": 600,
            "db": {**leaf, "allocated_bytes": 100},
            "wal": {**leaf, "allocated_bytes": 200},
            "shm": {**leaf, "allocated_bytes": 300},
        }
        tree = {**leaf, "count": 7, "allocated_bytes": 900}
        breakdown = {
            "version": 1,
            "opencode_default": family,
            "opencode_worker": family,
            "opencode_default_total": tree,
            "opencode_worker_total": tree,
        }

        available, _incomplete, context = summary.render({"storage_breakdown": breakdown})

        self.assertEqual(available, 1)
        keys = [pair.split("=", 1)[0] for pair in context.split(",")]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertIn("opencode_default_family_bytes=600", context)
        self.assertIn("opencode_default_total_bytes=900", context)
        self.assertIn("opencode_worker_family_bytes=600", context)
        self.assertIn("opencode_worker_total_bytes=900", context)

    def test_missing_breakdown_is_explicitly_unavailable(self):
        available, incomplete, context = summary.render({})
        self.assertEqual((available, incomplete), (0, 1))
        self.assertEqual(context, "storage_breakdown_unavailable=1")

    def test_renders_strategy_archive_format_split_as_fixed_numbers(self):
        leaf = {"present": True, "scan_complete": True, "count": 1, "allocated_bytes": 123}
        breakdown = {
            "version": 1,
            "strategy_archive": leaf,
            "strategy_archive_formats": {
                "present": True,
                "scan_complete": True,
                "max_entries": 100000,
                "formats": {
                    "plain": {"count": 4488, "allocated_bytes": 800 * 1024 * 1024},
                    "compressed": {"count": 112, "allocated_bytes": 12 * 1024 * 1024},
                },
            },
        }

        available, incomplete, context = summary.render({"storage_breakdown": breakdown})

        self.assertEqual((available, incomplete), (1, 1))
        self.assertIn("strategy_archive_formats_complete=1", context)
        self.assertIn("strategy_archive_plain_count=4488", context)
        self.assertIn("strategy_archive_compressed_count=112", context)
        self.assertIn("strategy_archive_plain_bytes=838860800", context)
        self.assertIn("strategy_archive_compressed_bytes=12582912", context)
        self.assertIn("strategy_archive_compressed_share_pct=1", context)
        self.assertNotIn("/", context)
        self.assertNotIn("home", context)

    def test_strategy_archive_formats_missing_or_incomplete_reports_zero_and_flags(self):
        leaf = {"present": True, "scan_complete": True, "count": 0, "allocated_bytes": 0}
        for formats in ({}, {"scan_complete": False}):
            available, incomplete, context = summary.render(
                {
                    "storage_breakdown": {
                        "version": 1,
                        "strategy_archive": leaf,
                        "strategy_archive_formats": formats,
                    }
                }
            )
            self.assertEqual(available, 1)
            self.assertEqual(incomplete, 1)
            self.assertIn("strategy_archive_formats_complete=0", context)
            self.assertIn("strategy_archive_plain_count=0", context)
            self.assertIn("strategy_archive_compressed_count=0", context)
            self.assertIn("strategy_archive_compressed_share_pct=0", context)


    def test_v2_renders_new_fixed_root_keys(self):
        # #2583: version 2 payloads project the new fixed roots with the same
        # fixed key shape; no path, filename or identity is ever emitted.
        leaf = {"present": True, "scan_complete": True, "count": 3, "allocated_bytes": 777}
        breakdown = {"version": 2}
        for category in summary.V2_TREE_CATEGORIES:
            breakdown[category] = {**leaf, "allocated_bytes": 900 + len(category)}

        available, _incomplete, context = summary.render({"storage_breakdown": breakdown})

        self.assertEqual(available, 1)
        self.assertIn("gateway_state_complete=1", context)
        self.assertIn("gateway_state_bytes=913", context)
        self.assertIn("gateway_state_count=3", context)
        self.assertIn("system_journal_complete=1", context)
        self.assertIn("swapfile_complete=1", context)
        self.assertIn("sys_tmp_complete=1", context)
        self.assertIn("docich_git_objects_pack_complete=1", context)
        self.assertNotIn("/", context)
        self.assertNotIn("home", context)

    def test_v2_incomplete_root_flags_the_shared_incomplete(self):
        leaf = {"present": True, "scan_complete": True, "count": 1, "allocated_bytes": 10}
        breakdown = {"version": 2}
        for category in summary.V2_TREE_CATEGORIES:
            breakdown[category] = leaf
        breakdown["sys_tmp"] = {**leaf, "scan_complete": False}

        _available, incomplete, context = summary.render({"storage_breakdown": breakdown})

        self.assertEqual(incomplete, 1)
        self.assertIn("sys_tmp_complete=0", context)

    def test_v1_breakdown_does_not_invent_v2_keys(self):
        leaf = {"present": True, "scan_complete": True, "count": 1, "allocated_bytes": 123}
        breakdown = {"version": 1, "soren_logs": leaf}

        available, incomplete, context = summary.render({"storage_breakdown": breakdown})

        self.assertEqual(available, 1)
        self.assertIn("soren_logs_bytes=123", context)
        for category in summary.V2_TREE_CATEGORIES:
            self.assertNotIn(f"{category}_complete", context)
        # Missing v2 keys must not be charged against the shared incomplete
        # flag while the deployed collector is still version 1.
        self.assertNotIn("gateway_state", context)

    def test_v2_context_keys_are_unique(self):
        leaf = {"present": True, "scan_complete": True, "count": 2, "allocated_bytes": 55}
        breakdown = {"version": 2, "soren_logs": leaf}
        for category in summary.V2_TREE_CATEGORIES:
            breakdown[category] = leaf

        available, _incomplete, context = summary.render({"storage_breakdown": breakdown})

        self.assertEqual(available, 1)
        keys = [pair.split("=", 1)[0] for pair in context.split(",")]
        self.assertEqual(len(keys), len(set(keys)))

    def test_unsupported_breakdown_version_is_unavailable(self):
        available, incomplete, context = summary.render(
            {"storage_breakdown": {"version": 3, "soren_logs": {}}}
        )
        self.assertEqual((available, incomplete), (0, 1))
        self.assertEqual(context, "storage_breakdown_unavailable=1")


class SysTmpReclaimCandidateTests(unittest.TestCase):
    """Case A of azumag/docich#2735: identity-free dry-run candidate count.

    Every gate must pass for an entry to count. Git and /proc are faked so the
    tests stay deterministic; the fixture paths and names must never leak.
    """

    NOW = 1_800_000_000
    IDLE = 24 * 3600
    MIN_BYTES = 32 * 1024
    BIG = 128 * 1024

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.sys_tmp = self.base / "tmp"
        self.proc = self.base / "proc"
        self.sys_tmp.mkdir()
        self.proc.mkdir()
        self.git_dirty = set()
        self.git_error = None
        self.git_calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def fake_run(self, args, **kwargs):
        self.git_calls.append(args)
        if self.git_error is not None:
            raise self.git_error
        repo = args[args.index("-C") + 1]
        if "rev-parse" in args:
            return subprocess.CompletedProcess(args, 0, stdout=b"true\n", stderr=b"")
        out = b" M file.txt\n" if repo in self.git_dirty else b""
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr=b"")

    def sweep(self, **overrides):
        kwargs = dict(
            now=self.NOW,
            proc_root=self.proc,
            min_bytes=self.MIN_BYTES,
            idle_sec=self.IDLE,
        )
        kwargs.update(overrides)
        with patch.object(collector.subprocess, "run", side_effect=self.fake_run):
            return collector._sweep_sys_tmp_reclaim_candidates(self.sys_tmp, **kwargs)

    def make_repo(self, name, *, git_file=False, size=None, age=2 * 24 * 3600):
        repo = self.sys_tmp / name
        if git_file:
            repo.mkdir(parents=True)
            (repo / ".git").write_text("gitdir: /nowhere\n")
        else:
            (repo / ".git" / "objects").mkdir(parents=True)
        (repo / "data.bin").write_bytes(b"x" * (self.BIG if size is None else size))
        self.age_tree(repo, self.NOW - age)
        return repo

    def age_tree(self, path, mtime):
        for root, dirs, files in os.walk(path):
            for name in dirs + files:
                os.utime(os.path.join(root, name), (mtime, mtime), follow_symlinks=False)
        os.utime(path, (mtime, mtime))

    def make_proc(self, pid, *, cmdline=b"/usr/bin/sleep\0infinity\0", cwd=None, fd=None):
        base = self.proc / str(pid)
        (base / "fd").mkdir(parents=True)
        (base / "cmdline").write_bytes(cmdline)
        if cwd is not None:
            os.symlink(cwd, base / "cwd")
        if fd is not None:
            os.symlink(fd, base / "fd" / "3")

    def test_clean_idle_repo_with_git_dir_is_counted_without_identity(self):
        repo = self.make_repo("clone-a")

        result = self.sweep()

        self.assertEqual(result["count"], 1)
        self.assertTrue(result["scan_complete"])
        self.assertGreaterEqual(result["allocated_bytes"], self.BIG)
        self.assertNotIn("clone-a", json.dumps(result))
        self.assertNotIn(str(repo), json.dumps(result))

    def test_linked_worktree_with_git_file_is_counted(self):
        self.make_repo("wt-b", git_file=True)

        result = self.sweep()

        self.assertEqual(result["count"], 1)
        self.assertTrue(result["scan_complete"])

    def test_directory_without_git_marker_is_excluded(self):
        plain = self.sys_tmp / "plain"
        plain.mkdir()
        (plain / "data.bin").write_bytes(b"x" * self.BIG)
        self.age_tree(plain, self.NOW - 2 * 24 * 3600)

        result = self.sweep()

        self.assertEqual(result["count"], 0)
        self.assertTrue(result["scan_complete"])

    def test_top_level_symlink_is_never_followed(self):
        outside = self.base / "outside-repo"
        (outside / ".git").mkdir(parents=True)
        (outside / "data.bin").write_bytes(b"x" * self.BIG)
        self.age_tree(outside, self.NOW - 2 * 24 * 3600)
        os.symlink(outside, self.sys_tmp / "link")

        result = self.sweep()

        self.assertEqual(result["count"], 0)

    def test_small_repo_below_size_gate_is_excluded(self):
        self.make_repo("small", size=1024)

        result = self.sweep()

        self.assertEqual(result["count"], 0)
        self.assertTrue(result["scan_complete"])

    def test_recent_file_keeps_repo(self):
        repo = self.make_repo("recent")
        os.utime(repo / "data.bin", (self.NOW - 60, self.NOW - 60))

        result = self.sweep()

        self.assertEqual(result["count"], 0)

    def test_recent_git_dir_keeps_repo_before_git_runs(self):
        repo = self.make_repo("recent-objects")
        os.utime(repo / ".git", (self.NOW - 60, self.NOW - 60))

        with patch.object(collector.subprocess, "run", side_effect=self.fake_run):
            result = collector._sweep_sys_tmp_reclaim_candidates(
                self.sys_tmp, now=self.NOW, proc_root=self.proc,
                min_bytes=self.MIN_BYTES, idle_sec=self.IDLE,
            )

        self.assertEqual(result["count"], 0)
        self.assertEqual(self.git_calls, [])

    def test_dirty_tree_is_excluded(self):
        repo = self.make_repo("dirty")
        self.git_dirty.add(str(repo))

        result = self.sweep()

        self.assertEqual(result["count"], 0)
        self.assertTrue(result["scan_complete"])

    def test_git_timeout_or_missing_git_fails_closed(self):
        self.make_repo("slow")
        for error in (subprocess.TimeoutExpired(cmd="git", timeout=5), OSError("no git")):
            with self.subTest(error=type(error).__name__):
                self.git_error = error
                result = self.sweep()
                self.assertEqual(result["count"], 0)

    def test_process_cmdline_cwd_and_fd_references_keep_repo(self):
        repo = self.make_repo("referenced")
        cases = {
            "cmdline": {"cmdline": f"tool\0{repo}\0".encode()},
            "cwd": {"cwd": str(repo / "sub")},
            "fd": {"fd": str(repo / "data.bin")},
        }
        for label, kwargs in cases.items():
            with self.subTest(reference=label):
                self.make_proc(910001, **kwargs)
                result = self.sweep()
                self.assertEqual(result["count"], 0)
                self.assertTrue(result["scan_complete"])
                for child in self.proc.iterdir():
                    shutil.rmtree(child)

    def test_unrelated_process_does_not_keep_repo(self):
        self.make_repo("unrelated")
        self.make_proc(910002, cwd="/", fd="/dev/null")

        result = self.sweep()

        self.assertEqual(result["count"], 1)

    def test_missing_proc_root_is_incomplete_and_counts_nothing(self):
        self.make_repo("no-proc")

        result = self.sweep(proc_root=self.base / "absent-proc")

        self.assertEqual(result["count"], 0)
        self.assertFalse(result["scan_complete"])

    def test_entry_bound_marks_scan_incomplete(self):
        self.make_repo("bound-a")
        self.make_repo("bound-b")

        with patch.object(collector.subprocess, "run", side_effect=self.fake_run):
            result = collector._sweep_sys_tmp_reclaim_candidates(
                self.sys_tmp, max_entries=1, now=self.NOW, proc_root=self.proc,
                min_bytes=self.MIN_BYTES, idle_sec=self.IDLE,
            )

        self.assertFalse(result["scan_complete"])

    def test_unexpected_error_is_contained_and_reported_incomplete(self):
        self.make_repo("boom")

        with patch.object(collector, "_storage_tree_usage", side_effect=RuntimeError("boom")):
            result = self.sweep()

        self.assertEqual(result["count"], 0)
        self.assertFalse(result["scan_complete"])

    def test_breakdown_carries_the_new_key_and_stays_identity_free(self):
        self.make_repo("in-breakdown")
        soren = self.base / "home" / "soren"
        prod = self.base / "home" / "docich"
        soren.mkdir(parents=True)
        prod.mkdir(parents=True)

        with patch.object(collector.subprocess, "run", side_effect=self.fake_run):
            result = collector._collect_storage_breakdown(
                soren, prod, home_root=self.base / "home",
                voicevox_root=self.base / "voicevox",
                sys_tmp=self.sys_tmp, var_log=self.base / "varlog",
                swap_file=self.base / "swapfile",
            )

        self.assertIn("sys_tmp_reclaim_candidates", result)
        self.assertEqual(set(result["sys_tmp_reclaim_candidates"]),
                         {"count", "allocated_bytes", "scan_complete"})
        self.assertNotIn("in-breakdown", json.dumps(result))


if __name__ == "__main__":
    unittest.main()

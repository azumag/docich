import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"
SUMMARIZER = ROOT / "ops" / "vm_actions" / "summarize_runtime.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TmpSharedObjectDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.collector = load_module("vm_collect_diagnostics_tmp_so", COLLECTOR)
        cls.summarizer = load_module("vm_summarize_runtime_tmp_so", SUMMARIZER)

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="vmops-tmp-so-"))
        self.tmp = self.base / "tmp"
        self.proc = self.base / "proc"
        self.tmp.mkdir()
        self.proc.mkdir()
        self.now = 2_000_000_000

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def make_candidate(self, name, size, age):
        path = self.tmp / name
        path.write_bytes(b"x" * size)
        stamp = self.now - age
        os.utime(path, (stamp, stamp))
        return path

    def proc_for_current_uid(self, pid="1234"):
        root = self.proc / pid
        (root / "fd").mkdir(parents=True)
        return root

    @staticmethod
    def maps_line(path):
        st = path.stat()
        device = f"{os.major(st.st_dev):x}:{os.minor(st.st_dev):x}"
        return f"7f000000-7f001000 r-xp 00000000 {device} {st.st_ino} /redacted\n"

    def test_counts_known_families_and_same_uid_references_without_identities(self):
        old_mapped = self.make_candidate(
            ".native-00000000.so", 11, self.collector.TMP_SO_STALE_SEC + 10
        )
        old_unreferenced = self.make_candidate(
            ".bun-old.so", 13, self.collector.TMP_SO_STALE_SEC + 20
        )
        young_open = self.make_candidate(".bun-young.so", 17, 60)
        self.make_candidate("normal.so", 19, self.collector.TMP_SO_STALE_SEC + 30)
        symlink = self.tmp / ".bun-link.so"
        symlink.symlink_to(old_unreferenced)

        proc = self.proc_for_current_uid()
        (proc / "maps").write_text(self.maps_line(old_mapped), encoding="utf-8")
        (proc / "fd" / "9").symlink_to(young_open)

        result = self.collector._collect_tmp_shared_objects(
            self.now, self.tmp, self.proc, euid=os.geteuid()
        )

        self.assertTrue(result["scan_complete"])
        self.assertTrue(result["reference_scan_complete"])
        self.assertEqual(result["candidate_entries"], 3)
        self.assertEqual(result["candidate_count"], 3)
        self.assertEqual(result["candidate_bytes"], 41)
        self.assertEqual(result["old_candidate_count"], 2)
        self.assertEqual(result["old_candidate_bytes"], 24)
        self.assertEqual(result["referenced_count"], 2)
        self.assertEqual(result["referenced_bytes"], 28)
        self.assertEqual(result["old_unreferenced_count"], 1)
        self.assertEqual(result["old_unreferenced_bytes"], 13)
        self.assertNotIn("path", result)
        self.assertNotIn("pid", result)

    def test_truncated_candidate_scan_never_claims_unreferenced_files(self):
        self.make_candidate(".a-00000000.so", 5, self.collector.TMP_SO_STALE_SEC + 1)
        self.make_candidate(".b-00000000.so", 7, self.collector.TMP_SO_STALE_SEC + 1)

        result = self.collector._collect_tmp_shared_objects(
            self.now,
            self.tmp,
            self.proc,
            euid=os.geteuid(),
            max_candidates=1,
        )

        self.assertFalse(result["scan_complete"])
        self.assertFalse(result["reference_scan_complete"])
        self.assertIsNone(result["old_unreferenced_count"])
        self.assertIsNone(result["old_unreferenced_bytes"])

    def test_public_summary_exposes_only_fixed_storage_metrics(self):
        data = {
            "status": "ok",
            "workers": {},
            "queues": {},
            "ai": {},
            "improvement": {},
            "corners": {},
            "storage_artifacts": {
                "scan_complete": True,
                "reference_scan_complete": True,
                "candidate_count": 3,
                "candidate_bytes": 41,
                "old_candidate_count": 2,
                "old_candidate_bytes": 24,
                "referenced_count": 2,
                "referenced_bytes": 28,
                "old_unreferenced_count": 1,
                "old_unreferenced_bytes": 13,
                "foreign_owner_entries": 0,
                "hardlink_entries": 0,
            },
        }
        severity, summary = self.summarizer.summarize(data)
        self.assertEqual(severity, "ok")
        self.assertIn("tmp_so_scan_complete=1", summary)
        self.assertIn("tmp_so_reference_scan_complete=1", summary)
        self.assertIn("tmp_so_candidate_count=3", summary)
        self.assertIn("tmp_so_old_unreferenced_known=1", summary)
        self.assertIn("tmp_so_old_unreferenced_count=1", summary)
        self.assertIn("tmp_so_old_unreferenced_bytes=13", summary)
        self.assertNotIn("/tmp", summary)
        self.assertNotIn("native", summary)

    def test_public_summary_hides_unknown_unreferenced_value_on_incomplete_scan(self):
        data = {
            "status": "ok",
            "workers": {},
            "queues": {},
            "ai": {},
            "improvement": {},
            "corners": {},
            "storage_artifacts": {
                "scan_complete": True,
                "reference_scan_complete": False,
                "candidate_count": 2,
                "candidate_bytes": 20,
                "old_candidate_count": 2,
                "old_candidate_bytes": 20,
                "referenced_count": 0,
                "referenced_bytes": 0,
                "old_unreferenced_count": None,
                "old_unreferenced_bytes": None,
                "foreign_owner_entries": 0,
                "hardlink_entries": 0,
            },
        }
        _, summary = self.summarizer.summarize(data)
        self.assertIn("tmp_so_old_unreferenced_known=0", summary)
        self.assertIn("tmp_so_old_unreferenced_count=0", summary)
        self.assertIn("tmp_so_old_unreferenced_bytes=0", summary)


if __name__ == "__main__":
    unittest.main()

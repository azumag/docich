import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GATEWAY = ROOT / "ops" / "vm_actions" / "gateway.py"
SUMMARY = ROOT / "ops" / "vm_actions" / "summarize_runtime.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BundleStorageDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.gateway = load_module("vm_gateway_bundle_test", GATEWAY)
        self.summary = load_module("vm_summary_bundle_test", SUMMARY)
        self.base = Path(tempfile.mkdtemp(prefix="vmops-bundle-diag-"))
        self.state = self.base / "state"
        self.production = self.base / "docich"
        self.production.mkdir()
        self.cfg = {
            "state": str(self.state),
            "repos": {"docich": {"production": str(self.production), "mode": "git", "projections": {}}},
        }
        self.now = 2_000_000_000
        (self.state / "current").mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def _bundle(self, sha, size, age_sec=0):
        root = self.state / "bundles" / "docich"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{sha}.bundle"
        path.write_bytes(b"x" * size)
        os.utime(path, (self.now - age_sec, self.now - age_sec))
        return path

    def _write_current(self, payload):
        (self.state / "current" / "docich.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_reports_bounded_identity_free_reference_and_age_metrics(self):
        a, b, c, d, e, f = (char * 40 for char in "abcdef")
        self._bundle(a, 10)
        self._bundle(b, 20)
        self._bundle(c, 30, 8 * 24 * 60 * 60)
        self._bundle(d, 40, 31 * 24 * 60 * 60)
        self._bundle(e, 50, 31 * 24 * 60 * 60)
        self._bundle(f, 60, 31 * 24 * 60 * 60)
        self._write_current(
            {
                "mode": "git",
                "sha": a,
                "previous_head": b,
                "deployment_intent": {"from": a, "to": c},
                "pending_repairs": [{"candidate_sha": d}],
            }
        )
        (self.state / "releases" / "docich" / e).mkdir(parents=True)

        result = self.gateway._bundle_storage_review(self.cfg, "docich", now=self.now)

        self.assertTrue(result["scan_complete"])
        self.assertTrue(result["reference_scan_complete"])
        self.assertEqual(result["bundle_count"], 6)
        self.assertEqual(result["bundle_bytes"], 210)
        self.assertEqual((result["older_7d_count"], result["older_7d_bytes"]), (4, 180))
        self.assertEqual((result["older_30d_count"], result["older_30d_bytes"]), (3, 150))
        self.assertEqual((result["referenced_count"], result["referenced_bytes"]), (5, 150))
        self.assertEqual(result["current_ref_count"], 1)
        self.assertEqual(result["previous_ref_count"], 1)
        self.assertEqual(result["intent_ref_count"], 2)
        self.assertEqual(result["pending_repair_ref_count"], 1)
        self.assertEqual(result["preview_ref_count"], 1)
        self.assertEqual((result["unreferenced_count"], result["unreferenced_bytes"]), (1, 60))
        self.assertNotIn(a, json.dumps(result))

        severity, text = self.summary.summarize({"status": "warn", "bundle_storage": result})
        self.assertEqual(severity, "warn")
        self.assertIn("bundle_scan_complete=1", text)
        self.assertIn("bundle_reference_scan_complete=1", text)
        self.assertIn("bundle_count=6", text)
        self.assertIn("bundle_bytes=210", text)
        self.assertIn("bundle_unreferenced_known=1", text)
        self.assertIn("bundle_unreferenced_count=1", text)
        self.assertIn("bundle_unreferenced_bytes=60", text)
        self.assertNotIn(a, text)

    def test_unexpected_bundle_entry_fails_closed_for_unreferenced_metrics(self):
        a = "a" * 40
        self._bundle(a, 10)
        (self.state / "bundles" / "docich" / "unexpected.txt").write_text("x", encoding="utf-8")
        self._write_current({"mode": "git", "sha": a, "previous_head": None, "pending_repairs": []})

        result = self.gateway._bundle_storage_review(self.cfg, "docich", now=self.now)

        self.assertFalse(result["scan_complete"])
        self.assertTrue(result["reference_scan_complete"])
        self.assertIsNone(result["unreferenced_count"])
        self.assertIsNone(result["unreferenced_bytes"])
        _severity, text = self.summary.summarize({"status": "ok", "bundle_storage": result})
        self.assertIn("bundle_unreferenced_known=0", text)
        self.assertIn("bundle_unreferenced_count=0", text)
        self.assertIn("bundle_unreferenced_bytes=0", text)

    def test_malformed_reference_fails_closed(self):
        a = "a" * 40
        self._bundle(a, 10)
        self._write_current(
            {
                "mode": "git",
                "sha": a,
                "previous_head": None,
                "pending_repairs": [{"candidate_sha": "not-a-sha"}],
            }
        )

        result = self.gateway._bundle_storage_review(self.cfg, "docich", now=self.now)

        self.assertTrue(result["scan_complete"])
        self.assertFalse(result["reference_scan_complete"])
        self.assertIsNone(result["unreferenced_count"])
        self.assertIsNone(result["unreferenced_bytes"])

    def test_entry_bound_fails_closed(self):
        a, b = "a" * 40, "b" * 40
        self._bundle(a, 10)
        self._bundle(b, 20)
        self._write_current({"mode": "git", "sha": a, "previous_head": None, "pending_repairs": []})

        result = self.gateway._bundle_storage_review(self.cfg, "docich", now=self.now, max_entries=1)

        self.assertFalse(result["scan_complete"])
        self.assertIsNone(result["unreferenced_count"])


if __name__ == "__main__":
    unittest.main()

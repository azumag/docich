import importlib.util
import json
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"


def load_collector():
    spec = importlib.util.spec_from_file_location("collect_diagnostics_paper", str(COLLECTOR))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PaperImproveDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="paper-diag-")
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.state = root / "state"
        self.soren = root / "soren"
        self.state.mkdir(parents=True)
        (self.soren / "tmp" / "state").mkdir(parents=True)
        self.now = int(time.time())
        self.module = load_collector()

    def write_json(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_manual_corner_and_improve_status_are_allowlisted(self):
        self.write_json(
            self.state / "paper_corner_manual.json",
            {
                "status": "completed",
                "date": "2026-09-12",
                "previous_game": "sorengame",
                "requested_at": 100.0,
                "started_at": 200.0,
                "ends_at": 260.0,
                "completed_at": 270.0,
                "reports": {
                    "opening": {
                        "text": "SECRET ANNOUNCEMENT BODY",
                        "overlay": True,
                        "speech": True,
                    }
                },
                "improve_job": {
                    "spawned": True,
                    "date": "2026-09-12",
                    "log": "/private/path/SECRET-improve.log",
                },
            },
        )
        self.write_json(
            self.state / "trading" / "paper_improve_status.json",
            {
                "schema_version": 1,
                "source": "paper",
                "status": "failed",
                "phase": "facts",
                "progress": 10,
                "detail": "boom token=SUPERSECRET123",
                "started_at": 300.0,
                "updated_at": 301.0,
                "completed_at": 301.0,
                "changed": False,
                "prompt": "SECRET PROMPT BODY",
            },
        )

        result = self.module._collect_programs(self.state, self.soren, self.now)
        manual = result["paper_corner_manual"]
        self.assertTrue(manual["present"])
        self.assertTrue(manual["readable"])
        self.assertEqual(manual["status"], "completed")
        self.assertEqual(manual["announcements"], {"total": 1, "overlay": 1, "speech": 1})
        self.assertEqual(
            manual["improve_job"],
            {"spawned": True, "date": "2026-09-12", "error": None},
        )

        improve = result["paper_improve"]
        self.assertTrue(improve["present"])
        self.assertTrue(improve["readable"])
        self.assertEqual(improve["status"], "failed")
        self.assertEqual(improve["phase"], "facts")
        self.assertEqual(improve["progress"], 10)
        self.assertFalse(improve["changed"])
        self.assertIn("[REDACTED]", improve["detail"])

        rendered = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("SECRET ANNOUNCEMENT BODY", rendered)
        self.assertNotIn("SECRET-improve.log", rendered)
        self.assertNotIn("SECRET PROMPT BODY", rendered)
        self.assertNotIn("SUPERSECRET123", rendered)
        self.assertNotIn("\"log\"", rendered)
        self.assertNotIn("\"prompt\"", rendered)

    def test_improve_progress_is_bounded_and_absence_is_explicit(self):
        result = self.module._collect_programs(self.state, self.soren, self.now)
        self.assertEqual(
            result["paper_improve"],
            {"present": False, "readable": False, "age_sec": -1},
        )
        self.assertFalse(result["paper_corner_manual"]["present"])

        self.write_json(
            self.state / "trading" / "paper_improve_status.json",
            {"status": "running", "phase": "generate", "progress": 999},
        )
        result = self.module._collect_programs(self.state, self.soren, self.now)
        self.assertEqual(result["paper_improve"]["progress"], 100)

    def test_corrupt_manual_and_improve_state_do_not_crash(self):
        (self.state / "paper_corner_manual.json").write_text("{bad", encoding="utf-8")
        improve = self.state / "trading" / "paper_improve_status.json"
        improve.parent.mkdir(parents=True, exist_ok=True)
        improve.write_text("[1,2,3]", encoding="utf-8")
        result = self.module._collect_programs(self.state, self.soren, self.now)
        self.assertEqual(
            result["paper_corner_manual"],
            {"present": True, "readable": False},
        )
        self.assertTrue(result["paper_improve"]["present"])
        self.assertFalse(result["paper_improve"]["readable"])


if __name__ == "__main__":
    unittest.main()

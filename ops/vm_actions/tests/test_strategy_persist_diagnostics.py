import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"


def load_collector():
    spec = importlib.util.spec_from_file_location(
        "collect_diagnostics_persist", str(COLLECTOR)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StrategyPersistDiagnosticsTests(unittest.TestCase):
    """#2489: adopted runtime strategies must be visible when they never reach
    the reviewed lineage. Only fixed enums and bounded ids are projected."""

    @classmethod
    def setUpClass(cls):
        cls.collector = load_collector()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vmops-persist-")
        base = Path(self.tmp.name)
        self.soren = base / "soren"
        self.state = self.soren / "tmp" / "state"
        self.state.mkdir(parents=True)
        self.clone_git = base / "soren-persist" / ".git"
        self.clone_git.mkdir(parents=True)
        self.now = int(time.time())

    def tearDown(self):
        self.tmp.cleanup()

    def write_status(self, payload, raw=None, age_sec=120):
        path = self.clone_git / "persist-status.json"
        path.write_text(raw if raw is not None else json.dumps(payload), encoding="utf-8")
        os.utime(path, (self.now - age_sec, self.now - age_sec))
        return path

    def write_failure(self, payload):
        path = self.state / "strategy_persist_last_failure.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def collect(self):
        return self.collector._collect_improvement(self.soren, self.now)

    def severity(self, improvement):
        workers = {
            "required_down": [], "required_stale": [], "duplicates": 0,
            "zombies": 0, "paused": [], "unregistered": [],
        }
        return self.collector._severity(
            workers, {"stale_locks": 0}, {"all_failed": 0, "queue_giveups": 0}, improvement
        )

    def test_missing_status_is_not_a_failure(self):
        persist = self.collect()["persist"]
        self.assertFalse(persist["present"])
        self.assertFalse(persist["readable"])
        self.assertEqual(persist["result"], "unknown")
        self.assertIsNone(persist["last_failure"])
        self.assertEqual(self.severity(self.collect()), "ok")

    def test_successful_candidate_projects_fixed_fields(self):
        self.write_status({
            "timestamp": "2026-10-23T00:00:00Z",
            "result": "ok",
            "reason": "none",
            "rc": 0,
            "origin_main": "abcdef0123456789abcdef0123456789abcdef01",
            "last_success_at": self.now - 100,
            "message": "eloop Improve after game #1",
            "branch": "runtime/eloop-improve",
            "commit": "0123456789abcdef0123456789abcdef01234567",
            "error": "",
        })
        persist = self.collect()["persist"]
        self.assertTrue(persist["present"])
        self.assertTrue(persist["readable"])
        self.assertEqual(persist["result"], "ok")
        self.assertEqual(persist["reason"], "none")
        self.assertEqual(persist["rc"], 0)
        self.assertEqual(persist["origin_main"], "abcdef0123456789abcdef0123456789abcdef01")
        self.assertEqual(persist["last_success_at"], self.now - 100)
        self.assertEqual(persist["last_success_age_sec"], 100)
        self.assertEqual(persist["age_sec"], 120)
        self.assertFalse(persist["stale"])
        self.assertEqual(persist["branch"], "runtime/eloop-improve")
        self.assertEqual(persist["commit"], "0123456789abcdef0123456789abcdef01234567")
        self.assertEqual(self.severity(self.collect()), "ok")

    def test_stale_status_is_warn(self):
        self.write_status(
            {"result": "no-change", "reason": "none", "rc": 3,
             "last_success_at": self.now - 30 * 24 * 3600},
            age_sec=8 * 24 * 3600,
        )
        persist = self.collect()["persist"]
        self.assertTrue(persist["stale"])
        self.assertEqual(self.severity(self.collect()), "warn")

    def test_failed_persist_is_warn_with_enum_reason(self):
        self.write_status({
            "result": "fail",
            "reason": "push_failed",
            "rc": 1,
            "branch": "runtime/eloop-improve",
            "commit": "",
            "message": "adopt after game #1",
            "error": "fatal: could not read from remote",
        })
        persist = self.collect()["persist"]
        self.assertEqual(persist["result"], "fail")
        self.assertEqual(persist["reason"], "push_failed")
        self.assertEqual(persist["rc"], 1)
        self.assertIsNone(persist["commit"])
        self.assertEqual(self.severity(self.collect()), "warn")

    def test_unknown_reason_is_bounded_not_raw(self):
        self.write_status({
            "result": "fail",
            "reason": "DO-NOT-PUBLISH /home/ubuntu/secret",
            "rc": 1,
            "message": "DO-NOT-PUBLISH message",
            "error": "DO-NOT-PUBLISH error",
            "branch": "../../etc/passwd",
            "commit": "DO-NOT-PUBLISH",
        })
        persist = self.collect()["persist"]
        self.assertEqual(persist["reason"], "unknown")
        self.assertIsNone(persist["branch"])
        self.assertIsNone(persist["commit"])
        self.assertNotIn("DO-NOT-PUBLISH", json.dumps(self.collect()))

    def test_legacy_status_without_reason_or_rc_still_fails_closed(self):
        self.write_status({"result": "fail", "error": "git push failed"})
        persist = self.collect()["persist"]
        self.assertEqual(persist["result"], "fail")
        self.assertEqual(persist["reason"], "unknown")
        self.assertIsNone(persist["rc"])
        self.assertEqual(self.severity(self.collect()), "warn")

    def test_no_change_is_healthy(self):
        self.write_status({"result": "no-change", "reason": "none", "rc": 3})
        persist = self.collect()["persist"]
        self.assertEqual(persist["result"], "no-change")
        self.assertEqual(persist["rc"], 3)
        self.assertEqual(self.severity(self.collect()), "ok")

    def test_malformed_status_is_readable_false_and_warn(self):
        self.write_status(None, raw="not-json")
        persist = self.collect()["persist"]
        self.assertTrue(persist["present"])
        self.assertFalse(persist["readable"])
        self.assertEqual(persist["result"], "unknown")
        self.assertEqual(self.severity(self.collect()), "warn")

    def test_durable_failure_record_keeps_enum_and_label(self):
        self.write_failure({
            "schema_version": 1,
            "status": "failed",
            "failure_code": "strategy_persist_failed",
            "reason": "pr_failed",
            "rc": 1,
            "label": "wildcard-adopt",
            "failed_at": self.now - 60,
        })
        persist = self.collect()["persist"]
        failure = persist["last_failure"]
        self.assertEqual(failure["failure_code"], "strategy_persist_failed")
        self.assertEqual(failure["reason"], "pr_failed")
        self.assertEqual(failure["rc"], 1)
        self.assertEqual(failure["label"], "wildcard-adopt")
        self.assertEqual(failure["age_sec"], 60)
        self.assertEqual(self.severity(self.collect()), "warn")

    def test_unknown_durable_failure_fields_are_not_projected(self):
        self.write_failure({
            "failure_code": "strategy_persist_failed",
            "reason": "../../etc/passwd",
            "rc": "1; touch /tmp/x",
            "label": "DO NOT PUBLISH",
            "failed_at": self.now,
        })
        failure = self.collect()["persist"]["last_failure"]
        self.assertEqual(failure["reason"], "unknown")
        self.assertIsNone(failure["rc"])
        self.assertEqual(failure["label"], "unknown")
        self.assertNotIn("DO NOT PUBLISH", json.dumps(self.collect()))

    def test_unhashable_status_fields_are_not_raw(self):
        self.write_status({"result": "fail", "reason": ["push_failed"], "rc": True})
        persist = self.collect()["persist"]
        self.assertEqual(persist["reason"], "unknown")
        self.assertIsNone(persist["rc"])
        self.assertEqual(self.severity(self.collect()), "warn")

    def test_unrelated_failure_code_is_ignored(self):
        self.write_failure({
            "failure_code": "apply_bundle_failed",
            "reason": "push_failed",
            "rc": 1,
            "label": "commit",
            "failed_at": self.now,
        })
        persist = self.collect()["persist"]
        self.assertIsNone(persist["last_failure"])
        self.assertEqual(self.severity(self.collect()), "ok")


if __name__ == "__main__":
    unittest.main()

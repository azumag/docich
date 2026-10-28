"""A detached hanjuku resume claim must reach the runtime health alert (#2870).

The resume plane can be left holding a claim whose owner is gone. The archive
then refuses the next resume while every other signal stays healthy, so the
condition only surfaces if an operator happens to read the diagnostics by hand.
These tests pin the two places that make it visible (collector severity and the
runtime summary) and pin the quiet normal transitions, so the alert cannot
start firing on claimed/queued/verified resumes.
"""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"
SUMMARIZER = ROOT / "ops" / "vm_actions" / "summarize_runtime.py"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


QUIET_WORKERS = {
    "required_down": [],
    "required_stale": [],
    "paused": [],
    "unregistered": [],
    "duplicates": [],
    "zombies": [],
}
QUIET_QUEUES = {"stale_locks": 0}
QUIET_AI = {"all_failed": 0, "queue_giveups": 0}
QUIET_IMPROVEMENT = {"stale": False, "retry_pending": False}
DETACHED_TOKEN = "hanjuku_resume_detached_claim"


class CollectorSeverityTests(unittest.TestCase):
    def setUp(self):
        self.collector = load(COLLECTOR, "collect_diagnostics_detached_claim")

    def severity(self, resume, workers=None):
        return self.collector._severity(
            workers or QUIET_WORKERS,
            QUIET_QUEUES,
            QUIET_AI,
            QUIET_IMPROVEMENT,
            {"corner_rotation": {"status": "waiting"}},
            resume,
        )

    def test_detached_claim_raises_warn(self):
        self.assertEqual(self.severity({"status": "detached_claim"}), "warn")

    def test_normal_resume_states_stay_quiet(self):
        for status in ("prepared", "queued", "claimed", "verified", "unavailable"):
            with self.subTest(status=status):
                self.assertEqual(self.severity({"status": status}), "ok")

    def test_missing_resume_section_stays_quiet(self):
        self.assertEqual(self.severity(None), "ok")
        # a non-dict projection (older payload) must not crash the collector
        self.assertEqual(self.severity("detached_claim"), "ok")

    def test_critical_workers_still_win(self):
        workers = dict(QUIET_WORKERS, required_down=["prediction_worker"])
        self.assertEqual(self.severity({"status": "detached_claim"}, workers), "critical")


class RuntimeSummaryTests(unittest.TestCase):
    def setUp(self):
        self.mod = load(SUMMARIZER, "summarize_runtime_detached_claim")

    def summarize(self, status, resume):
        data = {
            "status": status,
            "workers": QUIET_WORKERS,
            "queues": QUIET_QUEUES,
            "ai": dict(QUIET_AI, recent_events=[]),
            "improvement": QUIET_IMPROVEMENT,
        }
        if resume is not None:
            data["hanjuku_resume"] = resume
        return self.mod.summarize(data)

    def test_ok_payload_is_promoted_to_warn(self):
        severity, summary = self.summarize("ok", {"status": "detached_claim"})
        self.assertEqual(severity, "warn")
        self.assertIn(f"{DETACHED_TOKEN}=1", summary)

    def test_verified_resume_stays_ok(self):
        severity, summary = self.summarize("ok", {"status": "verified"})
        self.assertEqual(severity, "ok")
        self.assertIn(f"{DETACHED_TOKEN}=0", summary)

    def test_missing_resume_section_stays_ok(self):
        severity, summary = self.summarize("ok", None)
        self.assertEqual(severity, "ok")
        self.assertIn(f"{DETACHED_TOKEN}=0", summary)

    def test_existing_warn_and_critical_are_not_downgraded(self):
        self.assertEqual(self.summarize("warn", {"status": "detached_claim"})[0], "warn")
        self.assertEqual(self.summarize("critical", {"status": "detached_claim"})[0], "critical")


if __name__ == "__main__":
    unittest.main()

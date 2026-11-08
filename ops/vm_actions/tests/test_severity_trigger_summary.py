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


class SeverityTriggerTests(unittest.TestCase):
    """#3658: every warn branch must be named in the public summary.

    Before this change a ``severity=warn`` payload could leave every public
    actionable counter at zero (e.g. ``corner_rotation`` recovery or an
    ``improvement.persist`` failure), so the runtime health alert could not
    explain what fired. ``severity_triggers`` now carries a fixed-enum bool
    per branch, computed before the budget trims evidence sections.
    """

    def setUp(self):
        self.collector = load(COLLECTOR, "collect_diagnostics_severity_triggers")
        self.summarizer = load(SUMMARIZER, "summarize_runtime_severity_triggers")

    @staticmethod
    def quiet_inputs():
        workers = {
            "required_down": [],
            "required_stale": [],
            "paused": [],
            "duplicates": [],
            "zombies": [],
            "unregistered": [],
            "pause_ownership": {},
            "unregistered_health": {},
        }
        queues = {"stale_locks": 0}
        ai = {"all_failed": 0, "queue_giveups": 0}
        improvement = {"stale": False, "retry_pending": False}
        return workers, queues, ai, improvement

    def report(self, workers=None, queues=None, ai=None, improvement=None, **kwargs):
        w, q, a, i = self.quiet_inputs()
        return self.collector._severity_report(
            workers or w, queues or q, ai or a, improvement or i, **kwargs
        )

    def diagnostics(self, triggers=None):
        w, q, a, i = self.quiet_inputs()
        data = {
            "status": "warn",
            "workers": w,
            "queues": q,
            "ai": {**a, "recent_events": []},
            "improvement": i,
        }
        if triggers is not None:
            data["severity_triggers"] = triggers
        return data

    def test_enum_sync(self):
        self.assertEqual(
            self.collector.SEVERITY_TRIGGERS,
            self.summarizer.SEVERITY_TRIGGERS,
        )

    def test_ok_has_no_triggers(self):
        report = self.report()
        self.assertEqual(report["status"], "ok")
        self.assertFalse(any(report["triggers"].values()))
        self.assertEqual(set(report["triggers"]), set(self.collector.SEVERITY_TRIGGERS))

    def test_corner_rotation_warn_names_trigger(self):
        report = self.report(corners={"corner_rotation": {"status": "recovery_required"}})
        self.assertEqual(report["status"], "warn")
        self.assertTrue(report["triggers"]["corner_rotation_recovery_required"])
        self.assertEqual(sum(report["triggers"].values()), 1)

    def test_persist_fail_names_trigger(self):
        _, _, _, i = self.quiet_inputs()
        i["persist"] = {"result": "fail"}
        report = self.report(improvement=i)
        self.assertEqual(report["status"], "warn")
        self.assertTrue(report["triggers"]["improvement_persist_fail"])

    def test_persist_clone_disappeared_requires_activity(self):
        _, _, _, i = self.quiet_inputs()
        i["running"] = True
        i["persist"] = {"clone_present": False}
        report = self.report(improvement=i)
        self.assertTrue(report["triggers"]["improvement_persist_clone_disappeared"])
        i["running"] = False
        report = self.report(improvement=i)
        self.assertFalse(report["triggers"]["improvement_persist_clone_disappeared"])

    def test_clone_missing_failure_idle_not_actionable(self):
        _, _, _, i = self.quiet_inputs()
        i["persist"] = {"last_failure": {"reason": "clone_missing"}}
        report = self.report(improvement=i)
        self.assertEqual(report["status"], "ok")
        i["running"] = True
        report = self.report(improvement=i)
        self.assertTrue(report["triggers"]["improvement_persist_failure"])

    def test_required_worker_down_trigger_and_critical(self):
        w, _, _, _ = self.quiet_inputs()
        w["required_down"] = ["some_worker"]
        report = self.report(workers=w)
        self.assertEqual(report["status"], "critical")
        self.assertTrue(report["triggers"]["required_worker_down"])
        self.assertFalse(report["triggers"]["required_worker_stale"])

    def test_radio_prepass_all_failed_not_actionable(self):
        # docich#3661: radio_prepass all_failed iterations are structural and
        # must not raise WARN; other components still do.
        _, _, a, _ = self.quiet_inputs()
        a["all_failed"] = 3
        a["all_failed_components"] = {"radio_prepass": 3}
        report = self.report(ai=a)
        self.assertEqual(report["status"], "ok")
        a["all_failed"] = 4
        report = self.report(ai=a)
        self.assertEqual(report["status"], "warn")
        self.assertTrue(report["triggers"]["ai_all_failed"])

    def test_severity_wrapper_matches(self):
        w, q, a, i = self.quiet_inputs()
        self.assertEqual(
            self.collector._severity(w, q, a, i, corners={"corner_rotation": {"status": "recovery_required"}}),
            "warn",
        )

    def test_summary_projects_trigger_booleans(self):
        triggers = {name: False for name in self.collector.SEVERITY_TRIGGERS}
        triggers["corner_rotation_recovery_required"] = True
        severity, summary = self.summarizer.summarize(self.diagnostics(triggers))
        self.assertEqual(severity, "warn")
        self.assertIn("severity_triggers_reported=1", summary)
        self.assertIn("severity_trigger_corner_rotation_recovery_required=1", summary)
        self.assertIn("severity_trigger_improvement_persist_fail=0", summary)

    def test_summary_handles_missing_triggers(self):
        # Older collectors do not emit ``severity_triggers``; the projection
        # must fail closed to reported=0 with every trigger at 0 rather than
        # crashing or inventing names.
        severity, summary = self.summarizer.summarize(self.diagnostics())
        self.assertIn("severity_triggers_reported=0", summary)
        self.assertIn("severity_trigger_worker_zombies=0", summary)

    def test_summary_ignores_malformed_triggers(self):
        severity, summary = self.summarizer.summarize(
            self.diagnostics(triggers={"corner_rotation_recovery_required": "yes"})
        )
        self.assertIn("severity_trigger_corner_rotation_recovery_required=0", summary)

    def test_multiple_warn_triggers_are_all_reported(self):
        _, _, _, improvement = self.quiet_inputs()
        improvement["retry_pending"] = True
        improvement["persist"] = {"result": "fail"}
        report = self.report(
            improvement=improvement,
            corners={"corner_rotation": {"status": "recovery_required"}},
        )
        self.assertEqual(report["status"], "warn")
        expected = {
            "corner_rotation_recovery_required",
            "improvement_retry_pending",
            "improvement_persist_fail",
        }
        self.assertEqual(
            {name for name, active in report["triggers"].items() if active},
            expected,
        )
        _, summary = self.summarizer.summarize(self.diagnostics(report["triggers"]))
        for name in expected:
            self.assertIn(f"severity_trigger_{name}=1", summary)

    def test_critical_precedes_simultaneous_warn_triggers(self):
        workers, _, _, _ = self.quiet_inputs()
        workers["required_stale"] = ["worker_a"]
        report = self.report(
            workers=workers,
            corners={"corner_rotation": {"status": "recovery_required"}},
        )
        self.assertEqual(report["status"], "critical")
        self.assertTrue(report["triggers"]["required_worker_stale"])
        self.assertTrue(report["triggers"]["corner_rotation_recovery_required"])

    def test_lifecycle_handover_down_is_warn_not_critical(self):
        workers, _, _, _ = self.quiet_inputs()
        workers["required_down"] = ["worker_a"]
        report = self.report(
            workers=workers,
            lifecycle_pause={"active": True, "waiting_for": "lifecycle_handover"},
        )
        self.assertEqual(report["status"], "warn")
        self.assertTrue(report["triggers"]["required_worker_down"])

if __name__ == "__main__":
    unittest.main()

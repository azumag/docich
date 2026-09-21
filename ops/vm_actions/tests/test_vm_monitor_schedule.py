import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/vm-storage-monitor.yml"


class VmMonitorScheduleTests(unittest.TestCase):
    def test_monitor_has_staggered_schedule_redundancy_without_widening_vm_control(self):
        text = WORKFLOW.read_text()
        schedule = text.split("  schedule:\n", 1)[1].split("  workflow_dispatch:\n", 1)[0]

        self.assertEqual(schedule.count("cron:"), 2)
        self.assertIn("cron: '17 * * * *'", schedule)
        self.assertIn("cron: '47 * * * *'", schedule)
        self.assertIn("github.ref_protected == true", text)
        self.assertIn("diagnostics docich production", text)
        self.assertNotIn("exec docich production", text)


class VmMonitorStorageBreakdownTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text()

    def test_storage_alert_uses_sanitized_fixed_breakdown(self):
        self.assertIn("name: Summarize storage breakdown", self.text)
        self.assertIn("summarize_storage.py", self.text)
        self.assertIn("storage_breakdown_available", self.text)
        self.assertIn("storage_breakdown_incomplete", self.text)
        self.assertIn("STORAGE_CONTEXT", self.text)
        self.assertIn("fixed overlapping categories", self.text)
        self.assertNotIn('cat "$RUNNER_TEMP/runtime-diagnostics.json"', self.text)
        self.assertNotIn("exec docich production", self.text)


class VmMonitorTrackedDriftAlertTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text()

    def test_monitor_alerts_on_tracked_drift_via_read_only_diagnostics(self):
        self.assertIn("name: Assess tracked drift", self.text)
        self.assertIn("tracked_drift", self.text)
        self.assertIn("drift_detected", self.text)
        self.assertIn("name: Update deduplicated tracked drift alert", self.text)
        self.assertIn("steps.drift.outputs.available == '1'", self.text)
        self.assertIn("'[VM deploy] tracked drift alert'", self.text)
        # Deduplication reuses the existing open-issue mechanism.
        self.assertIn("gh issue list --repo \"$GITHUB_REPOSITORY\" --state open", self.text)
        self.assertIn("gh issue create --repo \"$GITHUB_REPOSITORY\" --title \"$title\"", self.text)
        # Read-only: no production exec is added by the drift check.
        self.assertNotIn("exec docich production", self.text)

    def test_tracked_drift_context_uses_fixed_categories_only(self):
        # The context emitted to the public alert is built from a fixed key
        # tuple; no path/file/diff is read or printed.
        for key in (
            "parent_tracked_dirty",
            "owned_submodule_head_mismatch",
            "owned_submodule_tracked_dirty",
            "owned_submodule_missing_or_invalid",
            "scan_complete",
            "unknown",
        ):
            self.assertIn(f"'{key}'", self.text)
        self.assertNotIn("git status --porcelain", self.text)
        # The diagnostics envelope is written to a file for summarizers, never
        # echoed to the log directly.
        self.assertNotIn('echo "$diagnostics_json"', self.text)
        self.assertNotIn("cat \"$RUNNER_TEMP/runtime-diagnostics.json\"", self.text)


class VmMonitorBaselineAlertTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text()

    def test_monitor_alerts_when_production_baseline_is_not_configured(self):
        self.assertIn("name: Assess production baseline", self.text)
        self.assertIn("name: Update deduplicated production baseline alert", self.text)
        self.assertIn("'[VM deploy] production baseline alert'", self.text)
        self.assertIn("baseline_flagged", self.text)
        self.assertIn("baseline_status", self.text)
        self.assertIn("storage-status.json", self.text)
        # Read-only: the baseline check never execs on production.
        self.assertNotIn("exec docich production", self.text)

    def test_baseline_alert_does_not_double_alert_tracked_drift(self):
        # git_clean(root)==false is the tracked drift alert's shape; the
        # baseline alert must skip it to avoid duplicate issues.
        self.assertIn("status != 'configured' and drift_detected != 1", self.text)


if __name__ == "__main__":
    unittest.main()

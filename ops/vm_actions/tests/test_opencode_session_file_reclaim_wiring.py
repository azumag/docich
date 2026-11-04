"""Contract tests for the docich#3140 session-file retention wiring.

The hourly OpenCode retention unit runs the DB pass and then the
session-file pass. These assertions keep the wiring and timeout budget from
drifting.
"""
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
HELPER = ROOT / "ops" / "vm_actions" / "opencode_db_retention_now.sh"
RECLAIM = ROOT / "ops" / "vm_actions" / "opencode_session_file_reclaim.py"
SERVICE = ROOT / "scripts" / "systemd" / "docich-opencode-retention.service"
TIMER = ROOT / "scripts" / "systemd" / "docich-opencode-retention.timer"
README = ROOT / "ops" / "vm_actions" / "README.md"
ADR = ROOT / "docs" / "adr" / "0006-opencode-session-file-retention.md"


class OpenCodeSessionFileWiringTest(unittest.TestCase):
    def test_hourly_helper_runs_the_file_pass_as_dry_run(self):
        text = HELPER.read_text(encoding="utf-8")
        self.assertIn("opencode_session_file_reclaim.py", text)
        self.assertIn(
            'session_state="$root/tmp/state/opencode_session_file_reclaim_state.json"',
            text,
        )
        self.assertIn('session_data="/home/ubuntu/.local/share/opencode"', text)
        self.assertIn("--state-file", text)
        self.assertIn("--manifest-file", text)
        self.assertIn(
            'session_manifest="$root/tmp/state/opencode_session_file_reclaim_manifest.jsonl"',
            text,
        )
        self.assertIn("--status-file", text)
        self.assertIn(
            'session_status_file="$root/tmp/state/opencode_session_file_retention.json"',
            text,
        )
        self.assertIn("--cutoff-hours 24", text)
        self.assertIn("--auto-apply", text)
        self.assertNotIn("--apply", text)
        self.assertNotIn("--expect-plan", text)

    def test_stages_are_independent_and_report_db_first(self):
        text = HELPER.read_text(encoding="utf-8")
        self.assertIn('if _opencode_db_retention_rotate "$retention_days"', text)
        self.assertIn("db_status=$?", text)
        self.assertIn("session_status=$?", text)
        self.assertIn('exit "$db_status"', text)
        self.assertIn('exit "$session_status"', text)
        # The file stage must still be reached when the DB stage fails.
        self.assertLess(
            text.index("db_status=$?"),
            text.index("session_status=$?"),
        )

    def test_unit_budget_covers_db_deadline_plus_file_gate_wait(self):
        service = SERVICE.read_text(encoding="utf-8")
        timer = TIMER.read_text(encoding="utf-8")
        budget = int(service.split("TimeoutStartSec=", 1)[1].splitlines()[0])
        # DB deadline 780s + file gate wait 300s + scan margin.
        self.assertGreaterEqual(budget, 1080)
        self.assertIn("SuccessExitStatus=75", service)
        self.assertIn("OnUnitInactiveSec=1h", timer)
        self.assertIn("docich-opencode-retention.service", timer)

    def test_reclaim_helper_keeps_apply_defaults_off(self):
        text = RECLAIM.read_text(encoding="utf-8")
        self.assertIn('parser.add_argument("--auto-apply", action="store_true")', text)
        self.assertIn('parser.add_argument("--state-file", default="")', text)
        self.assertIn('parser.add_argument("--manifest-file", default="")', text)
        self.assertIn('if auto_apply and not state_file:', text)
        self.assertIn('if auto_apply and not manifest_file:', text)
        self.assertIn('raise ReclaimError("plan_mismatch")', text)

    def test_adr_records_the_reviewed_decisions(self):
        text = ADR.read_text(encoding="utf-8")
        self.assertIn("Status: **Accepted**", text)
        self.assertIn("24h", text)
        self.assertIn("72h", text)
        self.assertIn("2-pass", text)
        self.assertIn("opencode_default_other", text)
        self.assertIn("owner", text)

    def test_readme_documents_the_three_reclaim_helpers(self):
        text = README.read_text(encoding="utf-8")
        for name in (
            "opencode_session_file_reclaim.py",
            "runtime_bulky_reclaim.py",
            "runtime_browser_profile_reclaim.py",
        ):
            self.assertIn(name, text)


if __name__ == "__main__":
    unittest.main()

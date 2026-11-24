"""Contract tests for retiring the Hanjuku audit timer."""
from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]
HOOK = ROOT / "ops" / "vm_actions" / "disable_hanjuku_month_audit_timer.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "vm-operations.yml"


class CleanupTests(unittest.TestCase):
    def test_hook_disables_and_removes_only_the_audit_timer(self):
        text = HOOK.read_text(encoding="utf-8")
        for expected in (
            'service="docich-hanjuku-month-audit.service"',
            'timer="docich-hanjuku-month-audit.timer"',
            'systemctl --user disable --now "$timer"',
            'systemctl --user stop "$service"',
            'rm -f "$unit_dir/$service" "$unit_dir/$timer"',
            "systemctl --user daemon-reload",
        ):
            self.assertIn(expected, text)
        for forbidden in ("sudo", "systemctl --user start", "rm -rf"):
            self.assertNotIn(forbidden, text)

    def test_workflow_runs_the_cleanup_on_production_deploy(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        marker = "- name: Remove retired Hanjuku month audit timer"
        self.assertIn(marker, text)
        block = text.split(marker, 1)[1].split("- name:", 1)[0]
        self.assertIn("steps.auth.outputs.target == 'production'", block)
        self.assertIn("control/ops/vm_actions/disable_hanjuku_month_audit_timer.sh", block)
        self.assertIn('"exec docich production $SHA"', block)
        self.assertNotIn("Ensure Hanjuku month audit timer", text)


if __name__ == "__main__":
    unittest.main()

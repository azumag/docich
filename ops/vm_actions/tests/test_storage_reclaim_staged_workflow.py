from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "emergency-storage-reclaim.yml"


class StagedWorkflowTests(unittest.TestCase):
    def test_old_general_step_unchanged_and_new_step_exists(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("Run general bounded storage maintenance", text)
        self.assertIn("Run staged general storage maintenance diagnostics", text)
        self.assertIn("storage_reclaim_staged.sh", text)

    def test_enum_projection_is_fail_closed(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('grep -E', text)
        self.assertIn('GITHUB_STEP_SUMMARY', text)
        self.assertIn('reason=(none|sudo_denied|command_failed|not_present|skipped)', text)


VM_OPERATIONS = ROOT / ".github" / "workflows" / "vm-operations.yml"


class StagedVmOperationsReclaimTests(unittest.TestCase):
    def test_reclaim_runs_staged_helper_before_legacy_step(self):
        text = VM_OPERATIONS.read_text(encoding="utf-8")
        staged = text.index("Run staged general storage maintenance (reclaim, per-stage enum)")
        legacy = text.index("Run reviewed storage reclaim")
        self.assertLess(staged, legacy)
        self.assertIn("control/ops/vm_actions/storage_reclaim_staged.sh", text)
        self.assertIn("control/ops/vm_actions/storage_reclaim.sh", text)

    def test_legacy_outcome_is_recorded_not_hidden(self):
        text = VM_OPERATIONS.read_text(encoding="utf-8")
        self.assertIn("id: legacy_reclaim", text)
        self.assertIn("steps.legacy_reclaim.outcome", text)
        self.assertIn("GITHUB_STEP_SUMMARY", text)


if __name__ == "__main__":
    unittest.main()

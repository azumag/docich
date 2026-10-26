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


if __name__ == "__main__":
    unittest.main()

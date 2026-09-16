from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/soren-improve-diagnostic.yml"


class SorenImproveDiagnosticWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = WORKFLOW.read_text()

    def test_newer_diagnostic_supersedes_stale_waiting_run(self):
        self.assertIn("group: soren-improve-diagnostic-${{ github.repository }}", self.workflow)
        self.assertIn("cancel-in-progress: true", self.workflow)

    def test_owner_only_vm_boundary_is_retained(self):
        for token in (
            "github.repository_owner == 'azumag'",
            "github.repository_owner_id == '9018513'",
            "github.ref == 'refs/heads/main'",
            "github.ref_protected == true",
            "environment: vm-operations",
        ):
            self.assertIn(token, self.workflow)


if __name__ == "__main__":
    unittest.main()

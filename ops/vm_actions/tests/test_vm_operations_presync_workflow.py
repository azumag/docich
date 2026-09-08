from __future__ import annotations

import unittest
from pathlib import Path


class VmOperationsPresyncWorkflowTests(unittest.TestCase):
    def test_failed_push_deploy_has_bounded_reconcile_then_retry(self):
        workflow = Path(".github/workflows/vm-operations.yml").read_text(encoding="utf-8")
        self.assertIn("id: deploy_initial", workflow)
        self.assertIn("continue-on-error: true", workflow)
        self.assertIn("Reconcile reviewed pre-synced Soren checkout", workflow)
        self.assertIn("github.event_name == 'push'", workflow)
        self.assertIn("steps.deploy_initial.outcome == 'failure'", workflow)
        self.assertIn("status docich production $SHA", workflow)
        self.assertIn("[[ \"$pending_count\" == 0 ]]", workflow)
        self.assertIn("merge-base --is-ancestor \"$old_root\" \"$SHA\"", workflow)
        self.assertIn("reconcile_presynced_submodule.py", workflow)
        self.assertIn("Retry production deploy after exact reconcile", workflow)
        self.assertIn("Fail unresolved deployment", workflow)

    def test_manual_deploy_does_not_auto_reconcile(self):
        workflow = Path(".github/workflows/vm-operations.yml").read_text(encoding="utf-8")
        reconcile_if = next(
            line.strip()
            for index, line in enumerate(workflow.splitlines())
            if "Reconcile reviewed pre-synced Soren checkout" in line
            for line in workflow.splitlines()[index + 1 : index + 5]
            if line.strip().startswith("if:")
        )
        self.assertIn("github.event_name == 'push'", reconcile_if)


if __name__ == "__main__":
    unittest.main()

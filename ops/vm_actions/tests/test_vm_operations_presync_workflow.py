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

    def test_reconcile_and_normalize_also_accept_configured_status(self):
        # #279: deploy_git()'s own projection check can refuse a mismatched
        # live file and roll back cleanly, leaving the VM at "configured"
        # (old baseline) rather than "drift". Both steps must accept either
        # status; only recovery_required/bootstrap_required stay excluded.
        workflow = Path(".github/workflows/vm-operations.yml").read_text(encoding="utf-8")
        occurrences = workflow.count('[[ "$vm_status" == drift || "$vm_status" == configured ]]')
        self.assertEqual(occurrences, 2, "reconcile and normalize steps must both accept configured status")
        self.assertNotIn('[[ "$vm_status" == drift ]]', workflow)

    def test_reconcile_invokes_helper_with_lineage_enabled(self):
        # #279: a named path (overlays/direct_broadcast_overlay.html) was
        # confirmed live-present but matching neither old nor new -- exactly
        # the "reviewed intermediate" shape the bounded lineage opt-in
        # exists for. The 6-arg invocation only converges bytes that are
        # sha256-identical to a real reviewed commit in old_sub..new_sub;
        # anything else still refuses (reconcile_presynced_submodule.py is
        # unchanged, only this call site's argv grew by one).
        workflow = Path(".github/workflows/vm-operations.yml").read_text(encoding="utf-8")
        self.assertIn(
            "printf \"python3 - '%s' '%s' '%s' '%s' '%s' '%s' <<'PY'\\n\" \\\n"
            "              /home/ubuntu/docich \"$old_root\" \"$old_sub\" \"$new_sub\" games/soviet_now lineage",
            workflow,
        )

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

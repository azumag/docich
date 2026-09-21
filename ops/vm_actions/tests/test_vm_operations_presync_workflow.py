from __future__ import annotations

import unittest
from pathlib import Path


class VmOperationsPresyncWorkflowTests(unittest.TestCase):
    def test_reconcile_is_not_used_when_gitlink_unchanged(self):
        workflow = Path(".github/workflows/vm-operations.yml").read_text(encoding="utf-8")
        self.assertIn("reconcile inapplicable: submodule_unchanged", workflow)
        self.assertIn('if [[ "$old_sub" == "$new_sub" ]]', workflow)

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
        # Issue #225: reconcile rides the dedicated narrow gateway operation,
        # never the arbitrary exec channel.
        self.assertIn('"reconcile docich production $SHA"', workflow)
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

    def test_reconcile_bootstraps_reviewed_candidate_object_before_show(self):
        # deploy_git() validates git_clean(root) before its bundle fetch. A
        # pre-existing tracked drift rejection therefore leaves the candidate
        # commit absent from the production object DB. Issue #225 moved the
        # fallback into the dedicated gateway op: it refreshes protected main
        # without touching the worktree, verifies the exact candidate object,
        # and only then reads helpers from that object. The ordering contract
        # now lives in the gateway (shell-free), not in piped workflow text.
        gateway = Path("ops/vm_actions/gateway.py").read_text(encoding="utf-8")
        fetch = "RECONCILE_FETCH_URL"
        verify = "cat-file', '-e'"
        root_helper = "RECONCILE_ROOT_HELPER"
        sub_helper = "RECONCILE_SUBMODULE_HELPER"
        self.assertIn(fetch, gateway)
        self.assertIn(verify, gateway)
        self.assertIn(root_helper, gateway)
        self.assertIn(sub_helper, gateway)
        body = gateway[gateway.index("def reconcile_presynced"):]
        self.assertLess(body.index(fetch), body.index(verify))
        self.assertLess(body.index(verify), body.index(root_helper))
        self.assertLess(body.index(root_helper), body.index(sub_helper))
        workflow = Path(".github/workflows/vm-operations.yml").read_text(encoding="utf-8")
        self.assertNotIn("reconcile_presynced_root.py' | python3", workflow)
        self.assertNotIn("reconcile_presynced_submodule.py' | python3", workflow)

    def test_parent_reconcile_runs_before_submodule_reconcile(self):
        # The fixed argv order is enforced inside the gateway op: the root
        # helper must converge (exit 0) before the submodule helper runs.
        gateway = Path("ops/vm_actions/gateway.py").read_text(encoding="utf-8")
        body = gateway[gateway.index("def reconcile_presynced"):]
        self.assertLess(body.index("root_helper"), body.index("sub_helper"))
        self.assertIn("RECONCILE_SUBMODULE = 'games/soviet_now'", gateway)
        workflow = Path(".github/workflows/vm-operations.yml").read_text(encoding="utf-8")
        self.assertNotIn(
            "show '%s:ops/vm_actions/reconcile_presynced_root.py' | python3 - '%s' '%s'\\n",
            workflow,
        )

    def test_reconcile_invokes_helper_with_lineage_enabled(self):
        # #279: a named path (overlays/direct_broadcast_overlay.html) was
        # confirmed live-present but matching neither old nor new -- exactly
        # the "reviewed intermediate" shape the bounded lineage opt-in
        # exists for. The workflow passes the fixed `lineage` marker as a
        # strict token (never shell), and the gateway forwards it in the
        # submodule helper argv.
        workflow = Path(".github/workflows/vm-operations.yml").read_text(encoding="utf-8")
        self.assertIn(
            "printf '%s %s %s lineage' \"$old_root\" \"$old_sub\" \"$new_sub\"",
            workflow,
        )
        # Helpers must still be read from the exact reviewed candidate object
        # on the VM. The small prefetch command only populates the Git object
        # DB; it does not checkout/reset production tracked files.
        self.assertNotIn("cat control/ops/vm_actions/reconcile_presynced_submodule.py", workflow)
        self.assertNotIn("cat control/ops/vm_actions/reconcile_presynced_root.py", workflow)
        gateway = Path("ops/vm_actions/gateway.py").read_text(encoding="utf-8")
        self.assertIn("*attest", gateway)

    def test_reconcile_attests_reviewed_commits_lost_to_squash(self):
        # #279 follow-up: a reviewed branch commit dropped from
        # old_sub..new_sub by a squash merge is attested in a reviewed
        # control-plane file. The workflow passes only the blobs recorded for
        # the current old/new pair, and only when an exact byte+mode match.
        workflow = Path(".github/workflows/vm-operations.yml").read_text(encoding="utf-8")
        self.assertIn("reviewed_lineage_attestations.json", workflow)
        self.assertIn("control/ops/vm_actions/reviewed_lineage_attestations.json", workflow)
        self.assertIn('item.get("old_sub") != old_sub or item.get("new_sub") != new_sub', workflow)
        self.assertIn('re.fullmatch(r"[A-Za-z0-9._/-]+", rel)', workflow)
        self.assertIn('read -r -a attest_args <<< "$attest_raw"', workflow)
        self.assertIn('for token in "${attest_args[@]}"; do printf \' %s\' "$token"; done', workflow)
        # Tokens travel unquoted over the dedicated op; the gateway
        # re-validates every triple (path/digest/mode) before use.
        gateway = Path("ops/vm_actions/gateway.py").read_text(encoding="utf-8")
        self.assertIn("RECONCILE_ATTEST_PATH_RE", gateway)
        self.assertIn("RECONCILE_ATTEST_SHA_RE", gateway)
        self.assertIn("RECONCILE_ATTEST_MODES", gateway)

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

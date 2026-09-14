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
        self.assertIn("reconcile_presynced_root.py", workflow)
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

    def test_reconcile_bootstraps_reviewed_candidate_object_before_show(self):
        # deploy_git() validates git_clean(root) before its bundle fetch. A
        # pre-existing tracked drift rejection therefore leaves the candidate
        # commit absent from the production object DB. The fallback must fetch
        # protected main without touching the worktree, verify the exact
        # candidate object, and only then read helpers from that object.
        workflow = Path(".github/workflows/vm-operations.yml").read_text(encoding="utf-8")
        fetch = (
            "fetch --no-recurse-submodules --no-tags --force "
            "https://github.com/azumag/docich.git refs/heads/main"
        )
        verify = "cat-file -e '%s^{commit}'"
        root_show = "show '%s:ops/vm_actions/reconcile_presynced_root.py'"
        sub_show = "show '%s:ops/vm_actions/reconcile_presynced_submodule.py'"
        self.assertIn(fetch, workflow)
        self.assertIn(verify, workflow)
        self.assertIn(root_show, workflow)
        self.assertIn(sub_show, workflow)
        self.assertLess(workflow.index(fetch), workflow.index(root_show))
        self.assertLess(workflow.index(verify), workflow.index(root_show))
        self.assertLess(workflow.index(root_show), workflow.index(sub_show))

    def test_parent_reconcile_runs_before_submodule_reconcile(self):
        workflow = Path(".github/workflows/vm-operations.yml").read_text(encoding="utf-8")
        # Four substitutions are required: helper commit, root, old root, new root.
        # Bash printf repeats a format string when extra values are supplied, so
        # dropping the final %s generates a second bogus command with empty args.
        root_cmd = (
            "show '%s:ops/vm_actions/reconcile_presynced_root.py' | python3 - '%s' '%s' '%s'"
        )
        sub_cmd = (
            "show '%s:ops/vm_actions/reconcile_presynced_submodule.py' | python3 - '%s' '%s' '%s' '%s' '%s' lineage"
        )
        self.assertIn(root_cmd, workflow)
        self.assertIn(sub_cmd, workflow)
        self.assertLess(workflow.index(root_cmd), workflow.index(sub_cmd))
        self.assertNotIn(
            "show '%s:ops/vm_actions/reconcile_presynced_root.py' | python3 - '%s' '%s'\\n",
            workflow,
        )

    def test_reconcile_invokes_helper_with_lineage_enabled(self):
        # #279: a named path (overlays/direct_broadcast_overlay.html) was
        # confirmed live-present but matching neither old nor new -- exactly
        # the "reviewed intermediate" shape the bounded lineage opt-in
        # exists for. The helper only converges bytes that are
        # sha256-identical to a real reviewed commit in old_sub..new_sub;
        # anything else still refuses.
        workflow = Path(".github/workflows/vm-operations.yml").read_text(encoding="utf-8")
        self.assertIn(
            "printf \"git -C /home/ubuntu/docich -c core.hooksPath=/dev/null show '%s:ops/vm_actions/reconcile_presynced_submodule.py' | python3 - '%s' '%s' '%s' '%s' '%s' lineage\" \\\n"
            "              \"$SHA\" /home/ubuntu/docich \"$old_root\" \"$old_sub\" \"$new_sub\" games/soviet_now",
            workflow,
        )
        # Helpers must still be read from the exact reviewed candidate object
        # on the VM. The small prefetch command only populates the Git object
        # DB; it does not checkout/reset production tracked files.
        self.assertNotIn("cat control/ops/vm_actions/reconcile_presynced_submodule.py", workflow)
        self.assertNotIn("cat control/ops/vm_actions/reconcile_presynced_root.py", workflow)

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
        self.assertIn('for token in "${attest_args[@]}"; do printf " \'%s\'" "$token"; done', workflow)

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

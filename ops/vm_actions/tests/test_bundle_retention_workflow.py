from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/vm-bundle-retention.yml"


class BundleRetentionWorkflowTests(unittest.TestCase):
    def test_uses_existing_owner_only_exec_and_exact_main_status_gate(self):
        text = WORKFLOW.read_text()
        self.assertIn("workflow_run:", text)
        self.assertIn("workflows: ['VM operations']", text)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", text)
        self.assertIn("github.event.workflow_run.head_branch == 'main'", text)
        self.assertIn("github.event.workflow_run.actor.id == 9018513", text)
        self.assertIn("github.actor_id == 9018513", text)
        self.assertIn("status docich production $sha", text)
        self.assertIn("data.get('status')!='configured' or data.get('sha')!=expected", text)
        self.assertIn("exec docich production $sha", text)
        self.assertIn("python3 ops/vm_actions/prune_bundles.py /etc/azumag-vm-ops.json --repo docich --json", text)

    def test_does_not_add_privileged_or_direct_filesystem_mutation_path(self):
        text = WORKFLOW.read_text()
        self.assertNotIn("sudo ", text)
        self.assertNotIn("rm -", text)
        self.assertNotIn("/home/ubuntu/.local/state/github-vm-ops/bundles", text)
        self.assertNotIn("install_vm_gateway.sh", text)
        self.assertNotIn("ssh-keyscan", text)
        self.assertIn("StrictHostKeyChecking=yes", text)
        self.assertIn("ForwardAgent=no", text)
        self.assertIn("persist-credentials: false", text)


if __name__ == "__main__":
    unittest.main()

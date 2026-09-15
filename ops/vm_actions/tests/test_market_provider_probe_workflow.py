from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/vm-operations.yml"
RUNNER = ROOT / "ops/vm_actions/run_market_provider_probe.sh"


class MarketProviderProbeWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = WORKFLOW.read_text()
        self.runner = RUNNER.read_text()

    def test_probe_lives_inside_canonical_owner_authorized_vm_workflow(self):
        self.assertIn("Authorize owner operation", self.workflow)
        self.assertIn("run: python3 control/ops/vm_actions/authorize.py >/dev/null", self.workflow)
        self.assertIn("github.actor_id == 9018513", self.workflow)
        self.assertIn("github.ref_protected == true", self.workflow)
        self.assertIn("environment: vm-operations", self.workflow)
        self.assertNotIn("Market provider probe\n\non:\n  workflow_run", self.workflow)

    def test_probe_only_runs_after_successful_production_push_deploy(self):
        marker = "- name: Probe Moomoo JP quote readiness after reviewed production deploy"
        self.assertIn(marker, self.workflow)
        tail = self.workflow.split(marker, 1)[1]
        condition = tail.split("shell: bash", 1)[0]
        self.assertIn("github.event_name == 'push'", condition)
        self.assertIn("steps.auth.outputs.operation == 'deploy'", condition)
        self.assertIn("steps.auth.outputs.target == 'production'", condition)
        self.assertIn("steps.deploy_initial.outcome == 'success'", condition)
        self.assertIn("steps.deploy_retry.outcome == 'success'", condition)
        self.assertIn("continue-on-error: true", condition)

    def test_exact_deployed_sha_and_pinned_ssh_are_required(self):
        marker = "- name: Probe Moomoo JP quote readiness after reviewed production deploy"
        tail = self.workflow.split(marker, 1)[1]
        block = tail.split("- name: Adopt reviewed production baseline", 1)[0]
        self.assertIn("SHA: ${{ steps.candidate.outputs.sha }}", block)
        self.assertIn('[[ "$SHA" =~ ^[0-9a-f]{40}$ ]]', block)
        for token in (
            "BatchMode=yes", "IdentitiesOnly=yes", "IdentityAgent=none",
            "ForwardAgent=no", "ClearAllForwardings=yes", "StrictHostKeyChecking=yes",
        ):
            self.assertIn(token, block)
        self.assertIn('"exec docich production $SHA"', block)

    def test_only_fixed_reviewed_runner_is_sent_to_production_exec(self):
        marker = "- name: Probe Moomoo JP quote readiness after reviewed production deploy"
        block = self.workflow.split(marker, 1)[1].split("- name: Adopt reviewed production baseline", 1)[0]
        self.assertIn("cat control/ops/vm_actions/run_market_provider_probe.sh", block)
        self.assertNotIn("inputs.command", block)
        self.assertNotIn("VM_COMMAND", block)
        self.assertIn("gateway withholds stdout/stderr from production exec", self.runner)

    def test_runner_is_quote_only_loopback_and_never_installs_or_trades(self):
        self.assertIn("--host 127.0.0.1", self.runner)
        self.assertIn("--provider moomoo", self.runner)
        self.assertIn("JP.7203,JP.7974", self.runner)
        for forbidden in (
            "pip install", "apt-get", "sudo", "curl ", "wget ", "place_order",
            "unlock_trade", "OpenSecTradeContext", "get_acc_list",
        ):
            self.assertNotIn(forbidden, self.runner)

    def test_exit_code_vocabulary_is_fixed_and_workflow_maps_every_expected_code(self):
        expected = {
            0: "realtime_ready",
            10: "entitled_not_realtime_ready",
            11: "jp_quote_unavailable",
            12: "sdk_unavailable",
            13: "opend_unreachable",
            14: "probe_failed",
            15: "invalid_probe_request",
        }
        marker = "- name: Probe Moomoo JP quote readiness after reviewed production deploy"
        block = self.workflow.split(marker, 1)[1].split("- name: Adopt reviewed production baseline", 1)[0]
        for code, status in expected.items():
            self.assertRegex(self.runner, rf"{re.escape(status)}\) exit {code} ;;")
            self.assertRegex(block, rf"{code}\) status={re.escape(status)} ;;")
        self.assertIn("*) exit 16 ;;", self.runner)
        self.assertIn("transport/status unavailable", block)

    def test_workflow_suppresses_gateway_exec_output(self):
        marker = "- name: Probe Moomoo JP quote readiness after reviewed production deploy"
        block = self.workflow.split(marker, 1)[1].split("- name: Adopt reviewed production baseline", 1)[0]
        self.assertRegex(block, r'"exec docich production \$SHA" >/dev/null')


if __name__ == "__main__":
    unittest.main()

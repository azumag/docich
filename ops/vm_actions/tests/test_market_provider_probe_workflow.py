from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/market-provider-probe.yml"
RUNNER = ROOT / "ops/vm_actions/run_market_provider_probe.sh"


class MarketProviderProbeWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = WORKFLOW.read_text()
        self.runner = RUNNER.read_text()

    def test_workflow_only_follows_successful_owner_main_deploys(self):
        self.assertIn('workflows: ["VM operations"]', self.workflow)
        self.assertIn("workflow_run.conclusion == 'success'", self.workflow)
        self.assertIn("workflow_run.event == 'push'", self.workflow)
        self.assertIn("workflow_run.head_branch == 'main'", self.workflow)
        self.assertIn("workflow_run.actor.id == 9018513", self.workflow)
        self.assertIn("github.repository_owner_id == 9018513", self.workflow)
        self.assertIn("environment: vm-operations", self.workflow)

    def test_exact_deployed_sha_and_pinned_ssh_are_required(self):
        self.assertIn("github.event.workflow_run.head_sha", self.workflow)
        self.assertIn('[[ "$(git rev-parse HEAD)" == "$SHA" ]]', self.workflow)
        for token in (
            "BatchMode=yes", "IdentitiesOnly=yes", "IdentityAgent=none",
            "ForwardAgent=no", "ClearAllForwardings=yes", "StrictHostKeyChecking=yes",
        ):
            self.assertIn(token, self.workflow)
        self.assertIn('"exec docich production $SHA"', self.workflow)

    def test_only_fixed_reviewed_runner_is_sent_to_production_exec(self):
        self.assertIn("cat ops/vm_actions/run_market_provider_probe.sh", self.workflow)
        self.assertNotIn("inputs.", self.workflow)
        self.assertNotIn("workflow_dispatch", self.workflow)
        self.assertIn("production output", self.runner)

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
        for code, status in expected.items():
            self.assertRegex(self.runner, rf"{re.escape(status)}\) exit {code} ;;")
            self.assertRegex(self.workflow, rf"{code}\) status={re.escape(status)} ;;")
        self.assertIn("*) exit 16 ;;", self.runner)
        self.assertIn("unexpected provider probe transport/status code", self.workflow)

    def test_workflow_suppresses_gateway_exec_output(self):
        # Production exec's stdout/stderr is already withheld by the gateway;
        # suppress its fixed JSON envelope too, leaving only our status label.
        self.assertRegex(self.workflow, r'"exec docich production \$SHA" >/dev/null')


if __name__ == "__main__":
    unittest.main()

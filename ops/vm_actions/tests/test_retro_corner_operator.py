import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
AUTH = ROOT / "ops/vm_actions/authorize_retro_corner.py"
SCRIPT = ROOT / "ops/vm_actions/restart_retro_corner.sh"
RECOVER_SCRIPT = ROOT / "ops/vm_actions/recover_retro_corner.sh"
WF = ROOT / ".github/workflows/retro-corner-operator.yml"


class RetroCornerAuthorizeTests(unittest.TestCase):
    def run_auth(self, **overrides):
        env = {
            "GITHUB_REPOSITORY": "azumag/docich",
            "GITHUB_REPOSITORY_ID": "1327276249",
            "GITHUB_REPOSITORY_OWNER": "azumag",
            "GITHUB_REPOSITORY_OWNER_ID": "9018513",
            "GITHUB_ACTOR": "azumag",
            "GITHUB_ACTOR_ID": "9018513",
            "GITHUB_TRIGGERING_ACTOR": "azumag",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_REF_PROTECTED": "true",
            "GITHUB_DEFAULT_BRANCH": "main",
            "GITHUB_WORKFLOW_REF": "azumag/docich/.github/workflows/retro-corner-operator.yml@refs/heads/main",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_SHA": "a" * 40,
            "INPUT_OPERATION": "restart-service",
            "INPUT_CONFIRM": "production",
        }
        env.update(overrides)
        return subprocess.run(["python3", str(AUTH)], capture_output=True, text=True, env=env)

    def test_owner_dispatch_allows_only_fixed_restart(self):
        result = self.run_auth()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout),
            {"operation": "restart-service", "target": "production", "ref": "main"},
        )
        recovered = self.run_auth(INPUT_OPERATION="recover-failed")
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(
            json.loads(recovered.stdout),
            {"operation": "recover-failed", "target": "production", "ref": "main"},
        )
        for operation in ("status", "restart", "exec", "restart-service;id", "", "recover-failed;id"):
            with self.subTest(operation=operation):
                self.assertNotEqual(self.run_auth(INPUT_OPERATION=operation).returncode, 0)

    def test_wrong_owner_confirmation_ref_event_or_workflow_fails_closed(self):
        cases = (
            {"GITHUB_ACTOR": "other", "GITHUB_ACTOR_ID": "42"},
            {"GITHUB_TRIGGERING_ACTOR": "other"},
            {"GITHUB_REF": "refs/heads/feature"},
            {"GITHUB_REF_PROTECTED": "false"},
            {"GITHUB_DEFAULT_BRANCH": "release"},
            {"GITHUB_WORKFLOW_REF": "azumag/docich/.github/workflows/retro-corner-operator.yml@refs/heads/feature"},
            {"GITHUB_EVENT_NAME": "push"},
            {"INPUT_CONFIRM": ""},
            {"GITHUB_SHA": "not-a-sha"},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.assertNotEqual(self.run_auth(**overrides).returncode, 0)


class RetroCornerOperatorPolicyTests(unittest.TestCase):
    def test_script_has_only_the_fixed_user_service_restart(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('unit="docich-retro-corner.service"', text)
        self.assertIn('fifo_timer="docich-game-switch-fifo.timer"', text)
        self.assertIn("docich-game-switch-fifo.service", text)
        self.assertIn('systemctl --user daemon-reload', text)
        self.assertIn('systemctl --user enable --now "$fifo_timer"', text)
        self.assertIn('systemctl --user show "$unit"', text)
        self.assertIn('systemctl --user --no-block restart "$unit"', text)
        self.assertNotIn("$1", text)
        self.assertNotIn("sudo", text)
        self.assertNotIn("docich.service", text)

    def test_fifo_watchdog_units_are_independent_from_the_retro_service(self):
        service = (ROOT / "scripts/systemd/docich-game-switch-fifo.service").read_text(
            encoding="utf-8"
        )
        timer = (ROOT / "scripts/systemd/docich-game-switch-fifo.timer").read_text(
            encoding="utf-8"
        )
        self.assertIn("maintain-fifo", service)
        self.assertIn("TimeoutStartSec=infinity", service)
        self.assertIn("OnActiveSec=30s", timer)
        self.assertIn("OnUnitActiveSec=30s", timer)
        self.assertIn("Unit=docich-game-switch-fifo.service", timer)
        self.assertNotIn("docich-retro-corner.service", service)

    def test_workflow_is_fixed_and_never_exposes_arbitrary_command_input(self):
        text = WF.read_text(encoding="utf-8")
        for required in (
            "options: [restart-service, recover-failed]",
            "github.actor_id == 9018513",
            "github.triggering_actor == 'azumag'",
            "github.ref_protected == true",
            "environment: vm-operations",
            "Require production to equal current protected main",
            "control/ops/vm_actions/authorize_retro_corner.py",
            "control/ops/vm_actions/restart_retro_corner.sh",
            "control/ops/vm_actions/recover_retro_corner.sh",
            "Recover only the failed retro corner slot",
            "if: steps.auth.outputs.operation == 'recover-failed'",
            "StrictHostKeyChecking=yes",
            "ForwardAgent=no",
            "ClearAllForwardings=yes",
            "exec docich production $SHA",
        ):
            self.assertIn(required, text)
        self.assertNotIn("inputs.command", text)
        self.assertNotIn("event.issue.body", text)
        self.assertNotIn("pull_request_target", text)

    def test_failed_recovery_script_restarts_only_the_corner_unit(self):
        text = RECOVER_SCRIPT.read_text(encoding="utf-8")
        self.assertIn('unit="docich-retro-corner.service"', text)
        self.assertIn('systemctl --user show "$unit"', text)
        self.assertIn('systemctl --user --no-block restart "$unit"', text)
        self.assertNotIn("docich.service", text)
        self.assertNotIn("$1", text)
        self.assertNotIn("sudo", text)


if __name__ == "__main__":
    unittest.main()

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
AUTH = ROOT / "ops/vm_actions/authorize_corner_rotation.py"
LEGACY_AUTH = ROOT / "ops/vm_actions/authorize_retro_corner.py"
SCRIPT = ROOT / "ops/vm_actions/restart_corner_rotation.sh"
RECOVER_SCRIPT = ROOT / "ops/vm_actions/recover_corner_rotation.sh"
WF = ROOT / ".github/workflows/corner-rotation-operator.yml"
LEGACY_WF = ROOT / ".github/workflows/retro-corner-operator.yml"

CANONICAL_REF = (
    "azumag/docich/.github/workflows/corner-rotation-operator.yml@refs/heads/main"
)
LEGACY_REF = (
    "azumag/docich/.github/workflows/retro-corner-operator.yml@refs/heads/main"
)


class CornerRotationAuthorizeTests(unittest.TestCase):
    def run_auth(self, auth=AUTH, **overrides):
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
            "GITHUB_WORKFLOW_REF": CANONICAL_REF,
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_SHA": "a" * 40,
            "INPUT_OPERATION": "restart-service",
            "INPUT_CONFIRM": "production",
        }
        env.update(overrides)
        return subprocess.run(["python3", str(auth)], capture_output=True, text=True, env=env)

    def test_owner_dispatch_allows_only_fixed_operations(self):
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

    def test_both_reviewed_workflow_paths_are_accepted_by_both_scripts(self):
        for auth in (AUTH, LEGACY_AUTH):
            for workflow_ref in (CANONICAL_REF, LEGACY_REF):
                with self.subTest(auth=auth.name, workflow_ref=workflow_ref):
                    result = self.run_auth(auth, GITHUB_WORKFLOW_REF=workflow_ref)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_wrong_owner_confirmation_ref_event_or_workflow_fails_closed(self):
        cases = (
            {"GITHUB_ACTOR": "other", "GITHUB_ACTOR_ID": "42"},
            {"GITHUB_TRIGGERING_ACTOR": "other"},
            {"GITHUB_REF": "refs/heads/feature"},
            {"GITHUB_REF_PROTECTED": "false"},
            {"GITHUB_DEFAULT_BRANCH": "release"},
            {"GITHUB_WORKFLOW_REF": "azumag/docich/.github/workflows/other.yml@refs/heads/main"},
            {
                "GITHUB_WORKFLOW_REF": (
                    "azumag/docich/.github/workflows/corner-rotation-operator.yml@refs/heads/feature"
                )
            },
            {"GITHUB_WORKFLOW_REF": ""},
            {"GITHUB_EVENT_NAME": "push"},
            {"INPUT_CONFIRM": ""},
            {"GITHUB_SHA": "not-a-sha"},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.assertNotEqual(self.run_auth(**overrides).returncode, 0)
                self.assertNotEqual(
                    self.run_auth(LEGACY_AUTH, **overrides).returncode, 0
                )


class CornerRotationOperatorPolicyTests(unittest.TestCase):
    def test_restart_script_resolves_the_reviewed_unit_and_never_restarts_shared(self):
        text = SCRIPT.read_text(encoding="utf-8")
        for required in (
            'canonical="docich-corner-rotation.service"',
            'legacy="docich-retro-corner.service"',
            'fifo_timer="docich-game-switch-fifo.timer"',
            "docich-game-switch-fifo.service",
            "ambiguous corner rotation unit state",
            "no corner rotation service unit found",
            "systemctl --user daemon-reload",
            'systemctl --user enable --now "$fifo_timer"',
            'systemctl --user show "$unit"',
            'systemctl --user --no-block restart "$unit"',
            "refusing to write a unit through a symlink",
        ):
            self.assertIn(required, text)
        self.assertNotIn("$@", text)
        self.assertNotIn("eval ", text)
        self.assertNotIn("sudo", text)
        self.assertNotIn("docich.service", text)
        self.assertNotIn("systemctl --user restart", text)

    def test_recover_script_resolves_the_reviewed_unit_and_never_restarts_shared(self):
        text = RECOVER_SCRIPT.read_text(encoding="utf-8")
        for required in (
            'canonical="docich-corner-rotation.service"',
            'legacy="docich-retro-corner.service"',
            "ambiguous corner rotation unit state",
            "no corner rotation service unit found",
            'systemctl --user show "$unit"',
            'systemctl --user --no-block restart "$unit"',
        ):
            self.assertIn(required, text)
        self.assertNotIn("$@", text)
        self.assertNotIn("eval ", text)
        self.assertNotIn("sudo", text)
        self.assertNotIn("docich.service", text)
        self.assertNotIn("systemctl --user restart", text)

    def test_workflow_is_fixed_and_never_exposes_arbitrary_command_input(self):
        text = WF.read_text(encoding="utf-8")
        for required in (
            "options: [restart-service, recover-failed]",
            "github.actor_id == 9018513",
            "github.triggering_actor == 'azumag'",
            "github.ref_protected == true",
            "environment: vm-operations",
            "Require production to equal current protected main",
            "control/ops/vm_actions/authorize_corner_rotation.py",
            "control/ops/vm_actions/restart_corner_rotation.sh",
            "control/ops/vm_actions/recover_corner_rotation.sh",
            "Recover only the failed corner rotation slot",
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

    def test_new_and_legacy_workflows_serialize_on_the_same_concurrency_group(self):
        group = "group: retro-corner-operator-${{ github.repository }}"
        self.assertIn(group, WF.read_text(encoding="utf-8"))
        self.assertIn(group, LEGACY_WF.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

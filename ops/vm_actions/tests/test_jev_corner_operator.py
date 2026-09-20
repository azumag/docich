import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
AUTH = ROOT / "ops/vm_actions/authorize_jev_corner.py"
WF = ROOT / ".github/workflows/jev-corner-operator.yml"


class JevCornerAuthorizeTests(unittest.TestCase):
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
            "GITHUB_WORKFLOW_REF": "azumag/docich/.github/workflows/jev-corner-operator.yml@refs/heads/main",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_SHA": "a" * 40,
            "INPUT_OPERATION": "start",
            "INPUT_CONFIRM": "production",
        }
        env.update(overrides)
        return subprocess.run(["python3", str(AUTH)], capture_output=True, text=True, env=env)

    def test_owner_dispatch_allows_only_fixed_operations(self):
        for operation in ("start", "finish", "status", "recover"):
            with self.subTest(operation=operation):
                result = self.run_auth(INPUT_OPERATION=operation)
                self.assertEqual(result.returncode, 0, result.stderr)
                data = json.loads(result.stdout)
                self.assertEqual(data["operation"], operation)
                self.assertEqual((data["target"], data["ref"]), ("production", "main"))

    def test_wrong_owner_confirmation_ref_or_event_fails_closed(self):
        cases = (
            {"GITHUB_ACTOR": "other", "GITHUB_ACTOR_ID": "42"},
            {"GITHUB_TRIGGERING_ACTOR": "other"},
            {"GITHUB_REF": "refs/heads/feature"},
            {"GITHUB_REF_PROTECTED": "false"},
            {"GITHUB_EVENT_NAME": "push"},
            {"INPUT_CONFIRM": ""},
            {"INPUT_OPERATION": "exec"},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.assertNotEqual(self.run_auth(**overrides).returncode, 0)

    def test_workflow_is_fixed_and_never_exposes_arbitrary_command_input(self):
        text = WF.read_text(encoding="utf-8")
        for required in (
            "github.actor_id == 9018513",
            "github.triggering_actor == 'azumag'",
            "github.ref_protected == true",
            "environment: vm-operations",
            "StrictHostKeyChecking=yes",
            "ForwardAgent=no",
            "ClearAllForwardings=yes",
            "docich-jev-corner",
            "docich.soren-live.toml",
            "steps.auth.outputs.operation == 'start'",
            "steps.auth.outputs.operation == 'finish'",
            "steps.auth.outputs.operation == 'status'",
            "steps.auth.outputs.operation == 'recover'",
        ):
            self.assertIn(required, text)
        self.assertNotIn("inputs.command", text)
        self.assertNotIn("event.issue.body", text)
        self.assertNotIn("pull_request_target", text)


if __name__ == "__main__":
    unittest.main()

"""Fixed owner-only Hanjuku latest-checkpoint resume query contracts."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import tempfile
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]
AUTH = ROOT / "ops" / "vm_actions" / "authorize_hanjuku_resume_query.py"
WORKFLOW = ROOT / ".github" / "workflows" / "hanjuku-resume-query.yml"
RESUME_SCRIPT = ROOT / "ops" / "vm_actions" / "resume_hanjuku_latest.sh"


def load_auth():
    spec = importlib.util.spec_from_file_location("authorize_hanjuku_resume_query", AUTH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class AuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.module = load_auth()
        self.env = {
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
            "GITHUB_WORKFLOW_REF": (
                "azumag/docich/.github/workflows/hanjuku-resume-query.yml@refs/heads/main"
            ),
            "GITHUB_EVENT_NAME": "issue_comment",
            "GITHUB_EVENT_ACTION": "created",
            "GITHUB_SHA": "a" * 40,
            "ISSUE_NUMBER": "1657",
            "COMMENT_AUTHOR": "azumag",
            "COMMENT_AUTHOR_ID": "9018513",
            "COMMENT_BODY": "/hanjuku-resume production",
        }

    def test_only_exact_owner_resume_is_authorized(self):
        self.assertEqual(self.module.authorize(self.env), {
            "operation": "resume-hanjuku-latest",
            "target": "production",
            "ref": "main",
        })
        for body in (
            "/hanjuku-resume",
            "/hanjuku-resume preview",
            "/hanjuku-resume production ",
            "/hanjuku-resume production\n",
            "/hanjuku-resume production; id",
            "/hanjuku-start production",
        ):
            env = dict(self.env, COMMENT_BODY=body)
            with self.subTest(body=body), self.assertRaisesRegex(ValueError, "authorization_denied"):
                self.module.authorize(env)

    def test_identity_ref_and_issue_are_pinned(self):
        changes = {
            "GITHUB_REPOSITORY": "azumag/other",
            "GITHUB_REPOSITORY_ID": "1",
            "GITHUB_REPOSITORY_OWNER_ID": "1",
            "GITHUB_ACTOR_ID": "1",
            "GITHUB_TRIGGERING_ACTOR": "other",
            "GITHUB_REF": "refs/heads/topic",
            "GITHUB_REF_PROTECTED": "false",
            "GITHUB_DEFAULT_BRANCH": "preview",
            "GITHUB_WORKFLOW_REF": "other",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_EVENT_ACTION": "edited",
            "GITHUB_SHA": "not-a-sha",
            "ISSUE_NUMBER": "1339",
            "COMMENT_AUTHOR_ID": "1",
        }
        for key, value in changes.items():
            env = dict(self.env)
            env[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "authorization_denied"):
                self.module.authorize(env)

    def test_cli_outputs_only_fixed_operation(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "output"
            env = dict(os.environ, **self.env, GITHUB_OUTPUT=str(output))
            result = subprocess.run(
                ["python3", str(AUTH)], env=env, text=True, capture_output=True, check=False
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                "operation=resume-hanjuku-latest\ntarget=production\nref=main\n",
            )


class WorkflowTests(unittest.TestCase):
    def test_workflow_is_fixed_owner_only_and_serialized(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        for required in (
            "issue_comment:",
            "github.event.issue.number == 1657",
            "github.event.comment.body == '/hanjuku-resume production'",
            "github.actor_id == 9018513",
            "github.event.comment.user.id == 9018513",
            "environment: vm-operations",
            "persist-credentials: false",
            "authorize_hanjuku_resume_query.py",
            "Require the event to remain current main",
            "Require production to equal event SHA",
            "< control/ops/vm_actions/resume_hanjuku_latest.sh",
            '"exec docich production $SHA"',
            "group: retro-corner-operator-${{ github.repository }}",
            'data.get("output") == "withheld"',
        ):
            self.assertIn(required, text)
        for forbidden in (
            "workflow_dispatch:",
            "pull_request_target:",
            "inputs.command",
            "contents: write",
            "cat \"$work/resume.stdout\"",
            "cat \"$work/resume.stderr\"",
            "recover_corner_rotation.sh",
        ):
            self.assertNotIn(forbidden, text)

    def test_workflow_reports_only_fixed_resume_reason_codes(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        for reason in (
            "queued", "invalid_request", "rotation_disabled", "recovery_required",
            "clock_regressed", "hanjuku_not_eligible", "manual_queue_conflict",
            "state_unavailable", "config_invalid", "queue_rejected",
            "rotation_adapter_state", "rotation_adapter_timestamp",
            "rotation_catalog_mismatch", "rotation_execution_error",
            "rotation_execution_unverified", "rotation_invalid_state",
            "rotation_unexpected", "invalid_value", "missing_key", "type_mismatch",
            "dependency_unavailable", "adapter_failure", "resume_rejected",
            "transport_failure",
        ):
            self.assertIn(reason, text)
        self.assertIn('f"Reason: {reason}"', text)

    def test_existing_resume_helper_is_fixed_latest_checkpoint_only(self):
        text = RESUME_SCRIPT.read_text(encoding="utf-8")
        syntax = subprocess.run(
            ["bash", "-n", str(RESUME_SCRIPT)], text=True, capture_output=True, check=False
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        self.assertIn('main(["--resume-latest"])', text)
        self.assertNotIn("$@", text)


if __name__ == "__main__":
    unittest.main()

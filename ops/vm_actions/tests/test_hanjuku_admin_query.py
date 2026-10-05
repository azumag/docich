import importlib.util
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
AUTH = ROOT / "ops/vm_actions/authorize_hanjuku_admin_query.py"
WORKFLOW = ROOT / ".github/workflows/hanjuku-admin-query.yml"
WORKFLOW_REF = "azumag/docich/.github/workflows/hanjuku-admin-query.yml@refs/heads/main"


def load_auth():
    spec = importlib.util.spec_from_file_location("authorize_hanjuku_admin_query", AUTH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def auth_env(body="/hanjuku-admin-check production", **overrides):
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
        "GITHUB_WORKFLOW_REF": WORKFLOW_REF,
        "GITHUB_EVENT_NAME": "issue_comment",
        "GITHUB_EVENT_ACTION": "created",
        "GITHUB_SHA": "a" * 40,
        "ISSUE_NUMBER": "1752",
        "COMMENT_AUTHOR": "azumag",
        "COMMENT_AUTHOR_ID": "9018513",
        "COMMENT_BODY": body,
    }
    env.update(overrides)
    return env


class HanjukuAdminQueryAuthorizeTests(unittest.TestCase):
    def setUp(self):
        self.module = load_auth()

    def test_exact_owner_commands_are_authorized(self):
        for body, mode in (
            ("/hanjuku-admin-check production", "check"),
            ("/hanjuku-admin-release production", "release"),
        ):
            with self.subTest(body=body):
                self.assertEqual(
                    self.module.authorize(auth_env(body)),
                    {"mode": mode, "target": "production", "ref": "main"},
                )

    def test_identity_event_and_issue_mismatches_are_rejected(self):
        cases = (
            {"GITHUB_REPOSITORY": "azumag/other"},
            {"GITHUB_REPOSITORY_ID": "1"},
            {"GITHUB_REPOSITORY_OWNER": "other"},
            {"GITHUB_REPOSITORY_OWNER_ID": "1"},
            {"GITHUB_ACTOR": "other"},
            {"GITHUB_ACTOR_ID": "1"},
            {"GITHUB_TRIGGERING_ACTOR": "other"},
            {"GITHUB_REF": "refs/heads/topic"},
            {"GITHUB_REF_PROTECTED": "false"},
            {"GITHUB_DEFAULT_BRANCH": "preview"},
            {"GITHUB_WORKFLOW_REF": "azumag/docich/.github/workflows/other.yml@refs/heads/main"},
            {"GITHUB_EVENT_NAME": "workflow_dispatch"},
            {"GITHUB_EVENT_ACTION": "edited"},
            {"GITHUB_SHA": "not-a-sha"},
            {"ISSUE_NUMBER": "1657"},
            {"COMMENT_AUTHOR": "other"},
            {"COMMENT_AUTHOR_ID": "1"},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(ValueError, "authorization_denied"):
                    self.module.authorize(auth_env(**overrides))

    def test_only_exact_fixed_commands_are_accepted(self):
        invalid = (
            "",
            "/hanjuku-admin-check",
            "/hanjuku-admin-check production ",
            " /hanjuku-admin-check production",
            "/hanjuku-admin-check production\n",
            "/hanjuku-admin-release production ",
            "/hanjuku-admin-release production; id",
            "/hanjuku-admin-release staging",
            "/hanjuku-start production",
        )
        for body in invalid:
            with self.subTest(body=body):
                with self.assertRaisesRegex(ValueError, "authorization_denied"):
                    self.module.authorize(auth_env(body))

    def test_cli_writes_only_fixed_outputs(self):
        for body, expected_mode in (
            ("/hanjuku-admin-check production", "check"),
            ("/hanjuku-admin-release production", "release"),
        ):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "output"
                env = dict(os.environ, **auth_env(body), GITHUB_OUTPUT=str(output))
                result = subprocess.run(
                    ["python3", str(AUTH)], env=env, text=True,
                    capture_output=True, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    output.read_text(encoding="utf-8"),
                    f"mode={expected_mode}\ntarget=production\nref=main\n",
                )
                self.assertEqual(
                    json.loads(result.stdout),
                    {"mode": expected_mode, "target": "production", "ref": "main"},
                )


class HanjukuAdminQueryWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_is_fixed_and_serialized(self):
        for marker in (
            "github.event.issue.number == 1752",
            "github.event.comment.body == '/hanjuku-admin-check production'",
            "github.event.comment.body == '/hanjuku-admin-release production'",
            "group: retro-corner-operator-${{ github.repository }}",
            "Require the event to remain current main",
            "Require production to equal event SHA",
            '"diagnostics docich production $SHA"',
            "manual_pending_fingerprint",
            'manual_q.get("status") == "error"',
            'scheduled_q.get("status") == "error"',
            "control/ops/vm_actions/admin_release_hanjuku_manual.sh",
            "control/src/docich/hanjuku_admin_result.py",
            '"exec docich production $SHA"',
            "gh api --method POST repos/azumag/docich/issues/1752/comments",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.text)
        for forbidden in (
            "workflow_dispatch:",
            "inputs.",
            "event.issue.body",
            "COMMENT_BODY:-",
            'cat "$work/expected" >> "$GITHUB_OUTPUT"',
            'echo "$diagnostics_json"',
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.text)

    def test_dynamic_fingerprint_is_bound_to_reviewed_digest_and_stuck_shape(self):
        evidence = self.text[
            self.text.index("- name: Read only the fixed stuck-reservation fingerprint"):
            self.text.index("- name: Check or release only that exact reservation")
        ]
        for marker in (
            'rotation.get("status") == "waiting"',
            'rotation.get("reason") == "manual-request-needs-resume-or-recovery"',
            'rotation.get("manual_pending_corner") == "hanjuku-hero"',
            'rotation.get("manual_pending_owner") == "none"',
            'rotation.get("queued_manual") is True',
            'receipt.get("present") is False',
            'manual_q.get("status") == "error"',
            'scheduled_q.get("status") == "error"',
            'game_switch.get("active_game") != "hanjuku-hero"',
            'fifo.get("queued_count") == 0',
            're.fullmatch(r"[0-9a-f]{64}", fingerprint or "")',
            "expected_fingerprint_digest = ",
            "hashlib.sha256(fingerprint.encode",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, evidence)
        digest_match = re.search(
            r'expected_fingerprint_digest = "([0-9a-f]{64})"', evidence
        )
        self.assertIsNotNone(digest_match)
        self.assertNotIn("GITHUB_OUTPUT", evidence)

    def test_admin_step_rechecks_current_main_and_uses_no_comment_payload(self):
        admin = self.text[
            self.text.index("- name: Check or release only that exact reservation"):
            self.text.index("- name: Verify release left the blocker state")
        ]
        for marker in (
            "ADMIN_RELEASE_EXPECTED",
            "ADMIN_RELEASE_MODE",
            "ADMIN_RELEASE_SHA",
            "ls-remote --exit-code origin refs/heads/main",
            '[[ "$current" == "$SHA" ]]',
        ):
            self.assertIn(marker, admin)
        for forbidden in ("github.event.comment.body", "eval ", "bash -c"):
            self.assertNotIn(forbidden, admin)

    def test_release_has_read_only_postcondition_verification(self):
        verify = self.text[
            self.text.index("- name: Verify release left the blocker state"):
            self.text.index("- name: Return fixed result metadata")
        ]
        self.assertIn("steps.admin.outputs.result == 'admin-released'", verify)
        self.assertIn('"diagnostics docich production $SHA"', verify)
        self.assertIn('rotation.get("manual_pending") is not False', verify)
        self.assertIn('"manual-request-needs-resume-or-recovery"', verify)
        self.assertNotIn("exec docich production", verify)

    def test_reply_never_contains_fingerprint_or_raw_gateway_output(self):
        reply = self.text[self.text.index("- name: Return fixed result metadata"):]
        for forbidden in (
            "manual_pending_fingerprint",
            "expected_fingerprint_digest",
            "ADMIN_RELEASE_EXPECTED",
            "gateway_result",
            "diagnostics_json",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, reply)
        self.assertIn('f"Reason: {result}"', reply)


if __name__ == "__main__":
    unittest.main()

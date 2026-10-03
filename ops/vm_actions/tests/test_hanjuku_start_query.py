import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
AUTH = ROOT / "ops/vm_actions/authorize_hanjuku_start_query.py"
WORKFLOW = ROOT / ".github/workflows/hanjuku-start-query.yml"
START_SCRIPT = ROOT / "ops/vm_actions/start_hanjuku_corner.sh"


def load_auth():
    spec = importlib.util.spec_from_file_location("authorize_hanjuku_start_query", AUTH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class HanjukuStartQueryAuthorizeTests(unittest.TestCase):
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
                "azumag/docich/.github/workflows/hanjuku-start-query.yml@refs/heads/main"
            ),
            "GITHUB_EVENT_NAME": "issue_comment",
            "GITHUB_EVENT_ACTION": "created",
            "GITHUB_SHA": "a" * 40,
            "ISSUE_NUMBER": "1657",
            "COMMENT_AUTHOR": "azumag",
            "COMMENT_AUTHOR_ID": "9018513",
            "COMMENT_BODY": "/hanjuku-start production",
        }

    def test_exact_owner_command_is_authorized(self):
        self.assertEqual(
            self.module.authorize(self.env),
            {"operation": "start-hanjuku", "target": "production", "ref": "main"},
        )

    def test_identity_and_event_mismatches_are_rejected(self):
        changes = {
            "GITHUB_REPOSITORY": "azumag/other",
            "GITHUB_REPOSITORY_ID": "1",
            "GITHUB_REPOSITORY_OWNER": "other",
            "GITHUB_REPOSITORY_OWNER_ID": "1",
            "GITHUB_ACTOR": "other",
            "GITHUB_ACTOR_ID": "1",
            "GITHUB_TRIGGERING_ACTOR": "other",
            "GITHUB_REF": "refs/heads/topic",
            "GITHUB_REF_PROTECTED": "false",
            "GITHUB_DEFAULT_BRANCH": "preview",
            "GITHUB_WORKFLOW_REF": "azumag/docich/.github/workflows/other.yml@refs/heads/main",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_EVENT_ACTION": "edited",
            "GITHUB_SHA": "not-a-sha",
            "ISSUE_NUMBER": "1339",
            "COMMENT_AUTHOR": "other",
            "COMMENT_AUTHOR_ID": "1",
        }
        for key, value in changes.items():
            with self.subTest(key=key):
                env = dict(self.env)
                env[key] = value
                with self.assertRaisesRegex(ValueError, "authorization_denied"):
                    self.module.authorize(env)

    def test_only_the_exact_fixed_command_is_accepted(self):
        invalid = (
            "",
            "/hanjuku-start",
            "/hanjuku-start production ",
            " /hanjuku-start production",
            "/hanjuku-start production\n",
            "/hanjuku-start production soren91",
            "/hanjuku-start production; id",
            "/hanjuku-start staging",
        )
        for body in invalid:
            with self.subTest(body=body):
                env = dict(self.env)
                env["COMMENT_BODY"] = body
                with self.assertRaisesRegex(ValueError, "authorization_denied"):
                    self.module.authorize(env)

    def test_cli_writes_only_fixed_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "output"
            env = dict(os.environ, **self.env, GITHUB_OUTPUT=str(output))
            result = subprocess.run(
                ["python3", str(AUTH)], env=env, text=True, capture_output=True, check=False
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                "operation=start-hanjuku\ntarget=production\nref=main\n",
            )
            self.assertEqual(
                result.stdout.strip(),
                '{"operation":"start-hanjuku","target":"production","ref":"main"}',
            )


class HanjukuStartQueryWorkflowTests(unittest.TestCase):
    def test_workflow_is_fixed_and_serialized_with_the_existing_operator(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("github.event.issue.number == 1657", text)
        self.assertIn("github.event.comment.body == '/hanjuku-start production'", text)
        workflow_preamble, jobs = text.split("\njobs:\n", 1)
        self.assertNotIn("\nconcurrency:\n", workflow_preamble)
        self.assertIn(
            "\n    concurrency:\n"
            "      group: retro-corner-operator-${{ github.repository }}\n"
            "      cancel-in-progress: false\n",
            jobs,
        )
        self.assertIn("Require the event to remain current main", text)
        self.assertIn("Require production to equal event SHA", text)
        self.assertIn("< control/ops/vm_actions/start_hanjuku_corner.sh", text)
        self.assertIn('"exec docich production $SHA"', text)
        self.assertNotIn("workflow_dispatch:", text)
        self.assertNotIn("INPUT_OPERATION", text)

    def test_start_script_waits_for_the_durable_queue_result(self):
        text = START_SCRIPT.read_text(encoding="utf-8")
        syntax = subprocess.run(
            ["bash", "-n", str(START_SCRIPT)], text=True, capture_output=True, check=False
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        self.assertNotIn("systemd-run", text)
        queue_call = '"$DOCICH_HANJUKU_PYTHON" -m docich.hanjuku_corner >/dev/null'
        self.assertIn(queue_call, text)
        self.assertNotIn(queue_call + " &", text)
        self.assertLess(text.index(queue_call), text.index("queued Hanjuku"))


if __name__ == "__main__":
    unittest.main()

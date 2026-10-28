import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
AUTH = ROOT / "ops/vm_actions/authorize_tsuitate_start_query.py"
WORKFLOW = ROOT / ".github/workflows/tsuitate-start-query.yml"
START_SCRIPT = ROOT / "ops/vm_actions/start_tsuitate_corner.sh"
START_MODULE = ROOT / "src/docich/tsuitate_start.py"


def load_auth():
    spec = importlib.util.spec_from_file_location("authorize_tsuitate_start_query", AUTH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TsuitateStartQueryAuthorizeTests(unittest.TestCase):
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
                "azumag/docich/.github/workflows/tsuitate-start-query.yml@refs/heads/main"
            ),
            "GITHUB_EVENT_NAME": "issue_comment",
            "GITHUB_EVENT_ACTION": "created",
            "GITHUB_SHA": "a" * 40,
            "ISSUE_NUMBER": "2938",
            "COMMENT_AUTHOR": "azumag",
            "COMMENT_AUTHOR_ID": "9018513",
            "COMMENT_BODY": "/tsuitate-start production",
        }

    def test_exact_owner_command_is_authorized(self):
        self.assertEqual(
            self.module.authorize(self.env),
            {"operation": "start-tsuitate", "target": "production", "ref": "main"},
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
            "ISSUE_NUMBER": "1657",
            "COMMENT_AUTHOR": "other",
            "COMMENT_AUTHOR_ID": "1",
        }
        for key, value in changes.items():
            with self.subTest(key=key):
                env = dict(self.env)
                env[key] = value
                with self.assertRaisesRegex(ValueError, "authorization_denied"):
                    self.module.authorize(env)

    def test_only_exact_fixed_command_is_accepted(self):
        for body in (
            "",
            "/tsuitate-start",
            "/tsuitate-start production ",
            " /tsuitate-start production",
            "/tsuitate-start production\n",
            "/tsuitate-start production tsuitate-view",
            "/tsuitate-start production; id",
            "/tsuitate-start staging",
        ):
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
                "operation=start-tsuitate\ntarget=production\nref=main\n",
            )


class TsuitateStartQueryStaticContractTests(unittest.TestCase):
    def test_workflow_is_fixed_and_uses_shared_operator_serialization(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("github.event.issue.number == 2938", text)
        self.assertIn("github.event.comment.body == '/tsuitate-start production'", text)
        self.assertIn("group: retro-corner-operator-${{ github.repository }}", text)
        self.assertIn("Require the event to remain current main", text)
        self.assertIn("Require production to equal event SHA", text)
        self.assertIn("< control/ops/vm_actions/start_tsuitate_corner.sh", text)
        self.assertIn('"exec docich production $SHA"', text)
        self.assertNotIn("workflow_dispatch:", text)
        self.assertNotIn("INPUT_OPERATION", text)
        self.assertNotIn('cat "$work/start.stdout"', text)
        self.assertNotIn('cat "$work/start.stderr"', text)

    def test_fixed_script_and_module_cannot_select_an_arbitrary_game(self):
        script = START_SCRIPT.read_text(encoding="utf-8")
        module = START_MODULE.read_text(encoding="utf-8")
        self.assertIn("start-tsuitate accepts no arguments", script)
        self.assertIn("-m docich.tsuitate_start >/dev/null", script)
        self.assertNotIn("systemd-run", script)
        self.assertNotIn("$@", script)
        self.assertIn('queue_manual("tsuitate-view")', module)
        self.assertNotIn("subprocess", module)
        self.assertNotIn("os.system", module)
        compile(module, str(START_MODULE), "exec")
        syntax = subprocess.run(
            ["bash", "-n", str(START_SCRIPT)], text=True, capture_output=True, check=False
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)


if __name__ == "__main__":
    unittest.main()

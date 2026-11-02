import datetime as dt
import importlib
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
AUTH = ROOT / "ops/vm_actions/authorize_tsuitate_start_query.py"
WORKFLOW = ROOT / ".github/workflows/tsuitate-start-query.yml"
START_SCRIPT = ROOT / "ops/vm_actions/start_tsuitate_corner.sh"
BACKGROUND_MODULE = ROOT / "src/docich/tsuitate_background_start.py"


def load_auth():
    spec = importlib.util.spec_from_file_location("authorize_tsuitate_start_query", AUTH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_background():
    src = str(ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    sys.modules.pop("docich.tsuitate_background_start", None)
    return importlib.import_module("docich.tsuitate_background_start")


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


class TsuitateBackgroundStartTests(unittest.TestCase):
    def setUp(self):
        self.module = load_background()
        self.now = dt.datetime(2026, 10, 28, 23, 33, tzinfo=dt.timezone.utc)
        self.run_id = "hourly-2026102823"

    def test_hourly_run_id_is_stable_within_hour(self):
        self.assertEqual(self.module.hourly_run_id(self.now), self.run_id)
        self.assertEqual(
            self.module.hourly_run_id(self.now.replace(minute=59, second=59)), self.run_id
        )

    def test_existing_same_hour_run_is_idempotent(self):
        status = {
            "state": "playing", "runId": self.run_id, "readyForNextRun": False,
        }
        with mock.patch.object(self.module, "_install_control_environment"), mock.patch.object(
            self.module, "call_beta_control", return_value=status
        ) as call:
            self.assertEqual(self.module.start_one_background_match(now=self.now), self.run_id)
        call.assert_called_once_with("status")

    def test_paused_same_hour_run_requires_attention(self):
        status = {"state": "paused", "runId": self.run_id, "readyForNextRun": False}
        with mock.patch.object(self.module, "_install_control_environment"), mock.patch.object(
            self.module, "call_beta_control", return_value=status
        ) as call:
            with self.assertRaisesRegex(
                self.module.BackgroundStartError, "control_state_requires_attention"
            ):
                self.module.start_one_background_match(now=self.now)
        call.assert_called_once_with("status")

    def test_other_active_run_is_never_stopped_or_replaced(self):
        status = {"state": "playing", "runId": "other-run", "readyForNextRun": False}
        with mock.patch.object(self.module, "_install_control_environment"), mock.patch.object(
            self.module, "call_beta_control", return_value=status
        ) as call:
            with self.assertRaisesRegex(self.module.BackgroundStartError, "background_busy"):
                self.module.start_one_background_match(now=self.now)
        call.assert_called_once_with("status")

    def test_ready_worker_starts_exactly_one_hourly_run(self):
        calls = []

        def control(action, run_id=None):
            calls.append((action, run_id))
            if action == "status":
                return {"state": "finished", "runId": "previous", "readyForNextRun": True}
            return {"state": "queued", "runId": run_id, "readyForNextRun": False}

        with mock.patch.object(self.module, "_install_control_environment"), mock.patch.object(
            self.module, "call_beta_control", side_effect=control
        ):
            self.assertEqual(self.module.start_one_background_match(now=self.now), self.run_id)
        self.assertEqual(calls, [("status", None), ("start", self.run_id)])


class TsuitateStartQueryStaticContractTests(unittest.TestCase):
    def test_workflow_is_fixed_and_independent_from_corner_serialization(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("github.event.issue.number == 2938", text)
        self.assertIn("github.event.comment.body == '/tsuitate-start production'", text)
        self.assertIn("group: tsuitate-background-${{ github.repository }}", text)
        self.assertNotIn("group: retro-corner-operator-${{ github.repository }}", text)
        self.assertIn("Require the event to remain current main", text)
        self.assertIn("Require production to equal event SHA", text)
        self.assertIn("< control/ops/vm_actions/start_tsuitate_corner.sh", text)
        self.assertIn('"exec docich production $SHA"', text)
        self.assertIn("background_started", text)
        self.assertNotIn("manual_queue_conflict", text)
        self.assertNotIn("rotation_disabled", text)
        self.assertNotIn("workflow_dispatch:", text)
        self.assertNotIn("INPUT_OPERATION", text)
        self.assertNotIn('cat "$work/start.stdout"', text)
        self.assertNotIn('cat "$work/start.stderr"', text)

    def test_fixed_script_starts_beta_control_without_corner_module(self):
        script = START_SCRIPT.read_text(encoding="utf-8")
        module = BACKGROUND_MODULE.read_text(encoding="utf-8")
        self.assertIn("start-tsuitate accepts no arguments", script)
        self.assertIn("-m docich.tsuitate_background_start >/dev/null", script)
        self.assertNotIn("docich.tsuitate_start", script)
        self.assertNotIn("systemd-run", script)
        self.assertNotIn("$@", script)
        self.assertIn('call_beta_control("status")', module)
        self.assertIn('call_beta_control("start", run_id)', module)
        self.assertNotIn("queue_manual", module)
        self.assertNotIn("CornerRotationManager", module)
        self.assertNotIn("GameSwitch", module)
        compile(module, str(BACKGROUND_MODULE), "exec")
        syntax = subprocess.run(
            ["bash", "-n", str(START_SCRIPT)], text=True, capture_output=True, check=False
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)


if __name__ == "__main__":
    unittest.main()

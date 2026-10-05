import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
AUTH = ROOT / "ops/vm_actions/authorize_weather_start_query.py"
WORKFLOW = ROOT / ".github/workflows/weather-start-query.yml"
START_SCRIPT = ROOT / "ops/vm_actions/start_weather_corner.sh"
START_MODULE = ROOT / "src/docich/weather_start.py"


def load_auth():
    spec = importlib.util.spec_from_file_location("authorize_weather_start_query", AUTH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class WeatherStartQueryAuthorizeTests(unittest.TestCase):
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
                "azumag/docich/.github/workflows/weather-start-query.yml@refs/heads/main"
            ),
            "GITHUB_EVENT_NAME": "issue_comment",
            "GITHUB_EVENT_ACTION": "created",
            "GITHUB_SHA": "a" * 40,
            "ISSUE_NUMBER": "1766",
            "COMMENT_AUTHOR": "azumag",
            "COMMENT_AUTHOR_ID": "9018513",
            "COMMENT_BODY": "/weather-start production",
        }

    def test_exact_owner_command_is_authorized(self):
        self.assertEqual(
            self.module.authorize(self.env),
            {"operation": "start-weather", "target": "production", "ref": "main"},
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
        invalid = (
            "",
            "/weather-start",
            "/weather-start production ",
            " /weather-start production",
            "/weather-start production\n",
            "/weather-start production weather-view",
            "/weather-start production; id",
            "/weather-start staging",
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
                "operation=start-weather\ntarget=production\nref=main\n",
            )
            self.assertEqual(
                result.stdout.strip(),
                '{"operation":"start-weather","target":"production","ref":"main"}',
            )


class WeatherStartQueryWorkflowTests(unittest.TestCase):
    def test_workflow_is_fixed_and_serialized_with_corner_operator(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("github.event.issue.number == 1766", text)
        self.assertIn("github.event.comment.body == '/weather-start production'", text)
        self.assertIn(
            "group: retro-corner-operator-${{ github.repository }}".replace("\\", ""),
            text,
        )
        self.assertIn("Require the event to remain current main", text)
        self.assertIn("Require production to equal event SHA", text)
        self.assertIn("< control/ops/vm_actions/start_weather_corner.sh", text)
        self.assertIn('"exec docich production $SHA"', text)
        self.assertIn('"status docich production $current"', text)
        self.assertIn('"diagnostics docich production $production_sha"', text)
        self.assertIn("ls-remote --exit-code origin refs/heads/main", text)
        self.assertIn("--depth=128", text)
        self.assertIn("refs/heads/main:refs/remotes/origin/main", text)
        self.assertIn('data.get("status") == "configured"', text)
        self.assertIn("merge-base --is-ancestor", text)
        self.assertIn('cat-file -e "$production_sha^{commit}"', text)
        self.assertNotIn("workflow_dispatch:", text)
        self.assertNotIn("INPUT_OPERATION", text)
        self.assertNotIn('echo "$diagnostics_json"', text)
        self.assertNotIn('cat "$work/start.stdout"', text)
        self.assertNotIn('cat "$work/start.stderr"', text)

    def test_workflow_reports_only_bounded_weather_progress(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        for field in (
            "Weather active observed:", "Audio max next_index:", "Terminal:",
            "End reason:", "Audio status:", "Restored:", "Active game after observation:",
            "Soren loop after restore:", "Soren game state:",
            "Soren game state age sec:", "Soren resumed:",
        ):
            self.assertIn(field, text)
        self.assertIn('0 <= index <= 13', text)
        self.assertNotIn("seq 1 216", text)
        self.assertIn("observe_deadline=$((SECONDS + 1080))", text)
        self.assertIn("while (( SECONDS < observe_deadline )); do", text)
        self.assertIn("timeout-minutes: 25", text)
        self.assertIn('"audio_delivery_status"', text)
        self.assertIn('"audio_next_index"', text)
        self.assertIn('details.get("soren_loop")', text)
        self.assertIn('corners.get("soren_game")', text)
        self.assertIn('soren_state in {"MOVE", "DROP", "WAITING"}', text)
        self.assertIn('0 <= soren_age <= 30', text)
        self.assertIn('switch.get("phase") == "ready"', text)
        self.assertIn('runner_alive is True', text)
        self.assertIn('"$soren_resumed" == true', text)
        self.assertNotIn('"requests"', text)
        self.assertNotIn('"item_key"', text)

    def test_start_script_and_module_have_only_fixed_weather_target(self):
        script = START_SCRIPT.read_text(encoding="utf-8")
        module = START_MODULE.read_text(encoding="utf-8")
        syntax = subprocess.run(
            ["bash", "-n", str(START_SCRIPT)], text=True, capture_output=True, check=False
        )
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        self.assertNotIn("systemd-run", script)
        self.assertNotIn(" &", script)
        self.assertIn("-m docich.weather_start >/dev/null", script)
        self.assertIn('queue_manual("weather-view")', module)
        self.assertNotIn("hanjuku-hero", module)


if __name__ == "__main__":
    unittest.main()

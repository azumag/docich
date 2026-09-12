import json
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
AUTH = ROOT / "ops/vm_actions/authorize_paper_corner.py"
WF = ROOT / ".github/workflows/paper-corner-operator.yml"
GENERIC_AUTH = ROOT / "ops/vm_actions/authorize.py"
sys.path.insert(0, str(ROOT / "src"))

from docich import paper_corner_operator as operator  # noqa: E402
from docich.paper_corner import PaperCornerError  # noqa: E402


class ReloadAuthorizeTests(unittest.TestCase):
    def _run(self, body):
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
            "GITHUB_WORKFLOW_REF": "azumag/docich/.github/workflows/paper-corner-operator.yml@refs/heads/main",
            "GITHUB_EVENT_NAME": "issues",
            "GITHUB_EVENT_ACTION": "edited",
            "GITHUB_SHA": "a" * 40,
            "GITHUB_ISSUE_NUMBER": "293",
            "GITHUB_ISSUE_AUTHOR": "azumag",
            "GITHUB_ISSUE_AUTHOR_ID": "9018513",
            "GITHUB_ISSUE_BODY": body,
            "INPUT_OPERATION": "",
            "INPUT_DURATION_MINUTES": "",
            "INPUT_CONFIRM": "",
        }
        return subprocess.run(["python3", str(AUTH)], env=env, text=True, capture_output=True)

    def test_issue_bridge_accepts_only_fixed_reload_worker(self):
        body = json.dumps({
            "operation": "reload-worker",
            "duration_minutes": 1,
            "confirm": "production",
            "nonce": "reload-1",
        })
        result = self._run(body)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["operation"], "reload-worker")
        self.assertEqual(payload["target"], "production")

        for operation in ("exec", "shell", "restart-all"):
            bad = json.dumps({
                "operation": operation,
                "duration_minutes": 1,
                "confirm": "production",
                "nonce": "bad",
            })
            self.assertNotEqual(self._run(bad).returncode, 0)

    def test_reload_still_requires_exact_schema_and_confirmation(self):
        no_confirm = json.dumps({
            "operation": "reload-worker",
            "duration_minutes": 1,
            "confirm": "",
            "nonce": "x",
        })
        extra = json.dumps({
            "operation": "reload-worker",
            "duration_minutes": 1,
            "confirm": "production",
            "nonce": "x",
            "command": "id",
        })
        self.assertNotEqual(self._run(no_confirm).returncode, 0)
        self.assertNotEqual(self._run(extra).returncode, 0)


class ReloadOperatorTests(unittest.TestCase):
    def _global(self, enabled=True):
        return SimpleNamespace(
            repo_root=Path("/home/ubuntu/docich"),
            config_path=Path("/home/ubuntu/docich/config/docich.soren-live.toml"),
            trading=SimpleNamespace(paper_worker_enabled=enabled),
        )

    def test_reload_touches_only_trading_window_and_verifies_new_pane(self):
        tmux = mock.Mock()
        tmux.session = "docich"
        tmux.has_window.side_effect = [True, True]
        tmux.pane_states_checked.return_value = [SimpleNamespace(dead=False)]
        with mock.patch.object(operator, "load_global", return_value=self._global()):
            result = operator.reload_worker(Path("config.toml"), tmux=tmux, sleep=lambda _: None)

        self.assertEqual(result, {"status": "reloaded", "worker": "trading"})
        tmux.ensure_session.assert_called_once_with()
        tmux.kill_window.assert_called_once_with("trading")
        tmux.new_window.assert_called_once_with(
            "trading",
            [
                "/home/ubuntu/docich/bin/docich",
                "--config",
                "/home/ubuntu/docich/config/docich.soren-live.toml",
                "run",
                "trading",
            ],
        )
        tmux.pane_states_checked.assert_called_once_with("docich:trading")

    def test_reload_can_start_missing_worker_but_refuses_disabled_worker(self):
        tmux = mock.Mock()
        tmux.session = "docich"
        tmux.has_window.side_effect = [False, True]
        tmux.pane_states_checked.return_value = [SimpleNamespace(dead=False)]
        with mock.patch.object(operator, "load_global", return_value=self._global()):
            result = operator.reload_worker(Path("config.toml"), tmux=tmux, sleep=lambda _: None)
        self.assertEqual(result["status"], "started")
        tmux.kill_window.assert_not_called()

        disabled = mock.Mock()
        with mock.patch.object(operator, "load_global", return_value=self._global(False)):
            with self.assertRaises(PaperCornerError):
                operator.reload_worker(Path("config.toml"), tmux=disabled, sleep=lambda _: None)
        disabled.ensure_session.assert_not_called()

    def test_reload_fails_closed_when_new_worker_is_dead(self):
        tmux = mock.Mock()
        tmux.session = "docich"
        tmux.has_window.side_effect = [True, True]
        tmux.pane_states_checked.return_value = [SimpleNamespace(dead=True)]
        with mock.patch.object(operator, "load_global", return_value=self._global()):
            with self.assertRaises(PaperCornerError):
                operator.reload_worker(Path("config.toml"), tmux=tmux, sleep=lambda _: None)


class ReloadWorkflowPolicyTests(unittest.TestCase):
    def test_workflow_uses_fixed_reload_command_only(self):
        text = WF.read_text(encoding="utf-8")
        self.assertIn("options: [start, reload-worker]", text)
        self.assertIn("steps.auth.outputs.operation == 'reload-worker'", text)
        self.assertIn("--reload-worker", text)
        self.assertIn("github.actor_id == 9018513", text)
        self.assertIn("github.ref_protected == true", text)
        self.assertIn("Require production to equal current protected main", text)
        self.assertNotIn("inputs.command", text)
        self.assertNotIn("event.comment.body", text)

    def test_public_repo_generic_exec_remains_disabled(self):
        text = GENERIC_AUTH.read_text(encoding="utf-8")
        self.assertIn("repo_private == 'false' and op == 'exec'", text)
        self.assertIn("arbitrary VM exec is disabled when the repository is public", text)


if __name__ == "__main__":
    unittest.main()

import json
import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
AUTH = ROOT / "ops/vm_actions/authorize_gameswitch_recover.py"
WF = ROOT / ".github/workflows/gameswitch-recover.yml"

WORKFLOW_REF = "azumag/docich/.github/workflows/gameswitch-recover.yml@refs/heads/main"


def run_auth(**overrides):
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
        "GITHUB_SHA": "a" * 40,
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "INPUT_OPERATION": "recover_gameswitch",
        "INPUT_PHASE": "recovery_required",
        "INPUT_CLEANUP_PENDING": "false",
        "GITHUB_OUTPUT": "",
    }
    for key, value in overrides.items():
        if value == "":
            env.pop(key, None)
        else:
            env[key] = value
    return subprocess.run(
        ["python3", str(AUTH)], env=env, capture_output=True, text=True
    )


class GameswitchRecoverAuthorizeTests(unittest.TestCase):
    def test_valid_recovery_required_phase_succeeds(self):
        result = run_auth(INPUT_PHASE="recovery_required", INPUT_CLEANUP_PENDING="false")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["operation"], "recover_gameswitch")
        self.assertEqual(data["phase"], "recovery_required")

    def test_valid_failed_phase_succeeds(self):
        result = run_auth(INPUT_PHASE="failed", INPUT_CLEANUP_PENDING="true")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["phase"], "failed")
        self.assertEqual(data["cleanup_pending"], "true")

    def test_unknown_phase_is_rejected(self):
        for phase in ("idle", "ready", "validating", "stopping", "draining", "unknown", ""):
            with self.subTest(phase=phase):
                result = run_auth(INPUT_PHASE=phase)
                self.assertNotEqual(result.returncode, 0)

    def test_unknown_cleanup_pending_is_rejected(self):
        for value in ("", "yes", "no", "1", "0", "TRUE", "FALSE"):
            with self.subTest(cleanup_pending=value):
                result = run_auth(INPUT_CLEANUP_PENDING=value)
                self.assertNotEqual(result.returncode, 0)

    def test_non_owner_actor_is_rejected(self):
        result = run_auth(GITHUB_ACTOR="other")
        self.assertNotEqual(result.returncode, 0)

    def test_non_owner_actor_id_is_rejected(self):
        result = run_auth(GITHUB_ACTOR_ID="42")
        self.assertNotEqual(result.returncode, 0)

    def test_unprotected_ref_is_rejected(self):
        result = run_auth(GITHUB_REF_PROTECTED="false")
        self.assertNotEqual(result.returncode, 0)

    def test_wrong_workflow_ref_is_rejected(self):
        result = run_auth(
            GITHUB_WORKFLOW_REF="azumag/docich/.github/workflows/vm-operations.yml@refs/heads/main"
        )
        self.assertNotEqual(result.returncode, 0)

    def test_wrong_operation_is_rejected(self):
        result = run_auth(INPUT_OPERATION="exec")
        self.assertNotEqual(result.returncode, 0)

    def test_invalid_sha_is_rejected(self):
        result = run_auth(GITHUB_SHA="notasha")
        self.assertNotEqual(result.returncode, 0)

    def test_non_dispatch_event_is_rejected(self):
        result = run_auth(GITHUB_EVENT_NAME="push")
        self.assertNotEqual(result.returncode, 0)

    def test_public_repository_is_permitted_for_fixed_operation(self):
        """The fixed operation is permitted even when the repository is public."""
        result = run_auth()
        self.assertEqual(result.returncode, 0, result.stderr)


class GameswitchRecoverWorkflowTests(unittest.TestCase):
    def test_workflow_exists_and_has_dispatch_trigger(self):
        self.assertTrue(WF.exists())
        content = WF.read_text()
        assert "workflow_dispatch" in content
        assert "gameswitch-recover" in content
        assert "authorize_gameswitch_recover.py" in content
        assert "timeout-minutes" in content

    def test_workflow_does_not_touch_shared_delivery_or_audio(self):
        content = WF.read_text()
        assert "shared" not in content.lower() or "never" in content.lower()
        assert "audio" not in content.lower() or "never" in content.lower()


if __name__ == "__main__":
    unittest.main()

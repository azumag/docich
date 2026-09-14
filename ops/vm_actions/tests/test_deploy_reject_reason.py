from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GATEWAY = ROOT / "ops/vm_actions/gateway.py"
WORKFLOW = ROOT / ".github/workflows/vm-operations.yml"


def load_gateway():
    spec = importlib.util.spec_from_file_location("vm_gateway_reject_reason", GATEWAY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class DeployRejectReasonTests(unittest.TestCase):
    def setUp(self):
        self.gw = load_gateway()

    def test_known_reasons_use_fixed_allowlist_codes(self):
        cases = {
            "bootstrap required": "bootstrap_required",
            "deployment recovery required": "recovery_required",
            "managed projection drift": "managed_projection_drift",
            "tracked VM drift detected": "tracked_vm_drift",
            "bundle missing": "bundle_missing",
            "git deployment verification failed": "deployment_verification_failed",
            "rollback incomplete; unknown drift preserved; recovery required": "rollback_recovery_required",
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(self.gw._deploy_reject_reason(ValueError(message)), expected)

    def test_path_bearing_reasons_never_expose_the_path(self):
        secret_path = "/srv/private/secret-name.txt"
        reason = self.gw._deploy_reject_reason(ValueError(f"projection drift detected: {secret_path}"))
        self.assertEqual(reason, "projection_drift")
        self.assertNotIn(secret_path, reason)

    def test_unknown_exception_text_is_never_exposed(self):
        marker = "TOKEN=do-not-leak /private/path"
        reason = self.gw._deploy_reject_reason(RuntimeError(marker))
        self.assertEqual(reason, "deploy_unknown")
        self.assertNotIn(marker, reason)

    def test_only_valid_production_deploy_command_enables_reason_output(self):
        original = os.environ.get("SSH_ORIGINAL_COMMAND")
        try:
            os.environ["SSH_ORIGINAL_COMMAND"] = f"deploy docich production {'a' * 40}"
            self.assertTrue(self.gw._is_production_deploy_request())
            os.environ["SSH_ORIGINAL_COMMAND"] = f"status docich production {'a' * 40}"
            self.assertFalse(self.gw._is_production_deploy_request())
            os.environ["SSH_ORIGINAL_COMMAND"] = "deploy docich production not-a-sha"
            self.assertFalse(self.gw._is_production_deploy_request())
        finally:
            if original is None:
                os.environ.pop("SSH_ORIGINAL_COMMAND", None)
            else:
                os.environ["SSH_ORIGINAL_COMMAND"] = original

    def test_workflow_explicitly_fails_closed_when_gitlink_is_unchanged(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('if [[ "$old_sub" == "$new_sub" ]]; then', workflow)
        self.assertIn('echo "reconcile_reason=submodule_unchanged" >&2', workflow)
        self.assertNotIn('[[ "$old_sub" != "$new_sub" ]]', workflow)


if __name__ == "__main__":
    unittest.main()

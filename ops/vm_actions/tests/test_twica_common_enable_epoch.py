import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/vm-operations.yml"
HELPER = ROOT / "ops/vm_actions/enable_twica_common_once.sh"
EPOCH = ROOT / "ops/vm_actions/twica_common_enable_epoch"


class TwicaCommonEnableEpochTests(unittest.TestCase):
    def test_helper_is_fixed_enable_with_explicit_confirmations(self):
        text = HELPER.read_text(encoding="utf-8")
        self.assertIn("set -euo pipefail", text)
        self.assertIn("python3 ops/vm_actions/twica_common.py enable", text)
        self.assertIn("--confirm-production", text)
        self.assertIn("--confirm-stream-restart", text)
        self.assertIn("--confirm-idle", text)
        self.assertNotIn("--shared-only", text)

    def test_workflow_enable_is_push_production_epoch_gated(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        marker = "- name: Enable common TwiCa foreground for reviewed runtime epoch"
        self.assertIn(marker, text)
        block = text.split(marker, 1)[1].split(
            "- name: Refresh active Soren91 GAME OPS rails after reviewed Soren update", 1
        )[0]
        self.assertIn("twica_common_enable_epoch", block)
        self.assertIn("enable_twica_common_once.sh", block)
        self.assertIn("github.event_name == 'push'", block)
        self.assertIn("steps.auth.outputs.target == 'production'", block)
        self.assertIn("steps.auth.outputs.operation == 'deploy'", block)

    def test_epoch_is_single_integer_line(self):
        self.assertEqual(EPOCH.read_text(encoding="utf-8"), "1\n")


if __name__ == "__main__":
    unittest.main()

import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
HELPER = ROOT / "ops" / "vm_actions" / "opencode_db_retention_now.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "vm-operations.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "vm-operations-ci.yml"


class OpenCodeRetentionNowContractTest(unittest.TestCase):
    def test_helper_is_fixed_bounded_and_uses_existing_gate(self):
        text = HELPER.read_text(encoding="utf-8")
        self.assertIn('root="/home/ubuntu/soren"', text)
        self.assertIn('default_db="/home/ubuntu/.local/share/opencode/opencode.db"', text)
        self.assertIn('worker_db="$root/tmp/state/xdg_data/opencode/opencode.db"', text)
        self.assertIn("retention_days=1", text)
        self.assertIn('source "$root/lib/opencode_db_retention.sh"', text)
        self.assertIn('_opencode_db_retention_rotate "$retention_days" "$worker_db" "$default_db"', text)
        self.assertEqual(text.count("export OPENCODE_DEFAULT_DB_RETENTION_ENABLED=1"), 1)
        for forbidden in ("sudo ", "systemctl ", "kill ", "rm -", "rm "):
            self.assertNotIn(forbidden, text)

    def test_helper_reads_only_the_retention_opt_out_from_env(self):
        text = HELPER.read_text(encoding="utf-8")
        self.assertIn('key.strip() != "OPENCODE_DEFAULT_DB_RETENTION_ENABLED"', text)
        self.assertNotIn("source \"$env_file\"", text)
        self.assertIn('value not in {"0", "1"}', text)

    def test_push_deploy_runs_bounded_reclaim_then_retention_once_per_epoch(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("Detect one-time critical storage reclaim epoch", text)
        self.assertIn("Run bounded storage reclaim before critical OpenCode VACUUM", text)
        self.assertIn("Retry gated OpenCode retention after bounded reclaim", text)
        self.assertIn("github.event_name == 'push'", text)
        self.assertIn("steps.auth.outputs.operation == 'deploy'", text)
        self.assertIn("steps.auth.outputs.target == 'production'", text)
        self.assertIn(
            'git -C candidate diff --quiet "$BEFORE_SHA" "$SHA" -- '
            'ops/vm_actions/opencode_db_retention_reclaim_epoch',
            text,
        )
        self.assertIn("APPLY=1", text)
        self.assertIn("VOICEVOX_ARCHIVE=0", text)
        self.assertIn("AIVIS_ENGINE=0", text)
        reclaim = text.index("Run bounded storage reclaim before critical OpenCode VACUUM")
        retention = text.index("Retry gated OpenCode retention after bounded reclaim")
        self.assertLess(reclaim, retention)
        self.assertIn('cat control/ops/vm_actions/opencode_db_retention_now.sh |', text)
        self.assertIn('"exec docich production $SHA"', text)

    def test_ci_shell_parses_helper(self):
        text = CI_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("ops/vm_actions/opencode_db_retention_now.sh", text)


if __name__ == "__main__":
    unittest.main()

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "ops" / "vm_actions" / "run_soren91_daily_improve.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "soren91-daily-improve.yml"


class Soren91DailyImproveOpsTests(unittest.TestCase):
    def test_runner_uses_fixed_live_and_persist_paths(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("runtime=/home/ubuntu/soren/soren91", text)
        self.assertIn("persist=/home/ubuntu/soren-persist", text)
        self.assertIn('runner="$runtime/daily_runtime_improve.mjs"', text)
        self.assertIn('state="$runtime/tmp/state/improve_daily.json"', text)
        self.assertNotIn("eval ", text)
        self.assertNotIn("$@", text)

    def test_runner_serializes_with_existing_persist_lock(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('lock="$persist/.git/persist.lock"', text)
        self.assertIn('flock -w 30 9', text)
        self.assertIn('exec node "$runner"', text)

    def test_workflow_runs_after_corner_with_owner_only_gateway(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("cron: '45 9 * * *'", text)  # 18:45 JST
        self.assertIn("environment: vm-operations", text)
        self.assertIn("github.repository_owner_id == '9018513'", text)
        self.assertIn('"exec docich production $SHA"', text)
        self.assertIn("run_soren91_daily_improve.sh", text)
        self.assertIn("group: vm-operations-${{ github.repository }}", text)


if __name__ == "__main__":
    unittest.main()

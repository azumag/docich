from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "emergency-storage-reclaim.yml"
EPOCH = ROOT / "ops" / "vm_actions" / "storage_reclaim_emergency_epoch"


class EmergencyStorageReclaimWorkflowTests(unittest.TestCase):
    def test_critical_tmp_cleanup_runs_before_best_effort_general_maintenance(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        critical = text.index("Reclaim identified stale tmp worktrees")
        general = text.index("Run general bounded storage maintenance")
        after = text.index("Record filesystem state after reclaim")
        self.assertLess(critical, general)
        self.assertLess(general, after)
        self.assertIn("storage_reclaim_critical_tmp.sh", text)
        self.assertIn("continue-on-error: true", text)

    def test_after_snapshot_runs_even_when_a_reclaim_step_fails(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        after = text[text.index("Record filesystem state after reclaim"):]
        self.assertIn("if: ${{ always() }}", after)
        self.assertIn("available_delta_bytes", after)

    def test_snapshot_resolves_configured_production_sha(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertGreaterEqual(text.count("production_sha"), 4)
        self.assertIn('status docich production $production_sha', text)
        self.assertIn("storage_before=", text)
        self.assertIn("storage_after=", text)

    def test_snapshot_reads_same_storage_object_as_vm_monitor(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("data.get('storage') or {}", text)
        self.assertIn("json.loads(sys.argv[1]).get('storage') or {}", text)
        self.assertIn("json.loads(path.read_text()).get('storage') or {}", text)
        self.assertNotIn("get('filesystem')", text)

    def test_epoch_three_triggers_metrics_verified_run(self):
        self.assertEqual(EPOCH.read_text(encoding="utf-8"), "3\n")


if __name__ == "__main__":
    unittest.main()

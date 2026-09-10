import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/vm-storage-monitor.yml"


class VmMonitorScheduleTests(unittest.TestCase):
    def test_monitor_has_staggered_schedule_redundancy_without_widening_vm_control(self):
        text = WORKFLOW.read_text()
        schedule = text.split("  schedule:\n", 1)[1].split("  workflow_dispatch:\n", 1)[0]

        self.assertEqual(schedule.count("cron:"), 2)
        self.assertIn("cron: '17 * * * *'", schedule)
        self.assertIn("cron: '47 * * * *'", schedule)
        self.assertIn("github.ref_protected == true", text)
        self.assertIn("diagnostics docich production", text)
        self.assertNotIn("exec docich production", text)


if __name__ == "__main__":
    unittest.main()

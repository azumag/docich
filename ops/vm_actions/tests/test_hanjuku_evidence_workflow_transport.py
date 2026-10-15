import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
QUERY_WORKFLOW = ROOT / ".github" / "workflows" / "hanjuku-evidence-query.yml"
EXPORT_WORKFLOW = ROOT / ".github" / "workflows" / "hanjuku-evidence-export.yml"
MONITOR_WORKFLOW = ROOT / ".github" / "workflows" / "vm-storage-monitor.yml"


class HanjukuEvidenceWorkflowTransportTests(unittest.TestCase):
    def test_hanjuku_evidence_workflows_use_their_own_concurrency_groups(self):
        query = QUERY_WORKFLOW.read_text(encoding="utf-8")
        export = EXPORT_WORKFLOW.read_text(encoding="utf-8")
        monitor = MONITOR_WORKFLOW.read_text(encoding="utf-8")

        # #1922: a stale queued run in the shared monitor group cancels every
        # later owner-triggered Hanjuku evidence request, so each Hanjuku
        # workflow owns its own group. The scheduled monitor keeps its own
        # group; VM operations stay serialized by the gateway's fixed
        # vm-operations.lock.
        self.assertIn(
            "group: hanjuku-evidence-query-${{ github.repository }}",
            query,
        )
        self.assertNotIn("group: vm-storage-monitor-${{ github.repository }}", query)
        self.assertIn(
            "group: hanjuku-evidence-export-${{ github.repository }}",
            export,
        )
        self.assertNotIn("group: vm-storage-monitor-${{ github.repository }}", export)
        self.assertIn("group: vm-storage-monitor-${{ github.repository }}", monitor)
        self.assertIn("cancel-in-progress: false", query)
        self.assertIn("cancel-in-progress: false", export)
        self.assertIn("cancel-in-progress: false", monitor)

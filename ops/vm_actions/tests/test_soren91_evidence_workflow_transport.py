import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_WORKFLOW = ROOT / ".github" / "workflows" / "soren91-evidence-export.yml"
RETENTION_WORKFLOW = ROOT / ".github" / "workflows" / "vm-bundle-retention.yml"


class EvidenceWorkflowTransportTests(unittest.TestCase):
    def test_production_exec_uses_proven_here_string_transport(self):
        evidence = EVIDENCE_WORKFLOW.read_text(encoding="utf-8")
        retention = RETENTION_WORKFLOW.read_text(encoding="utf-8")

        canonical = '"exec docich production $sha" <<< "$command"'
        self.assertIn(canonical, retention)
        self.assertEqual(
            evidence.count('"exec docich production $SHA" <<< "$command"'),
            3,
        )
        self.assertNotIn("|             ssh", evidence)
        self.assertIn(
            'command="python3 ops/vm_actions/soren91_evidence_export.py prepare $GAMES"',
            evidence,
        )
        self.assertIn(
            'command="python3 ops/vm_actions/soren91_evidence_export.py select $index"',
            evidence,
        )
        self.assertIn(
            "command='python3 ops/vm_actions/soren91_evidence_export.py clear'",
            evidence,
        )


if __name__ == "__main__":
    unittest.main()

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
            'command="python3 ops/vm_actions/soren91_evidence_prepare.py $GAMES"',
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

    def test_prepare_failure_is_caught_inside_errexit_safe_conditional(self):
        evidence = EVIDENCE_WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("set +e", evidence)
        self.assertIn(
            'if ssh "${ssh_args[@]}" "$VM_SSH_USER@$VM_SSH_HOST" "exec docich production $SHA" <<< "$command" >/dev/null; then',
            evidence,
        )
        self.assertIn("prepare_rc=$?", evidence)
        self.assertIn("if (( prepare_rc != 0 )); then", evidence)
        self.assertIn("if chunk_count=\"$(python3 control/ops/vm_actions/extract_soren91_evidence.py append", evidence)
        self.assertIn("extract_rc=$?", evidence)

    def test_diagnostics_transport_failure_is_classified_by_stage(self):
        evidence = EVIDENCE_WORKFLOW.read_text(encoding="utf-8")
        conditional = (
            'if diagnostics_json="$(ssh "${ssh_args[@]}" "$VM_SSH_USER@$VM_SSH_HOST" '
            '"diagnostics docich production $SHA")"; then'
        )
        self.assertEqual(evidence.count(conditional), 3)
        self.assertIn("diagnostics_rc=$?", evidence)
        self.assertIn(
            "Soren91 evidence export failed: prepare_failed_diagnostics_transport_failed",
            evidence,
        )
        self.assertIn(
            "Soren91 evidence export failed: post_prepare_diagnostics_transport_failed",
            evidence,
        )
        self.assertIn(
            "Soren91 evidence export failed: selected_chunk_diagnostics_transport_failed",
            evidence,
        )
        # Keep classification fixed-vocabulary: do not print SSH output or the
        # numeric return code into Actions logs.
        self.assertNotIn("diagnostics_rc=$diagnostics_rc", evidence)


if __name__ == "__main__":
    unittest.main()

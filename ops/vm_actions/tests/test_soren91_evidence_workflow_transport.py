import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_WORKFLOW = ROOT / ".github" / "workflows" / "soren91-evidence-export.yml"
MONITOR_WORKFLOW = ROOT / ".github" / "workflows" / "vm-storage-monitor.yml"
RETENTION_WORKFLOW = ROOT / ".github" / "workflows" / "vm-bundle-retention.yml"


class EvidenceWorkflowTransportTests(unittest.TestCase):
    def test_evidence_export_serializes_with_runtime_monitor(self):
        evidence = EVIDENCE_WORKFLOW.read_text(encoding="utf-8")
        monitor = MONITOR_WORKFLOW.read_text(encoding="utf-8")

        shared_group = "group: vm-storage-monitor-${{ github.repository }}"
        self.assertIn(shared_group, evidence)
        self.assertIn(shared_group, monitor)
        self.assertIn("cancel-in-progress: false", evidence)
        self.assertIn("cancel-in-progress: false", monitor)

    def test_production_exec_uses_proven_here_string_transport(self):
        evidence = EVIDENCE_WORKFLOW.read_text(encoding="utf-8")
        retention = RETENTION_WORKFLOW.read_text(encoding="utf-8")

        canonical = '"exec docich production $sha" <<< "$command"'
        self.assertIn(canonical, retention)
        self.assertEqual(
            evidence.count('"exec docich production $SHA" <<< "$command"'),
            6,
        )
        self.assertNotIn("|             ssh", evidence)
        self.assertIn(
            'command="python3 ops/vm_actions/soren91_evidence_prepare.py $GAMES"',
            evidence,
        )
        self.assertIn(
            "command='python3 ops/vm_actions/collect_diagnostics.py /home/ubuntu/soren >/dev/null 2>&1'",
            evidence,
        )
        self.assertIn("command=':'", evidence)
        self.assertIn(
            'command="python3 ops/vm_actions/probe_gateway_diagnostics.py $SHA"',
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

    def test_post_prepare_collector_probe_is_output_free_and_fixed_vocabulary(self):
        evidence = EVIDENCE_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            "command='python3 ops/vm_actions/collect_diagnostics.py /home/ubuntu/soren >/dev/null 2>&1'",
            evidence,
        )
        self.assertIn("collector_probe_rc=$?", evidence)
        self.assertIn("if (( collector_probe_rc != 0 )); then", evidence)
        self.assertIn("post_prepare_collector_failed", evidence)
        self.assertIn("post_prepare_exec_probe_transport_failed", evidence)
        self.assertNotIn("collector_probe_rc=$collector_probe_rc", evidence)
        self.assertNotIn("echo \"$collector_probe_rc\"", evidence)

    def test_source_gateway_probe_is_output_free_and_fixed_vocabulary(self):
        evidence = EVIDENCE_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            'command="python3 ops/vm_actions/probe_gateway_diagnostics.py $SHA"',
            evidence,
        )
        self.assertIn("source_gateway_probe_rc=$?", evidence)
        self.assertIn("source_gateway_output_too_large", evidence)
        self.assertIn("source_gateway_collector_verification_failed", evidence)
        self.assertIn("source_gateway_unclassified_value_error", evidence)
        self.assertIn("source_gateway_probe_internal_error", evidence)
        self.assertIn(
            "post_prepare_installed_gateway_rejected_after_source_gateway_success",
            evidence,
        )
        self.assertNotIn("source_gateway_probe_rc=$source_gateway_probe_rc", evidence)
        self.assertNotIn('echo "$source_gateway_probe_rc"', evidence)

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
            "Soren91 evidence export failed: post_prepare_installed_gateway_rejected_after_source_gateway_success",
            evidence,
        )
        self.assertIn(
            "Soren91 evidence export failed: selected_chunk_diagnostics_transport_failed",
            evidence,
        )
        self.assertNotIn("diagnostics_rc=$diagnostics_rc", evidence)


if __name__ == "__main__":
    unittest.main()

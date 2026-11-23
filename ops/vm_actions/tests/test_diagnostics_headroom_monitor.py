from pathlib import Path
import unittest
import importlib.util


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/vm-diagnostics-headroom-monitor.yml"
HELPER = ROOT / "ops/vm_actions/diagnostics_headroom.py"
_SPEC = importlib.util.spec_from_file_location("diagnostics_headroom", HELPER)
_HEADROOM = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_HEADROOM)


class DiagnosticsHeadroomMonitorContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_runs_periodically_on_protected_main(self):
        self.assertIn("cron: '37 * * * *'", self.text)
        self.assertIn("github.ref_protected == true", self.text)
        self.assertIn("environment: vm-operations", self.text)

    def test_uses_reviewed_gateway_cap_and_warns_before_exhaustion(self):
        self.assertIn("gateway.DIAGNOSTICS_JSON_MAX", self.text)
        self.assertIn("WARN_PERCENT: '80'", self.text)
        self.assertIn("percent >= warn", self.text)

    def test_alert_includes_the_validated_section_breakdown(self):
        self.assertIn("SECTION_BYTES_SUMMARY: ${{ steps.diagnostics.outputs.section_bytes_summary }}", self.text)
        self.assertIn("- section_bytes: ${SECTION_BYTES_SUMMARY:-unavailable}", self.text)
        self.assertIn("summarize_section_bytes", self.text)
        self.assertIn("validated section names with byte counts", self.text)

    def test_alert_is_deduplicated_and_recovers(self):
        title = "[VM diagnostics] availability/headroom alert"
        self.assertGreaterEqual(self.text.count(title), 2)
        self.assertIn("gh issue edit", self.text)
        self.assertIn("gh issue close", self.text)
        self.assertIn("gh issue create", self.text)

    def test_raw_diagnostics_and_stderr_are_not_published(self):
        self.assertIn('2>"$err"', self.text)
        self.assertNotIn('cat "$RUNNER_TEMP/diagnostics-headroom.json"', self.text)
        self.assertNotIn('cat "$RUNNER_TEMP/diagnostics-headroom.err"', self.text)
        self.assertIn("Section values, raw diagnostics, stderr, paths, credentials, and command output are never published", self.text)


class DiagnosticsHeadroomSummaryTest(unittest.TestCase):
    def test_only_bounded_ascii_names_and_numeric_counts_are_returned(self):
        bad_key = "bad" + chr(10) + "::error::inject"
        document = {"diagnostics": {"meta": {"section_bytes": {
            "top": {
                "corners": 10558,
                "workers": 4000,
                bad_key: 999,
                "queues": "not-a-number",
                "ai": 1200,
                "markets": 1000,
                "orders": 900,
            },
            "others": 100,
            "total": 20000,
        }}}}
        compact, lines = _HEADROOM.summarize_section_bytes(document)
        self.assertIn("corners=10558B (52%)", compact)
        self.assertIn("workers=4000B (20%)", compact)
        self.assertIn("?=999B (4%)", compact)
        self.assertIn("queues=0B (0%)", compact)
        self.assertIn("others=100B", compact)
        self.assertIn("total=20000B", compact)
        self.assertNotIn("inject", compact)
        self.assertNotIn("not-a-number", compact)
        self.assertIn("markets=1000B (5%)", compact)
        self.assertNotIn("orders", compact)
        section_rows = [line for line in lines if line.startswith("  - ") and not line.startswith("  - others:") and not line.startswith("  - total:")]
        self.assertEqual(len(section_rows), 5)

    def test_invalid_byte_counts_are_zeroed(self):
        document = {"diagnostics": {"meta": {"section_bytes": {
            "top": {"boolean": True, "negative": -1, "oversized": 10_000_001, "valid": 1},
            "others": False,
            "total": 100,
        }}}}
        compact, _ = _HEADROOM.summarize_section_bytes(document)
        self.assertIn("boolean=0B (0%)", compact)
        self.assertIn("negative=0B (0%)", compact)
        self.assertIn("oversized=0B (0%)", compact)
        self.assertIn("valid=1B (1%)", compact)
        self.assertIn("others=0B", compact)

    def test_missing_or_malformed_section_accounting_is_unavailable(self):
        for document in ({}, {"diagnostics": {"meta": {"section_bytes": {"top": []}}}}):
            with self.subTest(document=document):
                compact, lines = _HEADROOM.summarize_section_bytes(document)
                self.assertEqual(compact, "unavailable")
                self.assertIn("unavailable", lines[0])


if __name__ == "__main__":
    unittest.main()

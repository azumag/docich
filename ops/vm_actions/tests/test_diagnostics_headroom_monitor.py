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
        self.assertGreaterEqual(self.text.count("- section_bytes: ${SECTION_BYTES_SUMMARY:-unavailable}"), 2)
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
    def test_reports_all_sections_within_the_32_section_bound(self):
        bad_key = "bad" + chr(10) + "::error::inject"
        diagnostics = {
            "corners": "c" * 10558,
            "workers": "w" * 4000,
            bad_key: "b" * 2000,
            "ai": "a" * 1200,
            "markets": "m" * 1000,
            "orders": "o" * 900,
            "storage_breakdown": "s" * 800,
            "host_metrics": "h" * 700,
            "hanjuku_tactical": "t" * 600,
            "prediction": "p" * 500,
            "nethack_history": "n" * 400,
            "miscellaneous": "z" * 300,
            # The collector's embedded map must never be treated as a section.
            "meta": {"section_bytes": {
                "top": {"collector_only": 999999},
                "others": 1,
                "total": 2,
            }},
        }
        compact, lines = _HEADROOM.summarize_section_bytes({"diagnostics": diagnostics})
        for name in (
            "corners", "workers", "ai", "markets", "orders",
            "storage_breakdown", "host_metrics", "hanjuku_tactical",
            "prediction", "nethack_history", "miscellaneous", "meta",
        ):
            self.assertIn(f"{name}=", compact)
        self.assertIn("?=", compact)
        self.assertNotIn("collector_only", compact)
        self.assertNotIn("inject", compact)
        self.assertNotIn("cccc", compact)
        self.assertNotIn("wwww", compact)
        self.assertIn("others=0B", compact)
        self.assertIn("omitted=0", compact)
        self.assertIn("total=", compact)
        section_rows = [
            line for line in lines
            if line.startswith("  - ")
            and not line.startswith("  - others:")
            and not line.startswith("  - omitted_sections:")
            and not line.startswith("  - total:")
        ]
        self.assertEqual(len(section_rows), len(diagnostics))

    def test_section_output_is_capped_and_reports_omitted_count(self):
        diagnostics = {
            f"section_{index:02d}": "x" * (40 - index)
            for index in range(36)
        }
        compact, lines = _HEADROOM.summarize_section_bytes({"diagnostics": diagnostics})
        self.assertIn("section_00=", compact)
        self.assertIn("section_31=", compact)
        self.assertNotIn("section_32=", compact)
        self.assertIn("omitted=4", compact)
        self.assertIn("others=", compact)
        section_rows = [
            line for line in lines
            if line.startswith("  - section_")
        ]
        self.assertEqual(len(section_rows), 32)

    def test_unserializable_section_is_zeroed(self):
        compact, _ = _HEADROOM.summarize_section_bytes({
            "diagnostics": {"broken": object(), "valid": "ok"}
        })
        self.assertIn("broken=0B", compact)
        self.assertIn("valid=", compact)

    def test_missing_or_malformed_diagnostics_are_unavailable(self):
        for document in ({}, {"diagnostics": None}, {"diagnostics": []}):
            with self.subTest(document=document):
                compact, lines = _HEADROOM.summarize_section_bytes(document)
                self.assertEqual(compact, "unavailable")
                self.assertIn("unavailable", lines[0])


if __name__ == "__main__":
    unittest.main()

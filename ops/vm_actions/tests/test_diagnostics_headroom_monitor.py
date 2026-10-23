from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/vm-diagnostics-headroom-monitor.yml"


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
        self.assertIn("Raw diagnostics, stderr, paths, credentials, and command output are never published", self.text)


if __name__ == "__main__":
    unittest.main()

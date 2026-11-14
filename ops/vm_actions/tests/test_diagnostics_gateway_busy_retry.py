"""A busy gateway must not be reported as a stale one, and must be retried.

g1163 2026-11-14 13:56 JST: a queued owner operation held the gateway lock,
``diagnostics`` was rejected with the fixed ``VM operation rejected:
gateway_busy`` reason, and the workflow reported "Installed VM gateway does
not accept diagnostics" -- the wrong cause. The bundle-retention workflow
already retries this exact fixed reason; the diagnostics and live-status
queries must do the same, bounded, so a deploy in flight does not lose them.
"""
from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]
OPS_WORKFLOW = ROOT / ".github" / "workflows" / "vm-operations.yml"
LIVE_STATUS_WORKFLOW = ROOT / ".github" / "workflows" / "hanjuku-live-status.yml"


class DiagnosticsGatewayBusyRetryTests(unittest.TestCase):
    def test_owner_diagnostics_step_retries_only_the_busy_rejection(self):
        text = OPS_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("for attempt in 1 2 3 4; do", text)
        self.assertIn("grep -Fxq 'VM operation rejected: gateway_busy'", text)
        self.assertIn("VM gateway stayed busy for all retries", text)
        # The stale-gateway message must stay for every other rejection.
        self.assertIn("does not accept diagnostics", text)
        # A busy gateway is retried; a too-large payload is never retried.
        self.assertIn("diagnostics_output_too_large", text)

    def test_live_status_query_retries_the_busy_rejection(self):
        text = LIVE_STATUS_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("for attempt in 1 2 3 4; do", text)
        self.assertIn("VM operation rejected: gateway_busy", text)
        self.assertIn("VM gateway stayed busy for all retries", text)


if __name__ == "__main__":
    unittest.main()

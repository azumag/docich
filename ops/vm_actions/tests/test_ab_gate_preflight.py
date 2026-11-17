"""#4412: fail-closed A/B gate in front of live projection writes."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ops.vm_actions import ab_gate_preflight as gate
from ops.vm_actions.tests import _ab_gate_support


class AbGatePreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="vmops-abgate-")
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.root = base / "soren"
        self.root.mkdir()
        self.script = _ab_gate_support.install_clear_gate(self.root)
        self.marker = base / "state" / "ab-gate" / "op.json"
        self.log = io.BytesIO()

    def _check(self):
        return gate.check(self.root, marker=self.marker, log=self.log, operation="test-op")

    def test_clear_allows_without_evidence(self):
        self.assertEqual(self._check(), gate.CLEAR)
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.log.getvalue(), b"")

    def test_active_ab_refuses_and_leaves_evidence(self):
        _ab_gate_support.set_gate_rc(self.root, 1)
        with self.assertRaises(gate.GateRefused) as caught:
            self._check()
        self.assertEqual(caught.exception.outcome, gate.ACTIVE)
        self.assertEqual(caught.exception.reason, "active_ab")
        marker = json.loads(self.marker.read_text(encoding="utf-8"))
        self.assertEqual(marker["schema_version"], 1)
        self.assertEqual(marker["outcome"], gate.ACTIVE)
        self.assertEqual(marker["operation"], "test-op")
        self.assertIn(b"reason=active_ab", self.log.getvalue())

    def test_unreadable_state_refuses_fail_closed(self):
        _ab_gate_support.set_gate_rc(self.root, 2)
        with self.assertRaises(gate.GateRefused) as caught:
            self._check()
        self.assertEqual(caught.exception.outcome, gate.UNKNOWN)
        self.assertEqual(caught.exception.reason, "unknown_state")

    def test_missing_gate_script_refuses(self):
        self.script.unlink()
        with self.assertRaises(gate.GateRefused) as caught:
            self._check()
        self.assertEqual(caught.exception.outcome, gate.UNKNOWN)
        self.assertEqual(caught.exception.reason, "gate_missing")

    def test_missing_root_refuses(self):
        with self.assertRaises(gate.GateRefused) as caught:
            gate.check(Path(self.tmp.name) / "absent", marker=self.marker,
                       log=self.log, operation="test-op")
        self.assertEqual(caught.exception.reason, "root_missing")

    def test_caller_ab_env_cannot_force_clear(self):
        # The gate receives a fixed environment; AB_* overrides in the caller
        # must not be able to redirect state or force a clear verdict.
        self.script.write_text(
            '#!/usr/bin/env bash\n[ -z "${AB_STATE_FILE:-}" ] || exit 1\n'
            '[ -z "${SOREN_AB_ALT_STRATEGY:-}" ] || exit 1\nexit 0\n',
            encoding="utf-8",
        )
        self.script.chmod(0o755)
        with mock.patch.dict(os.environ, {
            "AB_STATE_FILE": "/tmp/synthetic-clear.json",
            "SOREN_AB_ALT_STRATEGY": "synthetic-arm",
        }):
            self.assertEqual(self._check(), gate.CLEAR)

    def test_gate_output_is_never_copied_into_log_or_marker(self):
        self.script.write_text(
            "#!/usr/bin/env bash\necho SYNTHETIC_SECRET_VALUE >&2\nexit 1\n",
            encoding="utf-8",
        )
        self.script.chmod(0o755)
        with self.assertRaises(gate.GateRefused):
            self._check()
        self.assertNotIn(b"SYNTHETIC_SECRET_VALUE", self.log.getvalue())
        self.assertNotIn("SYNTHETIC_SECRET_VALUE", self.marker.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

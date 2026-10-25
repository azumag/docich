from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import gateway_entry as entry


class GatewayEntryReadOnlyLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.lock = self.state / "vm-operations.lock"
        self.lock.touch()
        self.config = self.root / "config.json"
        self.config.write_text("{}")
        self.sha = "a" * 40

    def fake_gateway(self):
        core = types.ModuleType("gateway")
        core.load_config = Mock(return_value={"state": str(self.state)})
        core.parse_command = Mock(side_effect=lambda cfg: tuple(os.environ["SSH_ORIGINAL_COMMAND"].split()))
        core.state_root = Mock(side_effect=lambda cfg: Path(cfg["state"]))
        core.status_result = Mock(return_value={"status": "configured", "sha": self.sha})
        core.diagnostics_result = Mock(return_value={"status": "diagnosed", "sha": self.sha, "diagnostics": {"status": "ok"}})
        core.github_auth_diagnostics = Mock(return_value={"status": "diagnosed", "sha": self.sha, "api_read": "verified"})
        core.reason_code = Mock(return_value="operation_rejected")
        core._log_unexpected_traceback = Mock()
        core.main = Mock(return_value=None)

        def die(message="VM operation rejected"):
            raise SystemExit(message)

        core.die = Mock(side_effect=die)
        return core

    def run_entry(self, core, command):
        stdout = io.StringIO()
        with patch.dict(sys.modules, {"gateway": core}), \
                patch.object(sys, "argv", ["gateway_entry.py", str(self.config)]), \
                patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": command}), \
                redirect_stdout(stdout):
            rc = entry.main()
        return rc, json.loads(stdout.getvalue())

    def test_fixed_read_only_operations_use_entry_dispatch(self):
        cases = (
            ("status", "status_result", "configured"),
            ("diagnostics", "diagnostics_result", "diagnosed"),
            ("github_auth_diagnostics", "github_auth_diagnostics", "diagnosed"),
        )
        for operation, method, expected_status in cases:
            with self.subTest(operation=operation):
                core = self.fake_gateway()
                with patch.object(entry.fcntl, "flock", return_value=None) as flock:
                    rc, payload = self.run_entry(core, f"{operation} docich production {self.sha}")
                self.assertEqual(rc, 0)
                self.assertEqual(payload["status"], expected_status)
                getattr(core, method).assert_called_once()
                core.main.assert_not_called()
                flock.assert_called_once()
                self.assertEqual(flock.call_args.args[1], entry.fcntl.LOCK_SH | entry.fcntl.LOCK_NB)

    def test_read_only_fails_fast_when_writer_owns_operation_lock(self):
        core = self.fake_gateway()
        with patch.object(entry.fcntl, "flock", side_effect=BlockingIOError) as flock:
            with self.assertRaises(SystemExit) as raised:
                self.run_entry(core, f"status docich production {self.sha}")
        self.assertEqual(raised.exception.code, "VM operation rejected: gateway_busy")
        flock.assert_called_once()
        self.assertEqual(flock.call_args.args[1], entry.fcntl.LOCK_SH | entry.fcntl.LOCK_NB)
        core.status_result.assert_not_called()
        core.main.assert_not_called()

    def test_read_only_requests_shared_nonblocking_lock(self):
        core = self.fake_gateway()
        with patch.object(entry.fcntl, "flock", return_value=None) as flock:
            rc, payload = self.run_entry(core, f"status docich production {self.sha}")
        self.assertEqual(rc, 0)
        self.assertEqual(payload["sha"], self.sha)
        self.assertEqual(flock.call_args.args[1], entry.fcntl.LOCK_SH | entry.fcntl.LOCK_NB)
        core.status_result.assert_called_once()
        core.main.assert_not_called()

    def test_mutating_operation_still_uses_legacy_gateway_main(self):
        core = self.fake_gateway()
        stdout = io.StringIO()
        with patch.dict(sys.modules, {"gateway": core}), \
                patch.object(sys, "argv", ["gateway_entry.py", str(self.config)]), \
                patch.dict(os.environ, {"SSH_ORIGINAL_COMMAND": f"exec docich production {self.sha}"}), \
                redirect_stdout(stdout), \
                patch.object(entry.fcntl, "flock") as flock:
            self.assertEqual(entry.main(), 0)
        self.assertEqual(stdout.getvalue(), "")
        flock.assert_not_called()
        core.main.assert_called_once_with()
        core.status_result.assert_not_called()
        core.diagnostics_result.assert_not_called()
        core.github_auth_diagnostics.assert_not_called()


if __name__ == "__main__":
    unittest.main()

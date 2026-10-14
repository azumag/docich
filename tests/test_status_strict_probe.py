"""Regression tests for fail-closed switch-status tmux probes."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.status import _check_session, _check_window  # noqa: E402
from docich.tmux import TmuxError  # noqa: E402


RUNTIME = {
    "runtime_id": "g1-abcdef",
    "generation": 1,
}


class StrictProbeTmux:
    """Emulate tmux APIs that hide connection failures unless strict=True."""

    def window_target_exists(self, target, *, strict=False):
        if strict:
            raise TmuxError("failed to connect to server: Connection refused")
        return False

    def session_target_exists(self, session, *, strict=False):
        if strict:
            raise TmuxError("failed to connect to server: Connection refused")
        return False


class TestStatusStrictProbe(unittest.TestCase):
    def test_window_connection_error_is_unreadable_not_absent(self):
        result = _check_window(StrictProbeTmux(), "game-g1", RUNTIME, "game")
        self.assertIsNone(result["exists"])
        self.assertEqual(result["ownership"], "unreadable")

    def test_session_connection_error_is_unreadable_not_absent(self):
        result = _check_session(StrictProbeTmux(), "docich-game-g1", RUNTIME)
        self.assertIsNone(result["exists"])
        self.assertEqual(result["ownership"], "unreadable")


if __name__ == "__main__":
    unittest.main()

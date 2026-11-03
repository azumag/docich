"""Tests for the common retire marker written at game-switch teardown (#3122)."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from docich.runtime_retirement import (
    RETIRE_MARKER,
    RetireMarkerError,
    write_retire_marker,
)

RUNTIME_ID = "g7-abcdef12"


def _runtime(generation=7):
    return {"runtime_id": RUNTIME_ID, "generation": generation}


class RuntimeRetirementTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.state = Path(self.tempdir.name) / "run-soren-live"
        (self.state / "runtimes" / RUNTIME_ID).mkdir(parents=True)

    def tearDown(self):
        self.tempdir.cleanup()

    def runtime_dir(self):
        return self.state / "runtimes" / RUNTIME_ID

    def test_writes_marker_with_identity_and_timestamp(self):
        self.assertIs(write_retire_marker(
            self.state, _runtime(), retired_at="2026-11-04T00:00:00Z"), True)
        marker = json.loads((self.runtime_dir() / RETIRE_MARKER).read_text())
        self.assertEqual(marker, {
            "schema": 1,
            "runtime_id": RUNTIME_ID,
            "generation": 7,
            "retired_at": "2026-11-04T00:00:00Z",
        })

    def test_existing_marker_is_kept(self):
        self.assertIs(write_retire_marker(
            self.state, _runtime(), retired_at="2026-11-04T00:00:00Z"), True)
        first = (self.runtime_dir() / RETIRE_MARKER).read_bytes()
        self.assertIs(write_retire_marker(
            self.state, _runtime(), retired_at="2026-12-01T00:00:00Z"), False)
        self.assertEqual((self.runtime_dir() / RETIRE_MARKER).read_bytes(), first)

    def test_symlinked_marker_is_rejected(self):
        target = self.runtime_dir() / "elsewhere"
        target.write_text("{}")
        (self.runtime_dir() / RETIRE_MARKER).symlink_to(target)
        with self.assertRaises(RetireMarkerError) as ctx:
            write_retire_marker(self.state, _runtime())
        self.assertEqual(str(ctx.exception), "unsafe_marker")

    def test_hardlinked_marker_is_rejected(self):
        target = self.runtime_dir() / "elsewhere"
        target.write_text("{}")
        os.link(target, self.runtime_dir() / RETIRE_MARKER)
        with self.assertRaises(RetireMarkerError) as ctx:
            write_retire_marker(self.state, _runtime())
        self.assertEqual(str(ctx.exception), "unsafe_marker")

    def test_symlinked_runtime_dir_is_rejected(self):
        real = self.state / "runtimes" / RUNTIME_ID
        alias = self.state / "runtimes" / "g8-abcdef12"
        alias.symlink_to(real)
        with self.assertRaises(RetireMarkerError) as ctx:
            write_retire_marker(self.state, {"runtime_id": "g8-abcdef12", "generation": 8})
        self.assertEqual(str(ctx.exception), "unsafe_runtime_dir")

    def test_invalid_runtime_is_rejected(self):
        for runtime in ({"runtime_id": "not-a-runtime", "generation": 7},
                        {"runtime_id": RUNTIME_ID, "generation": 8},
                        {"runtime_id": RUNTIME_ID, "generation": "7"},
                        None):
            with self.subTest(runtime=runtime):
                with self.assertRaises(RetireMarkerError) as ctx:
                    write_retire_marker(self.state, runtime)
                self.assertEqual(str(ctx.exception), "invalid_runtime")

    def test_missing_runtime_dir_is_rejected(self):
        (self.state / "runtimes" / RUNTIME_ID).rmdir()
        with self.assertRaises(RetireMarkerError) as ctx:
            write_retire_marker(self.state, _runtime())
        self.assertEqual(str(ctx.exception), "runtime_dir_unavailable")

    def test_game_switch_housekeeping_writes_marker(self):
        from types import SimpleNamespace

        from docich.game_switch import GameSwitchCoordinator
        switch = object.__new__(GameSwitchCoordinator)
        switch.store = SimpleNamespace(state_dir=self.state)
        warnings = []
        GameSwitchCoordinator._write_retire_markers(
            switch, [_runtime()], warnings, phase="idle")
        self.assertEqual(warnings, [])
        self.assertTrue((self.runtime_dir() / RETIRE_MARKER).is_file())

    def test_post_terminal_housekeeping_calls_marker_writer(self):
        source = (Path(__file__).resolve().parents[1]
                  / "src" / "docich" / "game_switch.py").read_text(encoding="utf-8")
        marker_call = source.index("self._write_retire_markers(retired, warnings")
        prune_call = source.index("self._prune_hanjuku_logs(warnings)")
        self.assertLess(prune_call, marker_call)


if __name__ == "__main__":
    unittest.main()

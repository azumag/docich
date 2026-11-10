"""A terminal verdict must not lock a runtime that came back to life.

g1163 2026-11-10 17:4x: the emulator died, ``terminal_reason='game_over'`` was
recorded, the game was restarted and restored from a savestate, and every input
was then refused with "Hanjuku terminal evidence holds input" for two hours
while the restored game sat on a defence prompt.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from docich.hanjuku.os_layer.pixels import Frame
from docich import hanjuku_run

IDENTITY = {"game": "hanjuku-hero", "runtime_id": "g999-abcdef01",
            "generation": 999, "lease_id": "lease-terminal-revival"}


def _frame(seed: int) -> Frame:
    return Frame(256, 224, bytes([seed % 251]) * (256 * 224 * 3))


class TerminalRevivalTest(unittest.TestCase):
    def _terminal_state(self, runtime_dir: Path) -> dict:
        state = {"game": "hanjuku-hero", "runtime_id": "g999-abcdef01",
                 "generation": 999, "lease_id": "lease-terminal-revival",
                 "frame_sha256": _frame(1).digest(), "phase": "title",
                 "terminal_reason": "game_over", "terminal_candidate": True,
                 "playing": True, "gameplay_seen": True, "name_entered": True,
                 "title_count": 3, "title_since": 900.0, "observations": 10,
                 "observed_monotonic": 1000.0, "observed_at": 1000.0}
        (runtime_dir / "hanjuku_run.json").write_text(json.dumps(state))
        return state

    def test_a_restored_frame_clears_a_game_over_verdict(self):
        with tempfile.TemporaryDirectory() as raw:
            runtime_dir = Path(raw)
            self._terminal_state(runtime_dir)
            with mock.patch.object(hanjuku_run, "classify", return_value="defense"):
                state = hanjuku_run.observe(runtime_dir, IDENTITY, _frame(2),
                                            now=1100.0, wall=1100.0)
            self.assertIsNone(state.get("terminal_reason"))
            self.assertFalse(state.get("terminal_candidate"))
            log = (runtime_dir / "hanjuku_events.jsonl").read_text()
            self.assertIn("terminal_cleared", log)

    def test_the_same_frame_still_holds_the_verdict(self):
        with tempfile.TemporaryDirectory() as raw:
            runtime_dir = Path(raw)
            self._terminal_state(runtime_dir)
            with mock.patch.object(hanjuku_run, "classify", return_value="title"):
                state = hanjuku_run.observe(runtime_dir, IDENTITY, _frame(1),
                                            now=1100.0, wall=1100.0)
            self.assertEqual(state.get("terminal_reason"), "game_over")

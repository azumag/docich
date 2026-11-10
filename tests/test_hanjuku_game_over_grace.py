"""A released game_over must not re-latch before the first inputs land.

g1163 2026-11-10 21:4x: `hanjuku-clear-terminal` released the verdict, the next
observation saw the same title screen and latched it again, so every input was
refused again within seconds.
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
            "generation": 999, "lease_id": "lease-grace"}


def _frame(seed: int) -> Frame:
    return Frame(256, 224, bytes([seed % 251]) * (256 * 224 * 3))


class GameOverGraceTest(unittest.TestCase):
    def _prime(self, runtime: Path, cleared_at: float) -> None:
        state = {"game": "hanjuku-hero", "runtime_id": "g999-abcdef01",
                 "generation": 999, "lease_id": "lease-grace",
                 "frame_sha256": _frame(1).digest(), "phase": "title",
                 "name_entered": True, "gameplay_seen": True, "playing": True,
                 "terminal_candidate": True, "title_count": 2, "title_since": 990.0,
                 "observations": 5, "observed_monotonic": 1000.0,
                 "observed_at": 1000.0, "terminal_cleared_at": cleared_at}
        (runtime / "hanjuku_run.json").write_text(json.dumps(state))

    def test_the_grace_window_keeps_the_title_from_latching(self):
        with tempfile.TemporaryDirectory() as raw:
            runtime = Path(raw)
            self._prime(runtime, cleared_at=1000.0)
            with mock.patch.object(hanjuku_run, "classify", return_value="title"):
                state = hanjuku_run.observe(runtime, IDENTITY, _frame(1),
                                            now=1001.0, wall=1001.0)
            self.assertIsNone(state.get("terminal_reason"))

    def test_after_the_grace_a_real_title_return_latches_again(self):
        with tempfile.TemporaryDirectory() as raw:
            runtime = Path(raw)
            self._prime(runtime, cleared_at=100.0)
            with mock.patch.object(hanjuku_run, "classify", return_value="title"):
                state = hanjuku_run.observe(runtime, IDENTITY, _frame(1),
                                            now=1001.0, wall=1001.0)
            self.assertEqual(state.get("terminal_reason"), "game_over")

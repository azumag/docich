"""owner 2026-11-26: 画面が60秒以上止まったら issue を発行する（1エピソード1回）。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from docich.hanjuku.os_layer.pixels import Frame
from docich import hanjuku_run

IDENTITY = {"game": "hanjuku-hero", "runtime_id": "g999-abcdef01",
            "generation": 999, "lease_id": "lease-stall"}


def _frame(seed: int) -> Frame:
    return Frame(256, 224, bytes([seed % 251]) * (256 * 224 * 3))


class StallAlertTest(unittest.TestCase):
    def _prime(self, runtime: Path) -> None:
        state = {"game": "hanjuku-hero", "runtime_id": "g999-abcdef01",
                 "generation": 999, "lease_id": "lease-stall",
                 "frame_sha256": _frame(1).digest(), "recent_frame_sha256": [_frame(1).digest()],
                 "phase": "field", "playing": True, "observed_monotonic": 1000.0,
                 "observed_at": 1000.0, "unchanged_since": 930.0,
                 "unchanged_seconds": 70.0, "name_entered": True, "gameplay_seen": True}
        (runtime / "hanjuku_run.json").write_text(json.dumps(state))

    def test_a_sixty_second_stall_files_one_alert(self):
        calls = []
        with tempfile.TemporaryDirectory() as raw:
            runtime = Path(raw)
            self._prime(runtime)
            with mock.patch.object(hanjuku_run, "classify", return_value="field"), \
                    mock.patch("docich.hanjuku.fault_issue.note_fault",
                               side_effect=lambda *a, **kw: calls.append(kw)):
                state = hanjuku_run.observe(runtime, IDENTITY, _frame(1),
                                            now=1001.0, wall=1001.0)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0].get("signature"), "screen_stalled_60s")
            self.assertIsNotNone(state.get("stall_alert_since"))

    def test_the_same_stall_does_not_alert_again(self):
        calls = []
        with tempfile.TemporaryDirectory() as raw:
            runtime = Path(raw)
            self._prime(runtime)
            with mock.patch.object(hanjuku_run, "classify", return_value="field"), \
                    mock.patch("docich.hanjuku.fault_issue.note_fault",
                               side_effect=lambda *a, **kw: calls.append(kw)):
                hanjuku_run.observe(runtime, IDENTITY, _frame(1), now=1001.0, wall=1001.0)
                hanjuku_run.observe(runtime, IDENTITY, _frame(1), now=1002.0, wall=1002.0)
            self.assertEqual(len(calls), 1)

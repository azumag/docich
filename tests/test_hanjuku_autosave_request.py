"""The brain asks for a periodic savestate; the adapter performs it.

g1163 2026-11-10: the state file stayed at 10:58 for nine hours while the agent
loop's own periodic save never reached the emulator. A hung battle then cost
the whole run because there was no recent restore point.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from docich import hanjuku_run


class AutosaveRequestTest(unittest.TestCase):
    def test_a_request_is_written_once_per_interval(self):
        with tempfile.TemporaryDirectory() as raw:
            runtime = Path(raw)
            first = hanjuku_run.request_autosave(runtime, now=1000.0, interval=900)
            self.assertEqual(first, runtime / hanjuku_run.AUTOSAVE_REQUEST)
            self.assertEqual(json.loads(first.read_text())["requested_at"], 1000.0)
            self.assertIsNone(hanjuku_run.request_autosave(runtime, now=1500.0, interval=900))
            second = hanjuku_run.request_autosave(runtime, now=1901.0, interval=900)
            self.assertIsNotNone(second)

    def test_a_disabled_interval_writes_nothing(self):
        with tempfile.TemporaryDirectory() as raw:
            runtime = Path(raw)
            self.assertIsNone(hanjuku_run.request_autosave(runtime, now=1.0, interval=0))
            self.assertFalse((runtime / hanjuku_run.AUTOSAVE_REQUEST).exists())

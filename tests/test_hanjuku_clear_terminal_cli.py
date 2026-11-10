"""Owner-explicit recovery for a stale terminal verdict (2026-11-10).

A restart after an emulator crash left ``terminal_reason='game_over'`` in
``hanjuku_run.json``; every input was refused with "Hanjuku terminal evidence
holds input" for about two hours while the restored game sat on a defence
prompt. Clearing it is an owner decision, so it lives in a CLI command that
keeps a byte copy and records the act.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from docich import cli


def _config(state_dir: Path):
    cfg = mock.Mock()
    cfg.state_dir = state_dir
    return cfg


class ClearTerminalTest(unittest.TestCase):
    def _runtime(self, root: Path, runtime_id: str = "g1-abcdef01") -> Path:
        runtime_dir = root / "runtimes" / runtime_id
        runtime_dir.mkdir(parents=True)
        (runtime_dir / "hanjuku_run.json").write_text(json.dumps(
            {"terminal_reason": "game_over", "terminal_candidate": True,
             "terminal_evidence": "title_return_after_gameplay",
             "observations": 12}))
        (root / "game_switch.json").write_text(json.dumps(
            {"phase": "ready", "active": {"runtime_id": runtime_id}}))
        return runtime_dir

    def test_the_verdict_is_cleared_with_a_backup_and_an_event(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            runtime_dir = self._runtime(root)
            assert cli.cmd_hanjuku_clear_terminal(_config(root)) == 0
            run = json.loads((runtime_dir / "hanjuku_run.json").read_text())
            self.assertIsNone(run["terminal_reason"])
            self.assertIsNone(run["terminal_candidate"])
            self.assertEqual(run["terminal_cleared_reason"], "game_over")
            self.assertEqual(run["observations"], 12)
            self.assertTrue(list(runtime_dir.glob("hanjuku_run.json.bak-*")))
            events = (runtime_dir / "hanjuku_events.jsonl").read_text()
            self.assertIn("terminal_cleared_by_operator", events)

    def test_a_clean_runtime_is_left_alone(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            runtime_dir = self._runtime(root)
            (runtime_dir / "hanjuku_run.json").write_text(json.dumps(
                {"terminal_reason": None, "terminal_candidate": None}))
            assert cli.cmd_hanjuku_clear_terminal(_config(root)) == 0
            self.assertFalse(list(runtime_dir.glob("hanjuku_run.json.bak-*")))

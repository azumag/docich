from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from docich.adapters.soren import SorenCoordinatorAdapter
from docich.game_switch import RuntimeSpec


class TestSorenCoordinatorAdapter(unittest.TestCase):
    def make_adapter(self, root: Path) -> SorenCoordinatorAdapter:
        (root / "lib").mkdir()
        (root / "lib/game_lifecycle.py").write_text("# broker\n")
        (root / "game_lifecycle_control.sh").write_text("#!/bin/sh\n")
        game = SimpleNamespace(
            raw={"soren": {"root": str(root)}},
            lifecycle=SimpleNamespace(boundary_timeout_s=7200),
        )
        spec = RuntimeSpec(
            game="sorengame", adapter="soren", runtime_id="r1", generation=7,
            lease_id="lease-1", runtime_dir=root / "runtime",
            game_window="game-g7", agent_window="agent-g7", adapter_session="soren-external",
        )
        return SorenCoordinatorAdapter(SimpleNamespace(), game, spec)

    def test_boundary_request_binds_request_and_generation(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = self.make_adapter(Path(temp))
            outputs = [
                {"ack": {"request_id": "req-1", "status": "accepted"}},
                {"ack": {"request_id": "req-1", "status": "boundary"}},
            ]
            calls = []
            def fake_run(argv, **kwargs):
                calls.append(argv)
                return SimpleNamespace(returncode=0, stdout=json.dumps(outputs.pop(0)), stderr="")
            with patch("docich.adapters.soren.subprocess.run", side_effect=fake_run):
                adapter.request_round_boundary("req-1", time.monotonic() + 30, None)
            self.assertIn("--generation", calls[0])
            self.assertEqual(calls[0][calls[0].index("--generation") + 1], "7")
            self.assertEqual(calls[0][calls[0].index("--request-id") + 1], "req-1")

    def test_cleanup_uses_fixed_control_argv_and_requires_stopped(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = self.make_adapter(Path(temp))
            adapter._request_id = "req-2"
            outputs = [
                {},
                {"ack": {"request_id": "req-2", "status": "stopped"}},
            ]
            calls = []
            def fake_run(argv, **kwargs):
                calls.append((argv, kwargs))
                return SimpleNamespace(returncode=0, stdout=json.dumps(outputs.pop(0)), stderr="")
            with patch("docich.adapters.soren.subprocess.run", side_effect=fake_run):
                adapter.cleanup_runtime(time.monotonic() + 30, None)
            self.assertEqual(calls[0][0], [str(adapter.control), "stop-after-boundary", "req-2"])
            self.assertGreater(calls[0][1]["timeout"], 15.0)

    def test_cancel_uses_fixed_control_so_partial_pause_is_restored(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = self.make_adapter(Path(temp))
            calls = []

            def fake_run(argv, **kwargs):
                calls.append(argv)
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps({"ack": {"request_id": "req-c", "status": "cancelled"}}),
                    stderr="",
                )

            with patch("docich.adapters.soren.subprocess.run", side_effect=fake_run):
                self.assertTrue(adapter.cancel_round_boundary("req-c", time.monotonic() + 30, None))
            self.assertEqual(calls[0], [str(adapter.control), "cancel", "req-c"])

    def test_materialize_fresh_starts_only_matching_stopped_request(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = self.make_adapter(Path(temp))
            outputs = [
                {"ack": {"request_id": "req-3", "status": "stopped"}},
                {"status": "fresh_start"},
            ]
            calls = []
            def fake_run(argv, **kwargs):
                calls.append(argv)
                return SimpleNamespace(returncode=0, stdout=json.dumps(outputs.pop(0)), stderr="")
            with patch("docich.adapters.soren.subprocess.run", side_effect=fake_run):
                adapter.materialize_runtime(time.monotonic() + 30, None)
            self.assertEqual(calls[1], [str(adapter.control), "fresh-start", "req-3"])

    def test_materialize_recovers_matching_stopped_resource_when_ack_was_lost(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = self.make_adapter(Path(temp))
            identity = {"request_id": "req-4", "game": "sorengame", "generation": 7}
            outputs = [{"request": identity, "ack": None, "resource": {**identity, "status": "stopped"}}, {"status": "starting"}]
            calls = []
            def fake_run(argv, **kwargs):
                calls.append(argv)
                return SimpleNamespace(returncode=0, stdout=json.dumps(outputs.pop(0)), stderr="")
            with patch("docich.adapters.soren.subprocess.run", side_effect=fake_run):
                adapter.materialize_runtime(time.monotonic() + 30, None)
            self.assertEqual(calls[1], [str(adapter.control), "fresh-start", "req-4"])

    def test_materialize_clears_matching_cancelled_request_for_rollback(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = self.make_adapter(Path(temp))
            identity = {"request_id": "req-5", "game": "sorengame", "generation": 7}
            outputs = [
                {"request": identity, "ack": {**identity, "status": "cancelled"},
                 "resource": {**identity, "status": "cancelled", "quit_called": False}},
                {"status": "starting"},
            ]
            calls = []

            def fake_run(argv, **kwargs):
                calls.append(argv)
                return SimpleNamespace(returncode=0, stdout=json.dumps(outputs.pop(0)), stderr="")

            with patch("docich.adapters.soren.subprocess.run", side_effect=fake_run):
                adapter.materialize_runtime(time.monotonic() + 30, None)
            self.assertEqual(calls[1], [str(adapter.control), "fresh-start", "req-5"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from docich.adapters.base import AdapterError
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

    def test_cleanup_classifies_fixed_stop_failures_without_exposing_raw_output(self):
        cases = {
            "改善プロセスの停止確認に失敗。request=req-2": "improve_stop_failed",
            "予想ワーカーの停止確認に失敗。request=req-2": "prediction_stop_failed",
            "停止要求の期限切れ。旧ゲームを継続": "deadline_expired",
            "共有表示未準備/legacy bridge のため handover をキャンセル": "overlay_unsupported",
            "attacker supplied unexpected output secret=do-not-copy": "unknown_stop_failure",
        }
        for output, expected in cases.items():
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temp:
                adapter = self.make_adapter(Path(temp))
                adapter._request_id = "req-2"
                result = SimpleNamespace(returncode=1, stdout=output, stderr="raw-secret")
                with patch("docich.adapters.soren.subprocess.run", return_value=result):
                    with self.assertRaisesRegex(AdapterError, rf"rc=1 reason={expected}$") as caught:
                        adapter.cleanup_runtime(time.monotonic() + 30, None)
                self.assertNotIn("do-not-copy", str(caught.exception))
                self.assertNotIn("raw-secret", str(caught.exception))

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

    def test_cancelled_runtime_requires_materialize_before_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = self.make_adapter(Path(temp))
            result = SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"ack": {"request_id": "req-c", "status": "cancelled"}}),
                stderr="",
            )
            with patch("docich.adapters.soren.subprocess.run", return_value=result):
                self.assertFalse(adapter.alive(time.monotonic() + 30, None))

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
            self.assertIsNone(adapter._fresh_started_at)

    def make_stateful_adapter(self, root: Path) -> SorenCoordinatorAdapter:
        adapter = self.make_adapter(root)
        adapter.g = SimpleNamespace(state_dir=root / "state")
        return adapter

    def test_cleanup_failure_appends_diagnostic_log(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            adapter = self.make_stateful_adapter(root)
            adapter._request_id = "req-9"
            body = "改善プロセスの停止確認に失敗。request=req-9"
            result = SimpleNamespace(returncode=1, stdout=body, stderr="")
            with patch("docich.adapters.soren.subprocess.run", return_value=result):
                with self.assertRaises(AdapterError):
                    adapter.cleanup_runtime(time.monotonic() + 30, None)
                with self.assertRaises(AdapterError):
                    adapter.cleanup_runtime(time.monotonic() + 30, None)
            log = root / "state" / "logs" / "soren_adapter.log"
            text = log.read_text(encoding="utf-8")
            self.assertEqual(text.count("operation=stop-after-boundary request_id=req-9"), 2)
            self.assertIn("rc=1", text)
            self.assertIn(body, text)
            self.assertIn("generation=7", text)

    def test_cleanup_timeout_records_timeout_entry_without_stale_output(self):
        import subprocess
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            adapter = self.make_stateful_adapter(root)
            adapter._request_id = "req-10"
            adapter._last_command_output = "STALE-MARKER"
            with patch("docich.adapters.soren.subprocess.run",
                       side_effect=subprocess.TimeoutExpired(cmd="x", timeout=1)):
                from docich.game_switch import ReadinessTimeoutError
                with self.assertRaises(ReadinessTimeoutError):
                    adapter.cleanup_runtime(time.monotonic() + 30, None)
            text = (root / "state" / "logs" / "soren_adapter.log").read_text(encoding="utf-8")
            self.assertIn("rc=timeout", text)
            self.assertNotIn("STALE-MARKER", text)

    def test_diagnostic_log_never_breaks_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            adapter = self.make_adapter(root)
            blocker = root / "blocker"
            blocker.write_text("file, not dir")
            adapter.g = SimpleNamespace(state_dir=blocker)
            adapter._request_id = "req-11"
            result = SimpleNamespace(returncode=1, stdout="改善プロセスの停止確認に失敗", stderr="")
            with patch("docich.adapters.soren.subprocess.run", return_value=result):
                with self.assertRaisesRegex(AdapterError, "reason=improve_stop_failed"):
                    adapter.cleanup_runtime(time.monotonic() + 30, None)

    def test_diagnostic_log_bounds_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            adapter = self.make_stateful_adapter(root)
            adapter._request_id = "req-12"
            big = "x" * 20000
            result = SimpleNamespace(returncode=1, stdout=big, stderr="")
            with patch("docich.adapters.soren.subprocess.run", return_value=result):
                with self.assertRaises(AdapterError):
                    adapter.cleanup_runtime(time.monotonic() + 30, None)
            text = (root / "state" / "logs" / "soren_adapter.log").read_text(encoding="utf-8")
            self.assertLessEqual(len(text), 8192 + 512)


if __name__ == "__main__":
    unittest.main()

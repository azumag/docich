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
from docich.game_switch import DeadlineExceededError, RuntimeSpec


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
                self.boundary_payload("accepted"),
                self.boundary_payload("boundary"),
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

    @staticmethod
    def boundary_payload(status, **changes):
        identity = {"schema": 1, "request_id": "req-1", "game": "sorengame", "generation": 7,
                    "deadline_epoch": 1893456000.25, "deadline_at": "2030-01-01T00:00:00.250Z"}
        identity.update(changes)
        return {"request": dict(identity), "ack": {**identity, "status": status}}

    def run_boundary_script(self, adapter, script, cancel=None):
        calls = []
        def fake_run(argv, **kwargs):
            command, rc, payload = script.pop(0)
            self.assertEqual(argv[4], command)
            calls.append(argv)
            return SimpleNamespace(returncode=rc, stdout=json.dumps(payload), stderr="")
        with patch("docich.adapters.soren.subprocess.run", side_effect=fake_run), patch(
            "docich.adapters.soren.time.sleep", return_value=None
        ):
            adapter.request_round_boundary("req-1", time.monotonic() + 30, cancel)
        self.assertFalse(script)
        return calls

    def test_normal_boundary_actively_polls_without_game_bookkeeping(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = self.make_adapter(Path(temp))
            calls = self.run_boundary_script(adapter, [
                ("request", 0, self.boundary_payload("accepted")),
                ("status", 0, self.boundary_payload("accepted")),
                ("boundary", 1, self.boundary_payload("waiting")),
                ("status", 0, {**self.boundary_payload("waiting"), "next_generation": 99}),
                ("boundary", 0, self.boundary_payload("boundary")),
            ])
            for argv in calls:
                if argv[4] != "status":
                    self.assertEqual(argv[argv.index("--request-id") + 1], "req-1")
            self.assertEqual([argv[4] for argv in calls], ["request", "status", "boundary", "status", "boundary"])

    def test_boundary_poll_rejects_changed_or_missing_receipt_identity(self):
        for command in ("status", "boundary"):
            for field, value in (("request_id", "other"), ("game", "robots"), ("generation", 8),
                                 ("deadline_epoch", 1893456001.25), ("deadline_at", "2030-01-01T00:00:01Z"),
                                 ("generation", True), ("operation", "player_change")):
                with self.subTest(command=command, field=field), tempfile.TemporaryDirectory() as temp:
                    adapter = self.make_adapter(Path(temp))
                    changed = self.boundary_payload("boundary", **{field: value})
                    script = [("request", 0, self.boundary_payload("accepted"))]
                    if command == "boundary":
                        script.append(("status", 0, self.boundary_payload("waiting")))
                    script.append((command, 0, changed))
                    with self.assertRaises(AdapterError):
                        self.run_boundary_script(adapter, script)
        for payload in ({}, {"ack": {"request_id": "req-1", "status": "boundary"}},
                        {"request": self.boundary_payload("boundary")["request"], "ack": {}}):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as temp:
                adapter = self.make_adapter(Path(temp))
                with self.assertRaises(AdapterError):
                    self.run_boundary_script(adapter, [("request", 0, self.boundary_payload("accepted")),
                                                       ("status", 0, payload)])

    def test_boundary_poll_rejects_failure_codes_and_terminal_or_fenced_status(self):
        for command in ("status", "boundary"):
            for status in ("failed", "timeout", "unsupported", "cancelled", "resumed", "stopping", "stop_requested", "prepared"):
                with self.subTest(command=command, status=status), tempfile.TemporaryDirectory() as temp:
                    adapter = self.make_adapter(Path(temp))
                    script = [("request", 0, self.boundary_payload("accepted"))]
                    if command == "boundary":
                        script.append(("status", 0, self.boundary_payload("waiting")))
                    script.append((command, 0, self.boundary_payload(status)))
                    with self.assertRaises(AdapterError):
                        self.run_boundary_script(adapter, script)
        for rc in (2, 3, 4, 5, 79):
            with self.subTest(rc=rc), tempfile.TemporaryDirectory() as temp:
                adapter = self.make_adapter(Path(temp))
                with self.assertRaises(AdapterError):
                    self.run_boundary_script(adapter, [("request", 0, self.boundary_payload("accepted")),
                        ("status", 0, self.boundary_payload("waiting")), ("boundary", rc, self.boundary_payload("boundary"))])

    def test_boundary_poll_checks_cancellation_after_successful_boundary_response(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = self.make_adapter(Path(temp))
            cancelled = [False]
            cancel = SimpleNamespace(is_set=lambda: cancelled[0])
            def broker(*args):
                cancelled[0] = True
                return 0, self.boundary_payload("boundary")
            with patch.object(adapter, "_status", return_value=self.boundary_payload("waiting")), patch.object(adapter, "_broker", side_effect=broker):
                with self.assertRaises(DeadlineExceededError):
                    adapter._wait_round_boundary(self.boundary_payload("accepted")["request"], time.monotonic() + 30, cancel)

    def test_boundary_poll_rejects_inconsistent_request_and_ack_or_return_code(self):
        variants = []
        for side in ("request", "ack"):
            payload = self.boundary_payload("boundary")
            payload[side]["generation"] = 8
            variants.append((0, payload))
        variants.extend([(0, self.boundary_payload("waiting")), (1, self.boundary_payload("boundary"))])
        for rc, payload in variants:
            with self.subTest(rc=rc, payload=payload), tempfile.TemporaryDirectory() as temp:
                adapter = self.make_adapter(Path(temp))
                with self.assertRaises(AdapterError):
                    self.run_boundary_script(adapter, [("request", 0, self.boundary_payload("accepted")),
                        ("status", 0, self.boundary_payload("waiting")), ("boundary", rc, payload)])

    def test_boundary_poll_checks_cancel_and_deadline_before_side_effect_free_command(self):
        for cancel_set in (True, False):
            with self.subTest(cancel=cancel_set), tempfile.TemporaryDirectory() as temp:
                adapter = self.make_adapter(Path(temp))
                calls = []
                receipt = self.boundary_payload("accepted")
                def status(*args):
                    return receipt
                cancel = SimpleNamespace(is_set=lambda: cancel_set)
                with patch.object(adapter, "_status", side_effect=status), patch.object(adapter, "_broker", side_effect=lambda *args: calls.append(args)):
                    # Enter the wait with a valid receipt, then a cancelled or expired call.
                    with self.assertRaises(DeadlineExceededError):
                        adapter._wait_round_boundary(receipt["request"], time.monotonic() - (1 if not cancel_set else -30), cancel)
                self.assertEqual(calls, [])

    def test_reconfigure_player_commits_only_after_prepared_boundary(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = self.make_adapter(Path(temp))
            request_id = "11111111-1111-4111-8111-111111111111"
            run_id = "22222222-2222-4222-8222-222222222222"
            config_hash = "a" * 64
            outputs = [
                {"capabilities": ["player_policy_v1"], "game_generation": 9},
                {"ack": {"request_id": request_id, "status": "accepted"}},
                {"ack": {"request_id": request_id, "status": "prepared"}},
                {
                    "status": "committed",
                    "player_state": {"policy": "jev", "player_generation": 1},
                    "ack": {"request_id": request_id, "status": "committed"},
                },
            ]
            calls = []

            def fake_run(argv, **kwargs):
                calls.append(argv)
                return SimpleNamespace(returncode=0, stdout=json.dumps(outputs.pop(0)), stderr="")

            with patch("docich.adapters.soren.subprocess.run", side_effect=fake_run):
                state = adapter.reconfigure_player(
                    request_id=request_id,
                    target_policy="jev",
                    run_id=run_id,
                    expected_player_generation=0,
                    config_hash=config_hash,
                    deadline=time.monotonic() + 30,
                    cancel=None,
                )

            self.assertEqual(state["policy"], "jev")
            self.assertEqual(calls[1][calls[1].index("--generation") + 1], "9")
            self.assertEqual(calls[1][calls[1].index("--operation") + 1], "player_change")
            self.assertEqual(calls[3], [str(adapter.control), "player-commit", request_id])

    def test_reconfigure_player_drives_boundary_when_loop_is_parked(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = self.make_adapter(Path(temp))
            request_id = "11111111-1111-4111-8111-111111111111"
            run_id = "22222222-2222-4222-8222-222222222222"
            outputs = [
                {"capabilities": ["player_policy_v1"], "game_generation": 9},
                {"ack": {"request_id": request_id, "status": "accepted"}},
                {"ack": {"request_id": request_id, "status": "accepted"}},
                {"ack": {"request_id": request_id, "status": "waiting"}},
                {"ack": {"request_id": request_id, "status": "prepared"}},
                {
                    "status": "committed",
                    "player_state": {"policy": "existing", "player_generation": 2},
                    "ack": {"request_id": request_id, "status": "committed"},
                },
            ]
            calls = []

            def fake_run(argv, **kwargs):
                calls.append(argv)
                return SimpleNamespace(returncode=0, stdout=json.dumps(outputs.pop(0)), stderr="")

            with patch("docich.adapters.soren.subprocess.run", side_effect=fake_run), patch(
                "docich.adapters.soren.time.sleep", return_value=None
            ):
                state = adapter.reconfigure_player(
                    request_id=request_id,
                    target_policy="existing",
                    run_id=run_id,
                    expected_player_generation=1,
                    config_hash="b" * 64,
                    deadline=time.monotonic() + 30,
                    cancel=None,
                    game_generation=9,
                )

            self.assertEqual(state["policy"], "existing")
            self.assertEqual(calls[3], ["python3", str(adapter.broker), "--root", str(adapter.root), "boundary", "--request-id", request_id])
            self.assertEqual(calls[-1], [str(adapter.control), "player-commit", request_id])

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

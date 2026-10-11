from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from docich.adapters.base import AdapterError
from docich.adapters.soren import SorenCoordinatorAdapter
from docich.game_switch import DeadlineExceededError, GameSwitchCoordinator, GameSwitchStore, RuntimeSpec, runtime_names


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
            outputs = [{}]
            adapter._status = Mock(side_effect=[self.stop_payload("req-2", "stopping"),
                                               self.stop_payload("req-2", "stopped")])
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
                adapter._status = Mock(return_value=self.stop_payload("req-2", "stopping"))
                result = SimpleNamespace(returncode=1, stdout=output, stderr="raw-secret")
                with patch("docich.adapters.soren.subprocess.run", return_value=result):
                    with self.assertRaisesRegex(AdapterError, rf"rc=1 reason={expected}$") as caught:
                        adapter.cleanup_runtime(time.monotonic() + 30, None)
                self.assertNotIn("do-not-copy", str(caught.exception))
                self.assertNotIn("raw-secret", str(caught.exception))

    def test_completed_stop_proof_does_not_outlive_a_successful_fresh_start(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = self.make_adapter(Path(temp))
            adapter._request_id = "req-2"
            adapter._status = Mock(side_effect=[self.stop_payload("req-2", "stopping"),
                self.stop_payload("req-2", "stopped"), self.stop_payload("req-2", "stopped"),
                {"schema": 1, "request": None, "ack": None, "resource": None}])
            adapter._run = Mock(return_value=(0, {}))
            deadline = time.monotonic() + 30
            adapter.cleanup_runtime(deadline, None)
            self.assertIsNotNone(adapter._completed_stop_receipt)
            adapter.materialize_runtime(deadline, None)
            self.assertIsNone(adapter._completed_stop_receipt)
            self.assertTrue(adapter.alive(deadline, None))

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

    def _stopped_materialize(self, adapter, request_id="req-9"):
        identity = {"request_id": request_id, "game": "sorengame", "generation": 7}
        outputs = [{"request": identity, "ack": None, "resource": {**identity, "status": "stopped"}},
                   {"status": "starting"}]

        def fake_run(argv, **kwargs):
            return SimpleNamespace(returncode=0, stdout=json.dumps(outputs.pop(0)), stderr="")

        with patch("docich.adapters.soren.subprocess.run", side_effect=fake_run):
            adapter.materialize_runtime(time.monotonic() + 30, None)

    def test_paused_loop_that_survived_the_stop_is_accepted_once_the_pause_is_cleared(self):
        """2026-10-11: stop pauses soren_loop.sh; fresh-start only clears the pause."""
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            adapter = self.make_adapter(root)
            (root / "tmp/state").mkdir(parents=True)
            pause = root / "tmp/state/soren_loop.paused"
            pause.write_text("lifecycle:req-9")
            old_loop = [(time.time() - 3000, 4242, 777)]       # born long before the fence
            with patch.object(adapter, "_process_matches", side_effect=lambda name: list(old_loop)):
                self._stopped_materialize(adapter)
                self.assertEqual(adapter._resumed_loop, (4242, 777))
                self.assertFalse(adapter._live_process("soren_loop.sh"))   # still paused
                pause.unlink()
                self.assertTrue(adapter._live_process("soren_loop.sh"))     # resumed, same instance
                # the watchdog is never excused by this path
                self.assertFalse(adapter._live_process("soviet_watchdog.sh"))

    def test_a_different_old_loop_or_a_second_loop_is_still_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            adapter = self.make_adapter(root)
            (root / "tmp/state").mkdir(parents=True)
            (root / "tmp/state/soren_loop.paused").write_text("x")
            with patch.object(adapter, "_process_matches", return_value=[(time.time() - 3000, 4242, 777)]):
                self._stopped_materialize(adapter)
            (root / "tmp/state/soren_loop.paused").unlink()
            stale_other = [(time.time() - 3000, 9999, 888)]
            with patch.object(adapter, "_process_matches", return_value=stale_other):
                self.assertFalse(adapter._live_process("soren_loop.sh"))
            two = [(time.time() - 3000, 4242, 777), (time.time() - 5, 5000, 999)]
            with patch.object(adapter, "_process_matches", return_value=two):
                self.assertFalse(adapter._live_process("soren_loop.sh"))

    def test_without_a_pause_marker_no_old_loop_is_remembered(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = self.make_adapter(Path(temp))
            with patch.object(adapter, "_process_matches", return_value=[(time.time() - 3000, 4242, 777)]):
                self._stopped_materialize(adapter)
                self.assertIsNone(adapter._resumed_loop)
                self.assertFalse(adapter._live_process("soren_loop.sh"))

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
        from test_coordinator import _runtime_dict
        adapter = self.make_adapter(root)
        adapter.g = SimpleNamespace(state_dir=root / "state")
        store = GameSwitchStore(adapter.g.state_dir)
        state = store.initialize()
        runtime = {**_runtime_dict(7, "sorengame"), "adapter": "soren"}
        state.update(phase="ready", active=runtime, next_generation=8)
        store.canonical.save(state)
        adapter.spec = RuntimeSpec.from_runtime(adapter.g.state_dir, runtime)
        adapter._status = Mock(side_effect=lambda *_: self.stop_payload(adapter._request_id, "stopping"))
        return adapter

    def stop_payload(self, request_id, status):
        payload = self.boundary_payload(status, request_id=request_id)
        return {**payload, "schema": 1, "resource": {**payload["request"], "status": status}}

    def make_retired_singleton(self, root):
        import uuid
        adapter = self.make_stateful_adapter(root)
        store = GameSwitchStore(adapter.g.state_dir)
        state = store.initialize()
        def runtime(generation):
            names = runtime_names(generation)
            return dict(game="sorengame", adapter="soren", generation=generation,
                        runtime_id=f"g{generation}-abcdef", lease_id=str(uuid.uuid4()),
                        game_window=names.game_window, agent_window=names.agent_window,
                        adapter_session=names.adapter_session, started_at="2026-10-09T00:00:00Z")
        active, retired = runtime(7), runtime(9)
        state.update(phase="ready", active=active, retiring=[retired], next_generation=10)
        store.canonical.save(state)
        adapter.spec = RuntimeSpec.from_runtime(adapter.g.state_dir, retired)
        adapter._status = Mock(return_value={"schema": 1, "request": None, "ack": None, "resource": None})
        adapter._live_process = Mock(return_value=True)
        adapter._singleton_process_identity = Mock(side_effect=lambda name: (1 if name == "soren_loop.sh" else 2, 10))
        adapter._run = Mock(side_effect=AssertionError("must not stop the live singleton"))
        return adapter, store, state

    def cli_active(self, generation: int) -> dict:
        """A canonical active runtime owned by a non-Soren game (e.g. nethack).

        The caller must keep ``next_generation`` ahead of this generation.
        """
        import uuid
        names = runtime_names(generation)
        return dict(game="nethack", adapter="cli", generation=generation,
                    runtime_id=f"g{generation}-abcdef", lease_id=str(uuid.uuid4()),
                    game_window=names.game_window, agent_window=names.agent_window,
                    adapter_session=names.adapter_session, started_at="2026-10-09T00:00:00Z")

    def swap_active_to_cli(self, store, state: dict, generation: int = 10) -> None:
        """Hand the canonical active slot to a non-Soren game (a CLI corner)."""
        state["active"] = self.cli_active(generation)
        state["next_generation"] = max(generation + 1, state["next_generation"])
        store.canonical.save(state)

    def make_failed_singleton_restore(self, root):
        import uuid
        adapter, store, state = self.make_retired_singleton(root)
        previous = state["active"]
        state.update(retiring=[])
        store.canonical.save(state)
        rid = str(uuid.uuid4())
        acceptance = store.accept_request(rid, "switch", "tsuitate-view")
        body = dict(request_id=rid, operation="switch", status="failed",
                    from_game=None, to_game="tsuitate-view", generation=acceptance.generation,
                    error_code="rollback_failed", detail="synthetic rollback failure")
        with store.transaction() as tx:
            tx.finish_request(rid, "failed", body)
        names = runtime_names(acceptance.generation + 1)
        failed = dict(previous, generation=acceptance.generation + 1,
                      runtime_id=f"g{acceptance.generation + 1}-abcdef", lease_id=str(uuid.uuid4()),
                      game_window=names.game_window, agent_window=names.agent_window,
                      adapter_session=names.adapter_session, cleanup_role="failed_candidate")
        state = store.canonical.load()[0]
        state.update(phase="failed", active=None, previous=previous, candidate=None,
                     retiring=[failed], request_id=None, operation=None, last_result=body,
                     next_generation=failed["generation"] + 1)
        store.canonical.save(state)
        adapter.spec = RuntimeSpec.from_runtime(store.state_dir, previous)
        adapter._status = Mock(return_value=dict(schema=1, request=None, ack=None, resource=None, control=None))
        return adapter, store, state

    def test_failed_restore_releases_no_game_process_and_preserves_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter, store, before = self.make_failed_singleton_restore(Path(temp))
            receipt = store.receipts.load(before["last_result"]["request_id"])
            def factory(spec):
                instance = SorenCoordinatorAdapter(adapter.g, adapter.game, spec)
                instance._status = adapter._status
                instance._singleton_process_identity = adapter._singleton_process_identity
                instance._run = adapter._run
                instance.readiness = Mock()
                instance.materialize_runtime = Mock(side_effect=AssertionError("must not restart"))
                return instance
            result = GameSwitchCoordinator(store, factory).recover(timeout_s=5)
            after = store.canonical.load()[0]
            self.assertEqual(result.status, "rolled_back")
            self.assertFalse(result.cleanup_pending)
            self.assertEqual(after["phase"], "ready")
            self.assertEqual(after["active"]["runtime_id"], before["previous"]["runtime_id"])
            self.assertNotEqual(after["active"]["lease_id"], before["previous"]["lease_id"])
            self.assertEqual(after["retiring"], [])
            self.assertEqual(store.receipts.load(receipt["request_id"]), receipt)
            adapter._run.assert_not_called()

    def test_failed_restore_rechecks_proof_after_readiness(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter, store, before = self.make_failed_singleton_restore(Path(temp))
            proof = Mock(side_effect=[True, False])
            def factory(spec):
                instance = SorenCoordinatorAdapter(adapter.g, adapter.game, spec)
                instance._status = adapter._status
                instance._singleton_process_identity = adapter._singleton_process_identity
                instance._run = adapter._run
                instance.readiness = Mock()
                instance.can_restore_live_singleton = proof
                return instance
            result = GameSwitchCoordinator(store, factory).recover(timeout_s=5)
            self.assertEqual(result.status, "failed")
            after = store.canonical.load()[0]
            self.assertEqual(after["previous"], before["previous"])
            self.assertEqual(after["retiring"], before["retiring"])
            adapter._run.assert_not_called()

    def test_failed_live_restore_requires_exact_ownership_and_positive_process_proof(self):
        import copy
        with tempfile.TemporaryDirectory() as temp:
            adapter, store, before = self.make_failed_singleton_restore(Path(temp))
            deadline = time.monotonic() + 5
            self.assertTrue(adapter.can_restore_live_singleton(deadline, None))
            variants = []
            for key, value in [("cleanup_role", "source"), ("game", "another"), ("lease_id", None)]:
                state = copy.deepcopy(before)
                state["retiring"][0][key] = value
                variants.append(state)
            state = copy.deepcopy(before)
            state["retiring"].append(copy.deepcopy(state["retiring"][0]))
            # Duplicate identities are rejected by the canonical schema, so
            # use a second distinct, positively tracked runtime here.
            state["retiring"][1]["runtime_id"] = "g12-012345ab"
            state["retiring"][1]["lease_id"] = str(__import__("uuid").uuid4())
            state["retiring"][1]["generation"] += 1
            state["retiring"][1].update({k: v for k,v in vars(runtime_names(state["retiring"][1]["generation"])).items()})
            state["next_generation"] += 1
            variants.append(state)
            for state in variants:
                store.canonical.save(state)
                self.assertFalse(adapter.can_restore_live_singleton(deadline, None))
            store.canonical.save(before)
            for key in ("request", "ack", "resource", "control"):
                adapter._status.return_value = dict(schema=1, request=None, ack=None, resource=None, control=None)
                adapter._status.return_value[key] = {"held": True}
                self.assertFalse(adapter.can_restore_live_singleton(deadline, None))
            adapter._status.return_value = dict(schema=1, request=None, ack=None, resource=None, control=None)
            adapter._singleton_process_identity.return_value = None
            adapter._singleton_process_identity.side_effect = None
            self.assertFalse(adapter.can_restore_live_singleton(deadline, None))
            adapter._singleton_process_identity.side_effect = lambda name: (1 if name == "soren_loop.sh" else 2, 10)
            store.receipts._path(before["last_result"]["request_id"]).unlink()
            self.assertFalse(adapter.can_restore_live_singleton(deadline, None))

    def make_failed_live_candidate(self, root):
        """Soren switch timed out + rollback failed, yet the candidate is running."""
        import copy
        import uuid
        adapter, store, state = self.make_retired_singleton(root)
        previous = self.cli_active(6)
        state.update(phase="ready", active=previous, retiring=[], next_generation=7)
        store.canonical.save(state)
        rid = str(uuid.uuid4())
        acceptance = store.accept_request(rid, "switch", "sorengame")
        gen = acceptance.generation
        body = dict(request_id=rid, operation="switch", status="failed", from_game="nethack",
                    to_game="sorengame", generation=gen, error_code="rollback_failed",
                    detail="synthetic rollback failure")
        with store.transaction() as tx:
            tx.finish_request(rid, "failed", body)
        names = runtime_names(gen)
        failed = dict(game="sorengame", adapter="soren", generation=gen, runtime_id=f"g{gen}-abcdef",
                      lease_id=str(uuid.uuid4()), game_window=names.game_window,
                      agent_window=names.agent_window, adapter_session=names.adapter_session,
                      started_at="2026-10-09T00:00:00Z", cleanup_role="failed_candidate")
        state = store.canonical.load()[0]
        state.update(phase="failed", active=None, previous=previous, candidate=None,
                     retiring=[failed], request_id=None, operation=None, last_result=body,
                     next_generation=gen + 1)
        store.canonical.save(state)
        adapter.spec = RuntimeSpec.from_runtime(store.state_dir, failed)
        broker = {"v": self.candidate_broker_payload(adapter, "timeout")}
        calls = []
        def run(argv, deadline, cancel, **kwargs):
            calls.append(list(argv))
            if argv[1:2] == ["cancel"]:
                broker["v"] = self.candidate_broker_payload(adapter, "cancelled", control=True)
            return 0, {}
        adapter._status = Mock(side_effect=lambda *a: copy.deepcopy(broker["v"]))
        adapter._run = Mock(side_effect=run)
        adapter.materialize_runtime = Mock(side_effect=lambda *a: broker.update(v=self.clean_broker()))
        adapter.readiness = Mock()
        adapter.test_calls = calls
        adapter.test_broker = broker
        return adapter, store, state

    @staticmethod
    def clean_broker():
        return dict(schema=1, request=None, ack=None, resource=None, control=None)

    @staticmethod
    def candidate_broker_payload(adapter, status, control=False):
        rid = adapter._candidate_cleanup_request_id()
        rec = {"schema": 1, "request_id": rid, "game": "sorengame",
               "generation": adapter.spec.generation}
        return dict(schema=1, request=dict(rec), ack={**rec, "status": status}, resource=None,
                    control=dict(rec) if control else None)

    def test_adopt_failed_candidate_cancels_stale_stop_and_archives_it(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter, store, before = self.make_failed_live_candidate(Path(temp))
            deadline = time.monotonic() + 5
            self.assertTrue(adapter.adopt_failed_candidate(deadline, None))
            rid = adapter._candidate_cleanup_request_id()
            self.assertEqual(len(adapter.test_calls), 1)
            self.assertEqual(adapter.test_calls[0][1:], ["cancel", rid])
            adapter.materialize_runtime.assert_called_once()
            adapter.readiness.assert_called_once()
            # The adapter never writes canonical (the coordinator commits).
            self.assertEqual(store.canonical.load()[0]["phase"], "failed")
            self.assertEqual(store.canonical.load()[0]["retiring"], before["retiring"])

    def test_adopt_failed_candidate_resumes_after_cancel_or_when_already_clean(self):
        for payload, expect_materialize in (("cancelled", True), ("clean", False)):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as temp:
                adapter, _store, _before = self.make_failed_live_candidate(Path(temp))
                adapter.test_broker["v"] = (
                    self.candidate_broker_payload(adapter, "cancelled", control=True)
                    if payload == "cancelled" else self.clean_broker())
                self.assertTrue(adapter.adopt_failed_candidate(time.monotonic() + 5, None))
                self.assertEqual(adapter.test_calls, [])  # no second cancel
                self.assertEqual(adapter.materialize_runtime.called, expect_materialize)

    def test_adopt_failed_candidate_refuses_unproven_ownership_and_never_mutates(self):
        import copy
        import uuid
        def foreign_request(adapter):
            payload = self.candidate_broker_payload(adapter, "timeout")
            payload["request"]["request_id"] = str(uuid.uuid4())
            return payload
        def with_resource(adapter):
            payload = self.candidate_broker_payload(adapter, "timeout")
            payload["resource"] = {"status": "stopped"}
            return payload
        broker_cases = {
            "foreign request": foreign_request,
            "resource held": with_resource,
            "stop in progress": lambda a: self.candidate_broker_payload(a, "stopping"),
            "stopped": lambda a: self.candidate_broker_payload(a, "stopped"),
            "timeout with control": lambda a: self.candidate_broker_payload(a, "timeout", control=True),
        }
        for label, build in broker_cases.items():
            with self.subTest(broker=label), tempfile.TemporaryDirectory() as temp:
                adapter, _store, _before = self.make_failed_live_candidate(Path(temp))
                adapter.test_broker["v"] = build(adapter)
                self.assertFalse(adapter.adopt_failed_candidate(time.monotonic() + 5, None))
                self.assertEqual(adapter.test_calls, [])
                adapter.materialize_runtime.assert_not_called()
        def drop_receipt(adapter, store, state):
            store.receipts._path(state["last_result"]["request_id"]).unlink()
        def phase_ready(adapter, store, state):
            state = copy.deepcopy(state)
            state.update(phase="ready", previous=None, active=state["retiring"][0], retiring=[])
            store.canonical.save(state)
        def previous_is_soren(adapter, store, state):
            state = copy.deepcopy(state)
            state["previous"]["adapter"] = "soren"
            store.canonical.save(state)
        def other_error(adapter, store, state):
            state = copy.deepcopy(state)
            state["last_result"]["error_code"] = "start_failed"
            store.canonical.save(state)
        def has_retirement_proof(adapter, store, state):
            state = copy.deepcopy(state)
            state["retiring"][0]["retirement"] = {"request_id": state["last_result"]["request_id"]}
            store.canonical.save(state)
        def other_identity(adapter, store, state):
            adapter.spec = RuntimeSpec.from_runtime(
                store.state_dir, {**state["retiring"][0], "lease_id": str(uuid.uuid4())})
        for mutate in (drop_receipt, phase_ready, previous_is_soren, other_error,
                       has_retirement_proof, other_identity):
            with self.subTest(canonical=mutate.__name__), tempfile.TemporaryDirectory() as temp:
                adapter, store, state = self.make_failed_live_candidate(Path(temp))
                mutate(adapter, store, state)
                self.assertFalse(adapter.adopt_failed_candidate(time.monotonic() + 5, None))
                self.assertEqual(adapter.test_calls, [])
                adapter.materialize_runtime.assert_not_called()

    def test_adopt_failed_candidate_requires_both_singleton_processes(self):
        for identities in (None, "same_pid", "flapping"):
            with self.subTest(identities=identities), tempfile.TemporaryDirectory() as temp:
                adapter, _store, _before = self.make_failed_live_candidate(Path(temp))
                if identities is None:
                    adapter._singleton_process_identity = Mock(return_value=None)
                elif identities == "same_pid":
                    adapter._singleton_process_identity = Mock(return_value=(5, 10))
                else:
                    ticks = iter(range(100, 200))
                    adapter._singleton_process_identity = Mock(
                        side_effect=lambda name: (1 if name == "soren_loop.sh" else 2, next(ticks)))
                self.assertFalse(adapter.adopt_failed_candidate(time.monotonic() + 5, None))
                if identities != "flapping":
                    adapter.materialize_runtime.assert_not_called()

    def test_adoption_rejects_stable_replacement_after_initial_process_proof(self):
        import copy
        initial = {"soren_loop.sh": (11, 100), "soviet_watchdog.sh": (22, 200)}
        for names in (("soren_loop.sh",), ("soviet_watchdog.sh",), tuple(initial)):
            for field in (0, 1):  # PID replacement or PID reuse with a new birth tick.
                with self.subTest(names=names, field=field), tempfile.TemporaryDirectory() as temp:
                    adapter, store, before = self.make_failed_live_candidate(Path(temp))
                    before = store.canonical.load()[0]
                    changed = copy.deepcopy(initial)
                    for name in names:
                        identity = list(changed[name])
                        identity[field] += 1000
                        changed[name] = tuple(identity)
                    adapter._live_singleton_processes = Mock(
                        side_effect=[initial, changed, changed])
                    receipt_id = before["last_result"]["request_id"]
                    receipt = store.receipts.load(receipt_id)
                    self.assertFalse(adapter.adopt_failed_candidate(time.monotonic() + 5, None))
                    self.assertEqual(store.canonical.load()[0], before)
                    self.assertEqual(store.receipts.load(receipt_id), receipt)
                    self.assertEqual(adapter.spec.lease_id, before["retiring"][0]["lease_id"])

    def test_adoption_accepts_only_three_matching_process_samples(self):
        initial = {"soren_loop.sh": (11, 100), "soviet_watchdog.sh": (22, 200)}
        other = {**initial, "soren_loop.sh": (33, 300)}
        for samples, expected in (
            ([initial, initial, initial], True),
            ([initial, other, initial], False),
            ([initial, initial, other], False),
            ([initial, None, None], False),
            ([initial, initial, None], False),
        ):
            with self.subTest(samples=samples), tempfile.TemporaryDirectory() as temp:
                adapter, store, before = self.make_failed_live_candidate(Path(temp))
                before = store.canonical.load()[0]
                adapter._live_singleton_processes = Mock(side_effect=samples)
                self.assertEqual(adapter.adopt_failed_candidate(time.monotonic() + 5, None), expected)
                self.assertEqual(store.canonical.load()[0], before)

    def test_adopt_failed_candidate_fails_closed_when_cancel_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter, _store, _before = self.make_failed_live_candidate(Path(temp))
            adapter._run = Mock(return_value=(3, {}))
            self.assertFalse(adapter.adopt_failed_candidate(time.monotonic() + 5, None))
            adapter.materialize_runtime.assert_not_called()
            adapter.readiness.assert_not_called()

    def test_recover_retires_superseded_singleton_without_stopping_active_game(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter, store, before = self.make_retired_singleton(Path(temp))
            def factory(spec):
                instance = SorenCoordinatorAdapter(adapter.g, adapter.game, spec)
                instance._status, instance._live_process = adapter._status, adapter._live_process
                instance._singleton_process_identity = adapter._singleton_process_identity
                instance._run = adapter._run
                return instance
            result = GameSwitchCoordinator(store, factory).recover(timeout_s=5)
            after, _ = store.canonical.load()
            self.assertEqual(result.status, "succeeded")
            self.assertFalse(result.cleanup_pending)
            self.assertEqual(after["active"], before["active"])
            self.assertEqual(after["retiring"], [])
            adapter._run.assert_not_called()

    def test_retired_singleton_requires_stable_owner_and_idle_broker(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter, store, state = self.make_retired_singleton(Path(temp))
            deadline = time.monotonic() + 30
            for field in ("request", "ack", "resource"):
                with self.subTest(field=field):
                    adapter._status.return_value = {field: {"status": "stopped"}}
                    self.assertFalse(adapter._retired_singleton_is_superseded(deadline, None))
                    with self.assertRaises(AdapterError):
                        adapter.cleanup_runtime(deadline, None)
                    adapter._run.assert_not_called()
            for payload in ({}, {"schema": True, "request": None, "ack": None, "resource": None},
                            {"schema": 1, "request": None, "ack": None}):
                adapter._status.return_value = payload
                self.assertFalse(adapter._retired_singleton_is_superseded(deadline, None))
            adapter._status.return_value = {"schema": 1, "request": None, "ack": None, "resource": None}
            adapter._singleton_process_identity.side_effect = None
            adapter._singleton_process_identity.return_value = None
            self.assertFalse(adapter._retired_singleton_is_superseded(deadline, None))
            adapter._singleton_process_identity.side_effect = lambda name: (1 if name == "soren_loop.sh" else 2, 10)
            active = state["active"]
            # A Soren owner in the active slot must still be a distinct lease.
            for change in ({"game": "soren91", "adapter": "soren"},
                           {"lease_id": adapter.spec.lease_id}, {"lease_id": None}):
                state["active"] = {**active, **change}
                store.canonical.save(state)
                self.assertFalse(adapter._retired_singleton_is_superseded(deadline, None))
            state["active"] = active
            state["retiring"] = []
            store.canonical.save(state)
            self.assertFalse(adapter._retired_singleton_is_superseded(deadline, None))

    def test_cli_active_does_not_release_a_live_retiring_singleton(self):
        """A CLI owner cannot inherit the live Soren singleton processes.

        The restoring switch failed on adapter readiness, so canonical restored
        the CLI game as active while the old Soren generation stayed in
        ``retiring``. An idle broker has lost the original stop proof; the
        live singleton must stay tracked until a stop or Soren successor can
        be proven.
        """
        with tempfile.TemporaryDirectory() as temp:
            adapter, store, state = self.make_retired_singleton(Path(temp))
            self.swap_active_to_cli(store, state)
            adapter.spec = RuntimeSpec.from_runtime(adapter.g.state_dir, state["retiring"][0])
            deadline = time.monotonic() + 30
            self.assertFalse(adapter._retired_singleton_is_superseded(deadline, None))
            adapter._status.assert_not_called()
            adapter._singleton_process_identity.assert_not_called()
            self.assertTrue(adapter.alive(deadline, None))
            with self.assertRaises(AdapterError):
                adapter.cleanup_runtime(deadline, None)
            adapter._run.assert_not_called()

            def factory(spec):
                if spec.adapter != "soren":
                    # The active runtime is another game (nethack).  Its real
                    # adapter is out of scope here; only liveness matters.
                    # The factory return still has to satisfy the
                    # CoordinatorAdapter protocol and carry a matching name,
                    # exactly as a real cli_game.CliCoordinatorAdapter would.
                    instance = SimpleNamespace(
                        name=spec.adapter, agent_enabled=False,
                        preflight=lambda deadline, cancel: None,
                        materialize_runtime=lambda deadline, cancel: None,
                        readiness=lambda deadline, cancel: None,
                        alive=lambda deadline, cancel: True,
                        cleanup_runtime=lambda deadline, cancel: None,
                        start_agent=lambda deadline, cancel: None,
                        stop_agent=lambda deadline, cancel: None,
                        spec=spec,
                    )
                    return instance
                instance = SorenCoordinatorAdapter(adapter.g, adapter.game, spec)
                instance._status = adapter._status
                instance._live_process = adapter._live_process
                instance._singleton_process_identity = adapter._singleton_process_identity
                instance._run = adapter._run
                return instance
            before, _ = store.canonical.load()
            result = GameSwitchCoordinator(store, factory).recover(timeout_s=5)
            after, _ = store.canonical.load()
            self.assertEqual(result.status, "succeeded")
            self.assertTrue(result.cleanup_pending)
            self.assertEqual(after["active"], before["active"])
            self.assertEqual(after["retiring"], before["retiring"])
            adapter._run.assert_not_called()

    def test_cli_active_removes_retiring_only_after_original_stop_and_matching_ack(self):
        import uuid
        from test_adapter_stop_resume_safety import retirement
        from test_coordinator import FakeAdapterFactory
        for outcome in ("stopped", "missing-ack", "foreign-ack", "failed-ack", "stop-failed",
                        "ack-game", "ack-generation", "ack-deadline", "resource-owner",
                        "resource-stopping", "resource-missing", "request-deadline", "late-owner-change"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as temp:
                adapter, store, payload, request_id = retirement(Path(temp), status="stopping")
                before, _ = store.canonical.load()
                calls = []
                def controller(argv, *_args, **_kwargs):
                    calls.append(argv)
                    if outcome == "stop-failed":
                        return 1, {}
                    payload["ack"]["status"] = "stopped"
                    payload["resource"]["status"] = "stopped"
                    if outcome == "missing-ack": payload["ack"] = None
                    elif outcome == "foreign-ack": payload["ack"]["request_id"] = str(uuid.uuid4())
                    elif outcome == "failed-ack": payload["ack"]["status"] = "failed"
                    elif outcome == "ack-game": payload["ack"]["game"] = "nethack"
                    elif outcome == "ack-generation": payload["ack"]["generation"] += 1
                    elif outcome == "ack-deadline": payload["ack"]["deadline_epoch"] += 1
                    elif outcome == "resource-owner": payload["resource"]["generation"] += 1
                    elif outcome == "resource-stopping": payload["resource"]["status"] = "stopping"
                    elif outcome == "resource-missing": payload["resource"] = None
                    elif outcome == "request-deadline":
                        for key in ("request", "ack", "resource"):
                            payload[key]["deadline_epoch"] += 1
                    return 0, {}
                controller = Mock(side_effect=controller)
                processes = Mock(return_value=(1, 10))
                boundary = Mock(side_effect=AssertionError("must reuse original request"))
                fake = FakeAdapterFactory({"nethack": {}})
                active = fake(RuntimeSpec.from_runtime(store.state_dir, before["active"]))
                active.runtime.materialized = active.runtime.alive = active.runtime.agent_started = True
                def factory(spec):
                    if spec.adapter != "soren": return fake(spec)
                    fresh = SorenCoordinatorAdapter(adapter.g, adapter.game, spec)
                    def status(*_):
                        import copy
                        result = copy.deepcopy(payload)
                        if outcome == "late-owner-change" and result["ack"]["status"] == "stopped":
                            # The post-controller ACK passes; then the liveness
                            # probe observes replacement of the resource owner.
                            payload["resource"]["generation"] += 1
                        return result
                    fresh._status = Mock(side_effect=status)
                    fresh._run = controller
                    fresh._singleton_process_identity = processes
                    fresh.request_round_boundary = boundary
                    return fresh
                result = GameSwitchCoordinator(store, factory).recover(timeout_s=5)
                after, _ = store.canonical.load()
                self.assertEqual(result.status, "succeeded")
                self.assertEqual(bool(result.cleanup_pending), outcome != "stopped")
                self.assertEqual(after["active"], before["active"])
                self.assertEqual(after["retiring"], [] if outcome == "stopped" else before["retiring"])
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0], [str(adapter.control), "stop-after-boundary", request_id])
                processes.assert_not_called()
                boundary.assert_not_called()

    def test_no_stop_retirement_requires_the_retiring_lease(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter, store, state = self.make_retired_singleton(Path(temp))
            state["retiring"][0]["lease_id"] = None
            store.canonical.save(state)
            adapter.spec = RuntimeSpec.from_runtime(store.state_dir, state["retiring"][0])
            self.assertFalse(adapter._retired_singleton_is_superseded(time.monotonic() + 5, None))
            with self.assertRaises(AdapterError):
                adapter.cleanup_runtime(time.monotonic() + 5, None)
            self.assertEqual(store.canonical.load()[0]["retiring"], state["retiring"])
            adapter._run.assert_not_called()

    def test_foreign_game_active_still_refuses_with_live_broker_request(self):
        """Unknown retirement proof cannot borrow a live broker request."""
        with tempfile.TemporaryDirectory() as temp:
            adapter, store, state = self.make_retired_singleton(Path(temp))
            self.swap_active_to_cli(store, state)
            adapter.spec = RuntimeSpec.from_runtime(adapter.g.state_dir, state["retiring"][0])
            deadline = time.monotonic() + 30
            for field in ("request", "ack", "resource"):
                with self.subTest(field=field):
                    adapter._status.return_value = {
                        "schema": 1, "request": None, "ack": None, "resource": None,
                        field: {"status": "stopped"},
                    }
                    self.assertFalse(adapter._retired_singleton_is_superseded(deadline, None))
                    with self.assertRaises(AdapterError):
                        adapter.cleanup_runtime(deadline, None)
                    self.assertEqual(store.canonical.load()[0]["retiring"], state["retiring"])
                    adapter._run.assert_not_called()

    def test_foreign_game_active_rejects_in_flight_switch_and_other_adapters(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter, store, state = self.make_retired_singleton(Path(temp))
            self.swap_active_to_cli(store, state)
            adapter.spec = RuntimeSpec.from_runtime(adapter.g.state_dir, state["retiring"][0])
            adapter._status = Mock(return_value={"schema": 1, "request": None, "ack": None,
                                                "resource": None})
            deadline = time.monotonic() + 30
            import uuid
            in_flight = {
                "phase": ("starting", "switch", str(uuid.uuid4())),
                "request_id": ("starting", "switch", str(uuid.uuid4())),
                "candidate": ("starting", "switch", str(uuid.uuid4())),
                "previous": ("starting", "switch", str(uuid.uuid4())),
            }
            for field, (phase, operation, request_id) in in_flight.items():
                with self.subTest(field=field):
                    snapshot = dict(state)
                    snapshot["next_generation"] = 13
                    # Only an in-progress phase may carry operation/request_id;
                    # validate_state treats idle/ready/failed/recovery_required
                    # as stable phases that must keep neither.
                    snapshot["phase"], snapshot["operation"] = phase, operation
                    snapshot["request_id"] = request_id
                    if field == "candidate":
                        snapshot["candidate"] = self.cli_active(11)
                    if field == "previous":
                        snapshot["previous"] = self.cli_active(12)
                    store.canonical.save(snapshot)
                    self.assertFalse(adapter._retired_singleton_is_superseded(deadline, None))
                    adapter._run.assert_not_called()
            store.canonical.save(state)
            # canonical requires a non-empty adapter string, so an "unknown"
            # adapter means a valid one that is neither soren nor a cli corner
            # (retroarch/browser/...), plus a second Soren game identity.
            for adapter_kind in ("retroarch", "browser", "soren91"):
                with self.subTest(adapter=adapter_kind):
                    state["active"] = {**self.cli_active(10), "adapter": adapter_kind}
                    store.canonical.save(state)
                    self.assertFalse(adapter._retired_singleton_is_superseded(deadline, None))
                    adapter._run.assert_not_called()

    def test_retired_singleton_rejects_owner_change_during_probe(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter, store, state = self.make_retired_singleton(Path(temp))
            def probe(_deadline, _cancel):
                import uuid
                state["active"]["lease_id"] = str(uuid.uuid4())
                store.canonical.save(state)
                return {"schema": 1, "request": None, "ack": None, "resource": None}
            adapter._status.side_effect = probe
            self.assertFalse(adapter._retired_singleton_is_superseded(time.monotonic() + 5, None))

    def test_no_stop_recovery_requires_real_programs_at_fixed_root(self):
        cases = ("valid", "data-arguments", "foreign-root", "single-reporter", "duplicate-loop")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                adapter, store, before = self.make_retired_singleton(root)
                proc = root / "fake-proc"
                proc.mkdir()
                foreign = root / "other"
                foreign.mkdir()
                for directory in (root, foreign):
                    for name in ("soren_loop.sh", "soviet_watchdog.sh", "report.sh"):
                        (directory / name).write_text("#!/bin/bash\n")
                def process(pid, argv, cwd):
                    entry = proc / str(pid)
                    entry.mkdir()
                    (entry / "cmdline").write_bytes(("\0".join(argv) + "\0").encode())
                    (entry / "stat").write_text(f"{pid} (bash) " + " ".join(["S"] + ["0"] * 18 + ["10"]))
                    (entry / "cwd").symlink_to(cwd, target_is_directory=True)
                    (entry / "exe").symlink_to("/bin/bash")
                if case == "single-reporter":
                    process(11, ["python3", "report.sh", "--loop", "./soren_loop.sh",
                                 "--watchdog", "./soviet_watchdog.sh"], root)
                else:
                    directory = foreign if case == "foreign-root" else root
                    for pid, name in ((11, "soren_loop.sh"), (12, "soviet_watchdog.sh")):
                        argv = ["/bin/bash", "./" + name]
                        if case == "data-arguments":
                            argv = ["/bin/bash", "./report.sh", "--input", str(root / name)]
                        process(pid, argv, directory)
                    if case == "duplicate-loop":
                        process(13, ["/bin/bash", "./soren_loop.sh"], root)
                adapter._singleton_process_identity = lambda name: SorenCoordinatorAdapter._singleton_process_identity(
                    adapter, name, proc_root=proc)
                def factory(spec):
                    instance = SorenCoordinatorAdapter(adapter.g, adapter.game, spec)
                    instance._status, instance._live_process = adapter._status, adapter._live_process
                    instance._singleton_process_identity = adapter._singleton_process_identity
                    instance._run = adapter._run
                    return instance
                result = GameSwitchCoordinator(store, factory).recover(timeout_s=5)
                after, _ = store.canonical.load()
                self.assertEqual(after["active"], before["active"])
                self.assertEqual(after["retiring"], [] if case == "valid" else before["retiring"])
                self.assertEqual(bool(result.cleanup_pending), case != "valid")
                adapter._run.assert_not_called()

    def test_no_stop_proof_rejects_shared_pid_and_process_replacement(self):
        for identities in ([(1, 10), (1, 10)], [(1, 10), (2, 10), (1, 11)]):
            with self.subTest(identities=identities), tempfile.TemporaryDirectory() as temp:
                adapter, store, before = self.make_retired_singleton(Path(temp))
                adapter._singleton_process_identity.side_effect = identities
                self.assertFalse(adapter._retired_singleton_is_superseded(time.monotonic() + 5, None))
                self.assertEqual(store.canonical.load()[0]["retiring"], before["retiring"])
                adapter._run.assert_not_called()

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
            adapter = self.make_stateful_adapter(root)
            blocker = root / "blocker"
            blocker.write_text("file, not dir")
            adapter._diagnostic_log_path = Mock(return_value=blocker / "log")
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

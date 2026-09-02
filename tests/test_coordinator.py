"""P1 coordinator tests: state machine, idempotency, failure/rollback and
crash boundaries over the P0 store with fake replace-mode adapters."""

import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import game_switch  # noqa: E402
from docich.adapters import AdapterError  # noqa: E402
from docich.naming import runtime_names  # noqa: E402


class InjectedCrash(RuntimeError):
    pass


class FailN:
    """Raises ``exc`` for the first ``n`` calls, then succeeds."""

    def __init__(self, exc, n=1):
        self.exc = exc
        self.n = n

    def __call__(self):
        if self.n > 0:
            self.n -= 1
            raise self.exc


class FailAfter:
    """Succeeds for the first ``after`` calls, then raises ``exc``."""

    def __init__(self, exc, after=1):
        self.exc = exc
        self.after = after
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls > self.after:
            raise self.exc


class FailOn:
    """Raises ``exc`` exactly on the ``n``-th call, succeeds otherwise."""

    def __init__(self, exc, n):
        self.exc = exc
        self.n = n
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls == self.n:
            raise self.exc


class FakeRuntime:
    def __init__(self, spec):
        self.spec = spec
        self.materialized = False
        self.alive = False
        self.cleaned = False
        self.agent_started = False
        self.agent_stopped = False
        self.events = []


class FakeAdapter:
    name = "cli"

    def __init__(self, spec, behavior, runtime):
        self.spec = spec
        self.behavior = behavior
        self.runtime = runtime
        self.agent_enabled = bool(behavior.get("agent_enabled", True))

    def _maybe_fail(self, key):
        value = self.behavior.get(key)
        if value is None:
            return
        if callable(value):
            value()
        else:
            raise value

    def preflight(self):
        self._maybe_fail("preflight_error")

    def materialize_runtime(self):
        self._maybe_fail("materialize_error")
        if not self.runtime.materialized:
            self.runtime.materialized = True
            self.runtime.alive = True
            self.runtime.events.append("materialize")

    def readiness(self, deadline):
        self._maybe_fail("readiness_error")
        if time.monotonic() >= deadline:
            raise game_switch.ReadinessTimeoutError("fake deadline exceeded")
        if self.runtime.materialized and not self.runtime.alive:
            raise game_switch.ReadinessTimeoutError("runtime dead before ready")
        self.runtime.events.append("readiness")

    def alive(self):
        return self.runtime.materialized and self.runtime.alive and not self.runtime.cleaned

    def cleanup_runtime(self):
        self._maybe_fail("cleanup_error")
        if self.behavior.get("immortal"):
            return
        if self.runtime.alive:
            self.runtime.alive = False
            self.runtime.cleaned = True
            self.runtime.events.append("cleanup")

    def start_agent(self):
        self._maybe_fail("agent_start_error")
        self.runtime.agent_started = True
        self.runtime.events.append("agent_start")

    def stop_agent(self):
        self._maybe_fail("agent_stop_error")
        self.runtime.agent_stopped = True
        self.runtime.events.append("agent_stop")


class FakeAdapterFactory:
    """Resolves ``(game, generation)`` to one persistent adapter instance."""

    def __init__(self, behaviors):
        self.behaviors = behaviors
        self.adapters = {}

    def __call__(self, spec):
        if spec.game not in self.behaviors:
            raise AdapterError(f"ゲーム定義が見つかりません: {spec.game}")
        key = (spec.game, spec.generation)
        if key not in self.adapters:
            self.adapters[key] = FakeAdapter(spec, self.behaviors[spec.game], FakeRuntime(spec))
        return self.adapters[key]

    def adapter(self, game, generation):
        return self.adapters.get((game, generation))


def _runtime_dict(generation, game):
    names = runtime_names(generation)
    return {
        "game": game,
        "adapter": "cli",
        "generation": generation,
        "runtime_id": f"g{generation}-abcdef",
        "lease_id": str(uuid.uuid4()),
        "game_window": names.game_window,
        "agent_window": names.agent_window,
        "adapter_session": names.adapter_session,
        "started_at": "2026-09-03T00:00:00Z",
    }


class CoordinatorTestBase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.state_dir = Path(self.tempdir.name) / "run"
        self.store = game_switch.GameSwitchStore(self.state_dir)
        self.behaviors = {
            "nethack": {},
            "robots": {},
            "hanjuku": {},
        }
        self.factory = FakeAdapterFactory(self.behaviors)
        self.coordinator = game_switch.GameSwitchCoordinator(
            self.store,
            self.factory,
            quiesce_verify_timeout_s=0.3,
            poll_interval_s=0.01,
            default_timeout_s=60,
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def mirror_text(self):
        path = self.state_dir / "current_game"
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8").strip()

    def canonical(self):
        state, _ = self.store.canonical.load()
        return state

    def _crash_on_replace(self, n):
        count = {"n": 0}

        def hook(stage, _path):
            if stage == "after_replace":
                count["n"] += 1
                if count["n"] == n:
                    raise InjectedCrash(f"replace#{n}")

        return hook


class TestStart(CoordinatorTestBase):
    def test_start_success(self):
        result = self.coordinator.start("nethack")
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.to_game, "nethack")
        self.assertEqual(result.generation, 1)
        state = self.canonical()
        self.assertEqual(state["phase"], "ready")
        self.assertEqual(state["active"]["game"], "nethack")
        self.assertEqual(state["active"]["generation"], 1)
        self.assertEqual(state["active"]["lease_id"], self.factory.adapter("nethack", 1).spec.lease_id)
        self.assertEqual(self.mirror_text(), "nethack")
        adapter = self.factory.adapter("nethack", 1)
        self.assertEqual(adapter.runtime.events, ["materialize", "readiness", "agent_start"])
        receipt = self.store.receipts.load(result.request_id)
        self.assertEqual(receipt["status"], "succeeded")

    def test_start_unknown_game_fails_closed(self):
        result = self.coordinator.start("nosuchgame")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, game_switch.ERROR_INVALID_GAME)
        state = self.canonical()
        self.assertEqual(state["phase"], "failed")
        self.assertIsNone(state["active"])
        self.assertIsNone(state["candidate"])
        self.assertEqual(self.mirror_text(), None)

    def test_start_when_other_game_active_requests_switch(self):
        self.coordinator.start("nethack")
        result = self.coordinator.start("robots")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, game_switch.ERROR_ALREADY_ACTIVE)
        state = self.canonical()
        self.assertEqual(state["phase"], "ready")
        self.assertEqual(state["active"]["game"], "nethack")

    def test_start_same_game_is_noop_without_generation_consumption(self):
        first = self.coordinator.start("nethack")
        self.assertEqual(first.generation, 1)
        second = self.coordinator.start("nethack")
        self.assertEqual(second.status, "succeeded")
        self.assertEqual(second.detail, "既に起動中です")
        state = self.canonical()
        self.assertEqual(state["next_generation"], 2)
        self.assertEqual(len(self.store.receipts.receipts()), 1)


class TestSwitch(CoordinatorTestBase):
    def test_switch_success_stops_old_then_starts_candidate(self):
        self.coordinator.start("nethack")
        result = self.coordinator.switch("robots")
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.from_game, "nethack")
        self.assertEqual(result.to_game, "robots")
        self.assertEqual(result.generation, 2)
        state = self.canonical()
        self.assertEqual(state["phase"], "ready")
        self.assertEqual(state["active"]["game"], "robots")
        self.assertEqual(state["active"]["generation"], 2)
        self.assertEqual(state["retiring"], [])
        self.assertIsNone(state["previous"])
        self.assertEqual(self.mirror_text(), "robots")
        old = self.factory.adapter("nethack", 1)
        new = self.factory.adapter("robots", 2)
        self.assertEqual(old.runtime.events, ["materialize", "readiness", "agent_start", "agent_stop", "cleanup"])
        self.assertEqual(new.runtime.events, ["materialize", "readiness", "agent_start"])
        self.assertEqual(old.runtime.events.index("agent_stop") < old.runtime.events.index("cleanup"), True)

    def test_switch_same_game_is_restart_with_new_generation(self):
        self.coordinator.start("nethack")
        result = self.coordinator.switch("nethack")
        self.assertEqual(result.status, "succeeded")
        state = self.canonical()
        self.assertEqual(state["active"]["game"], "nethack")
        self.assertEqual(state["active"]["generation"], 2)
        self.assertEqual(state["retiring"], [])

    def test_switch_from_idle_is_start(self):
        result = self.coordinator.switch("robots")
        self.assertEqual(result.status, "succeeded")
        state = self.canonical()
        self.assertEqual(state["active"]["game"], "robots")
        self.assertEqual(state["active"]["generation"], 1)
        self.assertIsNone(state["previous"])

    def test_switch_disables_agent_when_target_has_none(self):
        self.behaviors["robots"]["agent_enabled"] = False
        self.coordinator.start("nethack")
        result = self.coordinator.switch("robots")
        self.assertEqual(result.status, "succeeded")
        new = self.factory.adapter("robots", 2)
        self.assertNotIn("agent_start", new.runtime.events)
        self.assertEqual(self.canonical()["active"]["game"], "robots")


class TestRestart(CoordinatorTestBase):
    def test_restart_uses_new_generation(self):
        self.coordinator.start("nethack")
        result = self.coordinator.restart()
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.to_game, "nethack")
        state = self.canonical()
        self.assertEqual(state["active"]["game"], "nethack")
        self.assertEqual(state["active"]["generation"], 2)

    def test_restart_without_active(self):
        result = self.coordinator.restart()
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, game_switch.ERROR_NO_ACTIVE_GAME)
        self.assertEqual(self.canonical()["phase"], "idle")


class TestStop(CoordinatorTestBase):
    def test_stop_stops_agent_before_game(self):
        self.coordinator.start("nethack")
        result = self.coordinator.stop()
        self.assertEqual(result.status, "succeeded")
        state = self.canonical()
        self.assertEqual(state["phase"], "idle")
        self.assertIsNone(state["active"])
        self.assertEqual(self.mirror_text(), None)
        adapter = self.factory.adapter("nethack", 1)
        self.assertEqual(adapter.runtime.events[-2:], ["agent_stop", "cleanup"])

    def test_stop_when_idle_is_noop(self):
        result = self.coordinator.stop()
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.detail, "実行中のゲームはありません")
        self.assertEqual(self.canonical()["phase"], "idle")
        self.assertEqual(self.store.receipts.receipts(), [])


class TestRotate(CoordinatorTestBase):
    def test_rotate_wraps_through_rotation_list(self):
        self.coordinator.start("nethack")
        first = self.coordinator.rotate(["nethack", "robots"])
        self.assertEqual(first.status, "succeeded")
        self.assertEqual(first.to_game, "robots")
        second = self.coordinator.rotate(["nethack", "robots"])
        self.assertEqual(second.status, "succeeded")
        self.assertEqual(second.to_game, "nethack")

    def test_rotate_without_active_starts_first(self):
        result = self.coordinator.rotate(["robots", "nethack"])
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(self.canonical()["active"]["game"], "robots")

    def test_rotate_rejects_invalid_games(self):
        result = self.coordinator.rotate([])
        self.assertEqual(result.error_code, game_switch.ERROR_INVALID_ROTATION)
        result = self.coordinator.rotate(["Bad!Name"])
        self.assertEqual(result.error_code, game_switch.ERROR_INVALID_ROTATION)
        self.assertEqual(self.canonical()["phase"], "idle")


class TestRequestIdempotency(CoordinatorTestBase):
    def test_terminal_resend_returns_same_result_without_new_generation(self):
        request_id = str(uuid.uuid4())
        payload = {"timeout": 30}
        first = self.coordinator.switch("robots", request_id=request_id, payload=payload)
        self.assertEqual(first.status, "succeeded")
        next_generation = self.canonical()["next_generation"]
        second = self.coordinator.switch("robots", request_id=request_id, payload=payload)
        self.assertEqual(second.status, "succeeded")
        self.assertEqual(second.generation, first.generation)
        self.assertEqual(second.request_id, request_id)
        self.assertEqual(self.canonical()["next_generation"], next_generation)

    def test_resend_with_different_payload_is_conflict(self):
        request_id = str(uuid.uuid4())
        self.coordinator.switch("robots", request_id=request_id, payload={"a": 1})
        result = self.coordinator.switch("robots", request_id=request_id, payload={"a": 2})
        self.assertEqual(result.status, "request_conflict")
        self.assertEqual(result.error_code, game_switch.ERROR_REQUEST_CONFLICT)

    def test_other_request_is_busy_while_canonical_in_progress(self):
        first_id = str(uuid.uuid4())
        self.coordinator.start("nethack")
        self.coordinator.crash_hook = self._crash_on_replace(1)
        with self.assertRaises(InjectedCrash):
            self.coordinator.switch("robots", request_id=first_id)
        self.coordinator.crash_hook = None
        self.assertEqual(self.canonical()["phase"], "preparing")

        result = self.coordinator.switch("robots", request_id=str(uuid.uuid4()))
        self.assertEqual(result.status, "busy")
        self.assertEqual(result.error_code, game_switch.ERROR_BUSY)

    def test_lock_contention_returns_busy_or_in_progress(self):
        held = game_switch.GameSwitchLock(self.state_dir).acquire(exclusive=True)
        try:
            result = self.coordinator.start("nethack")
            self.assertEqual(result.status, "busy")
            self.assertEqual(result.error_code, game_switch.ERROR_BUSY)
        finally:
            held.release()


class TestFailureRollback(CoordinatorTestBase):
    def test_preflight_failure_keeps_previous_alive_with_new_lease(self):
        self.behaviors["robots"]["preflight_error"] = AdapterError("preflight boom")
        self.coordinator.start("nethack")
        result = self.coordinator.switch("robots")
        self.assertEqual(result.status, "rolled_back")
        state = self.canonical()
        self.assertEqual(state["phase"], "ready")
        self.assertEqual(state["active"]["game"], "nethack")
        self.assertEqual(state["active"]["generation"], 1)
        self.assertEqual(state["candidate"], None)
        receipt = self.store.receipts.load(result.request_id)
        self.assertEqual(receipt["status"], "rolled_back")
        old = self.factory.adapter("nethack", 1)
        self.assertEqual(old.runtime.events.count("agent_start"), 2)
        self.assertNotEqual(state["active"]["lease_id"], old.spec.lease_id)
        self.assertEqual(self.mirror_text(), "nethack")

    def test_materialize_failure_restarts_previous_with_new_generation(self):
        self.behaviors["robots"]["materialize_error"] = AdapterError("start boom")
        self.coordinator.start("nethack")
        result = self.coordinator.switch("robots")
        self.assertEqual(result.status, "rolled_back")
        state = self.canonical()
        self.assertEqual(state["phase"], "ready")
        self.assertEqual(state["active"]["game"], "nethack")
        self.assertEqual(state["active"]["generation"], 3)
        self.assertEqual(result.generation, 2)
        self.assertEqual(self.mirror_text(), "nethack")
        retired = self.factory.adapter("nethack", 1)
        self.assertTrue(retired.runtime.cleaned)
        self.assertIsNone(self.canonical()["candidate"])

    def test_readiness_timeout_restarts_previous(self):
        self.behaviors["robots"]["readiness_error"] = game_switch.ReadinessTimeoutError("slow")
        self.coordinator.start("nethack")
        result = self.coordinator.switch("robots")
        self.assertEqual(result.status, "rolled_back")
        state = self.canonical()
        self.assertEqual(state["active"]["game"], "nethack")
        self.assertEqual(state["active"]["generation"], 3)

    def test_agent_start_failure_restarts_previous(self):
        self.behaviors["robots"]["agent_start_error"] = AdapterError("agent boom")
        self.coordinator.start("nethack")
        result = self.coordinator.switch("robots")
        self.assertEqual(result.status, "rolled_back")
        state = self.canonical()
        self.assertEqual(state["active"]["game"], "nethack")

    def test_start_failure_without_previous_leaves_failed(self):
        self.behaviors["robots"]["materialize_error"] = AdapterError("start boom")
        result = self.coordinator.start("robots")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, game_switch.ERROR_START_FAILED)
        state = self.canonical()
        self.assertEqual(state["phase"], "failed")
        self.assertIsNone(state["active"])
        self.assertEqual(self.mirror_text(), None)
        receipt = self.store.receipts.load(result.request_id)
        self.assertEqual(receipt["status"], "failed")

    def test_quiesce_failure_keeps_previous_with_new_lease(self):
        self.behaviors["nethack"]["immortal"] = True
        self.coordinator.start("nethack")
        old_lease = self.canonical()["active"]["lease_id"]
        result = self.coordinator.switch("robots")
        self.assertEqual(result.status, "rolled_back")
        state = self.canonical()
        self.assertEqual(state["phase"], "ready")
        self.assertEqual(state["active"]["game"], "nethack")
        self.assertEqual(state["active"]["generation"], 1)
        self.assertNotEqual(state["active"]["lease_id"], old_lease)
        old = self.factory.adapter("nethack", 1)
        self.assertEqual(old.runtime.events.count("agent_start"), 2)

    def test_rollback_failure_leaves_failed_then_recover_restores(self):
        self.behaviors["nethack"]["materialize_error"] = FailOn(AdapterError("rollback boom"), 2)
        self.behaviors["robots"]["readiness_error"] = game_switch.ReadinessTimeoutError("slow")
        self.coordinator.start("nethack")
        result = self.coordinator.switch("robots")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_code, game_switch.ERROR_ROLLBACK_FAILED)
        state = self.canonical()
        self.assertEqual(state["phase"], "failed")
        self.assertIsNone(state["active"])
        self.assertEqual(state["previous"]["generation"], 1)
        self.assertIsNone(state["candidate"])

        recovered = self.coordinator.recover()
        self.assertEqual(recovered.status, "rolled_back")
        state = self.canonical()
        self.assertEqual(state["phase"], "ready")
        self.assertEqual(state["active"]["game"], "nethack")
        self.assertEqual(state["active"]["generation"], 4)
        self.assertEqual(self.mirror_text(), "nethack")

    def test_failed_phase_blocks_new_requests_until_recover(self):
        self.behaviors["robots"]["materialize_error"] = AdapterError("start boom")
        self.coordinator.start("robots")
        blocked = self.coordinator.start("nethack")
        self.assertEqual(blocked.status, "failed")
        self.assertEqual(blocked.error_code, game_switch.ERROR_RECOVERY_REQUIRED)
        recovered = self.coordinator.recover()
        self.assertEqual(recovered.status, "succeeded")
        self.assertEqual(self.canonical()["phase"], "idle")
        self.coordinator.start("nethack")
        self.assertEqual(self.canonical()["active"]["game"], "nethack")


class TestCleanupPending(CoordinatorTestBase):
    def test_retiring_cleanup_failure_is_recorded_as_pending(self):
        self.behaviors["nethack"]["cleanup_error"] = FailAfter(AdapterError("cleanup boom"))
        self.coordinator.start("nethack")
        result = self.coordinator.switch("robots")
        self.assertEqual(result.status, "succeeded")
        self.assertTrue(result.cleanup_pending)
        state = self.canonical()
        self.assertEqual(state["phase"], "ready")
        self.assertEqual(state["active"]["game"], "robots")
        self.assertEqual(len(state["retiring"]), 1)
        self.assertEqual(state["retiring"][0]["game"], "nethack")

    def test_candidate_cleanup_failure_on_rollback_is_recorded(self):
        self.behaviors["robots"]["readiness_error"] = game_switch.ReadinessTimeoutError("slow")
        self.behaviors["robots"]["cleanup_error"] = AdapterError("candidate cleanup boom")
        self.coordinator.start("nethack")
        result = self.coordinator.switch("robots")
        self.assertEqual(result.status, "rolled_back")
        state = self.canonical()
        self.assertEqual(state["phase"], "ready")
        self.assertEqual(state["active"]["game"], "nethack")
        self.assertTrue(result.cleanup_pending)
        self.assertIsNone(state["candidate"])
        pending = [r["game"] for r in state["retiring"]]
        self.assertIn("robots", pending)

    def test_recover_cleans_pending_retiring(self):
        state, _ = self.store.canonical.load()
        active = _runtime_dict(2, "robots")
        state.update(
            {
                "phase": "ready",
                "active": active,
                "retiring": [_runtime_dict(1, "nethack")],
                "next_generation": 3,
                "last_result": {
                    "request_id": str(uuid.uuid4()),
                    "operation": "switch",
                    "status": "succeeded",
                    "from_game": "nethack",
                    "to_game": "robots",
                    "generation": 2,
                },
            }
        )
        self.store.canonical.save(state)
        # The crafted active runtime must look alive to the adapter factory.
        adapter = self.factory(game_switch.RuntimeSpec.from_runtime(self.state_dir, active))
        adapter.runtime.materialized = True
        adapter.runtime.alive = True
        result = self.coordinator.recover()
        self.assertEqual(result.status, "succeeded")
        state = self.canonical()
        self.assertEqual(state["retiring"], [])
        self.assertEqual(self.mirror_text(), "robots")


class TestMirror(CoordinatorTestBase):
    def test_mirror_failure_is_warning_not_rollback(self):
        def failing_mirror(_path, _game):
            raise OSError("mirror boom")

        self.coordinator.mirror_writer = failing_mirror
        result = self.coordinator.start("nethack")
        self.assertEqual(result.status, "succeeded")
        self.assertTrue(any("mirror" in warning for warning in result.warnings))
        self.assertEqual(self.canonical()["phase"], "ready")

    def test_default_mirror_is_atomic_text(self):
        self.coordinator.start("nethack")
        self.assertEqual(self.mirror_text(), "nethack")
        self.coordinator.stop()
        self.assertEqual(self.mirror_text(), None)


class TestCrashBoundaries(CoordinatorTestBase):
    def test_crash_during_accept_retry_converges_via_recovery(self):
        request_id = str(uuid.uuid4())
        payload = {"p": 1}

        def crash_receipt(stage, _path):
            if stage == "receipt_after_replace":
                raise InjectedCrash(stage)

        self.coordinator.start("nethack")
        self.coordinator.crash_hook = crash_receipt
        with self.assertRaises(InjectedCrash):
            self.coordinator.switch("robots", request_id=request_id, payload=payload)
        self.coordinator.crash_hook = None
        retried = self.coordinator.switch("robots", request_id=request_id, payload=payload)
        self.assertEqual(retried.status, "rolled_back")
        state = self.canonical()
        self.assertEqual(state["phase"], "ready")
        self.assertEqual(state["active"]["game"], "nethack")
        self.assertEqual(state["active"]["generation"], 1)
        receipt = self.store.receipts.load(request_id)
        self.assertEqual(receipt["status"], "rolled_back")

    def test_crash_mid_switch_retry_rolls_back(self):
        request_id = str(uuid.uuid4())
        self.coordinator.start("nethack")
        self.coordinator.crash_hook = self._crash_on_replace(3)  # starting transition
        with self.assertRaises(InjectedCrash):
            self.coordinator.switch("robots", request_id=request_id)
        state = self.canonical()
        self.assertEqual(state["phase"], "starting")
        self.assertEqual(state["candidate"]["game"], "robots")

        self.coordinator.crash_hook = None
        retried = self.coordinator.switch("robots", request_id=request_id)
        self.assertEqual(retried.status, "rolled_back")
        state = self.canonical()
        self.assertEqual(state["phase"], "ready")
        self.assertEqual(state["active"]["game"], "nethack")
        self.assertEqual(state["active"]["generation"], 3)
        candidate = self.factory.adapter("robots", 2)
        self.assertFalse(candidate.runtime.alive)
        self.assertIsNone(state["candidate"])

    def test_crash_after_commit_retry_returns_terminal_result(self):
        request_id = str(uuid.uuid4())
        self.coordinator.start("nethack")
        self.coordinator.crash_hook = self._crash_on_replace(5)  # commit transition
        with self.assertRaises(InjectedCrash):
            self.coordinator.switch("robots", request_id=request_id)
        state = self.canonical()
        self.assertEqual(state["phase"], "ready")
        self.assertEqual(state["active"]["game"], "robots")
        self.assertEqual(state["operation"], None)

        self.coordinator.crash_hook = None
        retried = self.coordinator.switch("robots", request_id=request_id)
        self.assertEqual(retried.status, "succeeded")
        self.assertEqual(retried.to_game, "robots")
        receipt = self.store.receipts.load(request_id)
        self.assertEqual(receipt["status"], "succeeded")
        self.assertEqual(self.canonical()["phase"], "ready")

    def test_crash_during_stop_retry_completes_stop(self):
        request_id = str(uuid.uuid4())
        self.coordinator.start("nethack")
        self.coordinator.crash_hook = self._crash_on_replace(1)  # quiescing transition
        with self.assertRaises(InjectedCrash):
            self.coordinator.stop(request_id=request_id)
        self.assertEqual(self.canonical()["phase"], "quiescing")

        self.coordinator.crash_hook = None
        retried = self.coordinator.stop(request_id=request_id)
        self.assertEqual(retried.status, "succeeded")
        state = self.canonical()
        self.assertEqual(state["phase"], "idle")
        self.assertIsNone(state["active"])
        receipt = self.store.receipts.load(request_id)
        self.assertEqual(receipt["status"], "succeeded")

    def test_crash_before_receipt_finish_recover_reconciles(self):
        request_id = str(uuid.uuid4())
        self.coordinator.start("nethack")
        self.coordinator.crash_hook = self._crash_on_replace(5)
        with self.assertRaises(InjectedCrash):
            self.coordinator.switch("robots", request_id=request_id)
        self.assertEqual(self.store.receipts.load(request_id)["status"], "accepted")

        result = self.coordinator.recover()
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(self.store.receipts.load(request_id)["status"], "succeeded")
        self.assertEqual(self.canonical()["phase"], "ready")

    def test_crash_at_start_retry_fails_then_recover_resets(self):
        request_id = str(uuid.uuid4())
        self.coordinator.crash_hook = self._crash_on_replace(1)  # preparing transition
        with self.assertRaises(InjectedCrash):
            self.coordinator.start("robots", request_id=request_id)
        self.assertEqual(self.canonical()["phase"], "preparing")

        self.coordinator.crash_hook = None
        retried = self.coordinator.start("robots", request_id=request_id)
        self.assertEqual(retried.status, "failed")
        self.assertEqual(self.canonical()["phase"], "failed")

        recovered = self.coordinator.recover()
        self.assertEqual(recovered.status, "succeeded")
        self.assertEqual(self.canonical()["phase"], "idle")
        receipt = self.store.receipts.load(request_id)
        self.assertEqual(receipt["status"], "failed")


class TestStateConsistency(CoordinatorTestBase):
    def test_corrupt_canonical_fails_closed(self):
        self.store.initialize()
        state, _ = self.store.canonical.load()
        state["phase"] = "bogus"
        game_switch.atomic_write_json(self.state_dir / "game_switch.json", state)
        with self.assertRaises(game_switch.StateCorruptError):
            self.coordinator.start("nethack")

    def test_dangling_in_progress_canonical_blocks_new_request(self):
        request_id = str(uuid.uuid4())
        self.store.initialize()
        self.store.canonical.transition(
            {"idle"}, "starting",
            updates={
                "operation": "switch",
                "request_id": request_id,
                "candidate": _runtime_dict(1, "robots"),
                "next_generation": 2,
            },
        )
        result = self.coordinator.start("nethack")
        self.assertEqual(result.status, "busy")
        self.assertEqual(result.error_code, game_switch.ERROR_BUSY)

    def test_crash_mid_rollback_retry_converges(self):
        request_id = str(uuid.uuid4())
        self.behaviors["robots"]["readiness_error"] = game_switch.ReadinessTimeoutError("slow")
        self.coordinator.start("nethack")
        # Switch transitions: preparing#1, quiescing#2, starting#3, probing#4,
        # then rollback: rolling_back#5, candidate-clear#6, and the rollback
        # generation allocation is #7.
        self.coordinator.crash_hook = self._crash_on_replace(7)
        with self.assertRaises(InjectedCrash):
            self.coordinator.switch("robots", request_id=request_id)
        state = self.canonical()
        self.assertEqual(state["phase"], "rolling_back")
        self.assertEqual(state["candidate"]["game"], "nethack")
        self.assertEqual(state["candidate"]["generation"], 3)
        self.assertEqual(state["next_generation"], 4)
        self.assertEqual(state["previous"]["generation"], 1)

        self.coordinator.crash_hook = None
        retried = self.coordinator.switch("robots", request_id=request_id)
        self.assertEqual(retried.status, "rolled_back")
        state = self.canonical()
        self.assertEqual(state["phase"], "ready")
        self.assertEqual(state["active"]["game"], "nethack")
        self.assertEqual(state["active"]["generation"], 4)
        receipt = self.store.receipts.load(request_id)
        self.assertEqual(receipt["status"], "rolled_back")


class TestGenerationContract(CoordinatorTestBase):
    def test_generations_are_never_reused(self):
        self.coordinator.start("nethack")
        self.coordinator.switch("robots")
        self.behaviors["nethack"]["materialize_error"] = AdapterError("boom")
        self.coordinator.switch("nethack")
        self.coordinator.recover()
        used = []
        state, _ = self.store.canonical.load()
        for key in ("active", "candidate", "previous"):
            if state.get(key):
                used.append(state[key]["generation"])
        used.extend(r["generation"] for r in state["retiring"])
        used.extend(int(r["generation"]) for r in self.store.receipts.receipts())
        self.assertEqual(len(used), len(set(used)), used)
        self.assertGreater(state["next_generation"], max(used))

    def test_request_failure_does_not_reuse_generation(self):
        first = self.coordinator.start("robots")
        self.assertEqual(first.generation, 1)
        self.coordinator.stop()
        second = self.coordinator.start("nethack")
        self.assertEqual(second.generation, 3)
        self.assertNotEqual(
            self.store.receipts.load(first.request_id)["runtime_id"],
            self.store.receipts.load(second.request_id)["runtime_id"],
        )


if __name__ == "__main__":
    unittest.main()
"""Round-boundary drain tests for the game-switch coordinator."""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import game_switch  # noqa: E402
from docich.naming import runtime_names  # noqa: E402


class BoundaryRuntime:
    def __init__(self, spec):
        self.spec = spec
        self.alive = False
        self.events: list[str] = []


class BoundaryAdapter:
    name = "cli"
    agent_enabled = False

    def __init__(self, spec, *, required=True, method=True):
        self.spec = spec
        self.runtime = BoundaryRuntime(spec)
        self.requires_round_boundary = required
        self.method_enabled = method
        self.boundary_request_ids: list[str] = []
        self.cancel_request_ids: list[str] = []
        self.boundary_entered = threading.Event()
        self.boundary_release = threading.Event()

    def preflight(self, deadline, cancel):
        return None

    def materialize_runtime(self, deadline, cancel):
        self.runtime.alive = True
        self.runtime.events.append("materialize")

    def readiness(self, deadline, cancel):
        self.runtime.events.append("readiness")

    def alive(self, deadline, cancel):
        return self.runtime.alive

    def cleanup_runtime(self, deadline, cancel):
        self.runtime.events.append("cleanup")
        self.runtime.alive = False

    def start_agent(self, deadline, cancel):
        return None

    def stop_agent(self, deadline, cancel):
        self.runtime.events.append("stop_agent")

    def request_round_boundary(self, request_id, deadline, cancel):
        if not self.method_enabled:
            raise AssertionError("unsupported adapter must not call this method")
        self.boundary_request_ids.append(request_id)
        self.boundary_entered.set()
        while not self.boundary_release.wait(0.01):
            if cancel.is_set() or time.monotonic() >= deadline:
                raise game_switch.ReadinessTimeoutError("boundary wait cancelled")
        self.runtime.events.append("boundary")

    def cancel_round_boundary(self, request_id, deadline, cancel):
        self.cancel_request_ids.append(request_id)
        self.runtime.events.append("cancel_boundary")
        self.boundary_release.set()
        return True


class BoundaryFactory:
    def __init__(self, *, required=True, method=True):
        self.required = required
        self.method = method
        self.adapters: dict[tuple[str, int], BoundaryAdapter] = {}

    def __call__(self, spec):
        key = (spec.game, spec.generation)
        if key not in self.adapters:
            self.adapters[key] = BoundaryAdapter(
                spec, required=self.required, method=self.method
            )
        return self.adapters[key]


def _coordinator(factory, state_dir):
    store = game_switch.GameSwitchStore(state_dir)
    coordinator = game_switch.GameSwitchCoordinator(
        store,
        factory,
        default_timeout_s=2.0,
        poll_interval_s=0.01,
        quiesce_verify_timeout_s=0.2,
        round_reacquire_timeout_s=0.5,
        step_timeouts=game_switch.StepTimeouts(
            preflight_s=0.5,
            round_boundary_s=0.2,
            stop_agent_s=0.5,
            start_s=0.5,
            agent_start_s=0.5,
            cleanup_s=0.5,
            probe_s=0.2,
        ),
    )
    return store, coordinator


def _wait_for_phase(store, phase):
    limit = time.monotonic() + 1.0
    while time.monotonic() < limit:
        state, _ = store.canonical.load()
        if state["phase"] == phase:
            return state
        time.sleep(0.01)
    raise AssertionError(f"phase {phase!r} was not reached")


def test_boundary_wait_releases_writer_and_orders_stop_after_ack():
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / "run"
        factory = BoundaryFactory()
        store, coordinator = _coordinator(factory, state_dir)
        assert coordinator.start("nethack").status == "succeeded"
        old = factory.adapters[("nethack", 1)]
        request_id = str(uuid.uuid4())
        result_box = []

        worker = threading.Thread(
            target=lambda: result_box.append(
                coordinator.switch("robots", request_id=request_id)
            )
        )
        worker.start()
        _wait_for_phase(store, "draining")
        assert old.boundary_entered.wait(1.0)

        # Input/observation's shared lock is obtainable while the writer is
        # waiting for the one-game boundary.
        with store.lock(exclusive=False, blocking=False):
            state, _ = store.canonical.load()
            assert state["active"]["game"] == "nethack"
            assert state["phase"] == "draining"

        same = coordinator.switch("robots", request_id=request_id)
        assert same.status == "in_progress"
        assert coordinator.restart(request_id=request_id).status == "request_conflict"
        other = coordinator.switch("hanjuku", request_id=str(uuid.uuid4()))
        assert other.status == "busy"

        old.boundary_release.set()
        worker.join(2.0)
        assert not worker.is_alive()
        assert result_box[0].status == "succeeded"
        assert old.boundary_request_ids == [request_id]
        assert old.runtime.events.index("boundary") < old.runtime.events.index("stop_agent")
        assert old.runtime.events.index("stop_agent") < old.runtime.events.index("cleanup")


def test_boundary_timeout_retains_old_active_without_cleanup():
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / "run"
        factory = BoundaryFactory()
        store, coordinator = _coordinator(factory, state_dir)
        assert coordinator.start("nethack").status == "succeeded"
        old = factory.adapters[("nethack", 1)]
        result = coordinator.switch("robots", timeout_s=0.2)
        assert result.status == "failed"
        assert result.error_code == game_switch.ERROR_TIMEOUT
        state, _ = store.canonical.load()
        assert state["phase"] == "ready"
        assert state["active"]["game"] == "nethack"
        assert old.runtime.alive
        assert "stop_agent" not in old.runtime.events
        assert "cleanup" not in old.runtime.events
        assert "cancel_boundary" in old.runtime.events
        assert old.cancel_request_ids == [result.request_id]


@pytest.mark.parametrize("cancel_mode", ["false", "error", "missing"])
def test_boundary_failure_keeps_draining_until_cancel_ack(cancel_mode):
    class FailingCancelAdapter(BoundaryAdapter):
        def cancel_round_boundary(self, request_id, deadline, cancel):
            self.runtime.events.append("cancel_boundary")
            self.boundary_release.set()
            if cancel_mode == "false":
                return False
            if cancel_mode == "error":
                raise RuntimeError("cancel broker unavailable")
            return True

    if cancel_mode == "missing":
        FailingCancelAdapter.cancel_round_boundary = None

    class FailingCancelFactory(BoundaryFactory):
        def __call__(self, spec):
            key = (spec.game, spec.generation)
            if key not in self.adapters:
                self.adapters[key] = FailingCancelAdapter(
                    spec, required=True, method=True
                )
            return self.adapters[key]

    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / "run"
        factory = FailingCancelFactory()
        store, coordinator = _coordinator(factory, state_dir)
        assert coordinator.start("nethack").status == "succeeded"
        old = factory.adapters[("nethack", 1)]
        result = coordinator.switch("robots", timeout_s=0.2)
        assert result.status == "failed"
        assert result.error_code == game_switch.ERROR_RECOVERY_REQUIRED
        state, _ = store.canonical.load()
        assert state["phase"] == "draining"
        assert state["active"]["game"] == "nethack"
        receipt = store.receipts.load(result.request_id)
        assert receipt is not None
        assert receipt["status"] == "accepted"
        assert "stop_agent" not in old.runtime.events
        assert "cleanup" not in old.runtime.events


def test_required_boundary_without_capability_fails_closed():
    class UnsupportedAdapter(BoundaryAdapter):
        request_round_boundary = None

    class UnsupportedFactory(BoundaryFactory):
        def __call__(self, spec):
            key = (spec.game, spec.generation)
            if key not in self.adapters:
                self.adapters[key] = UnsupportedAdapter(
                    spec, required=True, method=False
                )
            return self.adapters[key]

    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / "run"
        factory = UnsupportedFactory()
        store, coordinator = _coordinator(factory, state_dir)
        assert coordinator.start("nethack").status == "succeeded"
        old = factory.adapters[("nethack", 1)]
        result = coordinator.switch("robots")
        assert result.status == "failed"
        assert result.error_code == game_switch.ERROR_ROUND_BOUNDARY_UNSUPPORTED
        state, _ = store.canonical.load()
        assert state["phase"] == "ready"
        assert state["active"]["game"] == "nethack"
        assert old.runtime.alive
        assert "stop_agent" not in old.runtime.events


@pytest.mark.parametrize("legacy_cancel", [False, True])
def test_expired_draining_recovery_cancels_stale_driver_without_stop(legacy_cancel):
    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / "run"
        factory = BoundaryFactory()
        store, coordinator = _coordinator(factory, state_dir)
        assert coordinator.start("nethack").status == "succeeded"
        old = factory.adapters[("nethack", 1)]
        request_id = str(uuid.uuid4())
        if legacy_cancel:
            original_cancel = old.cancel_round_boundary
            old.cancel_round_boundary = lambda deadline, cancel: original_cancel(request_id, deadline, cancel)
        result_box = []
        worker = threading.Thread(
            target=lambda: result_box.append(
                coordinator.switch("robots", request_id=request_id, timeout_s=60.0)
            )
        )
        worker.start()
        _wait_for_phase(store, "draining")
        assert old.boundary_entered.wait(1.0)
        # Simulate a controller crash whose durable deadline has elapsed;
        # the adapter worker itself is still waiting and will acknowledge
        # late, after recovery has cancelled the request.
        with store.lock(exclusive=True, blocking=False):
            state, _ = store.canonical.load()
            state["deadline_at"] = "2000-01-01T00:00:00Z"
            store.canonical.save(state)

        recovered = coordinator.recover()
        assert recovered.status == "failed"
        assert recovered.error_code == game_switch.ERROR_TIMEOUT
        state, _ = store.canonical.load()
        assert state["phase"] == "ready"
        assert state["active"]["game"] == "nethack"
        assert old.runtime.alive

        # A delayed adapter acknowledgement is stale after recovery and must
        # not stop the game.  The real adapter follows the same request/active
        # identity check before any quiesce call.
        old.boundary_release.set()
        worker.join(2.0)
        assert not worker.is_alive()
        assert "stop_agent" not in old.runtime.events
        assert "cleanup" not in old.runtime.events
        assert result_box[0].status == "failed"
        assert old.cancel_request_ids == [request_id]


def test_boundary_wait_longer_than_reacquire_grace_succeeds():
    with tempfile.TemporaryDirectory() as tmp:
        factory = BoundaryFactory()
        store, coordinator = _coordinator(factory, Path(tmp) / "run")
        coordinator.round_reacquire_timeout_s = 0.1
        coordinator.step_timeouts = replace(coordinator.step_timeouts, round_boundary_s=1.0)
        assert coordinator.start("nethack").status == "succeeded"
        old = factory.adapters[("nethack", 1)]
        results = []
        worker = threading.Thread(target=lambda: results.append(coordinator.switch("robots")))
        worker.start()
        try:
            assert old.boundary_entered.wait(1.0)
            time.sleep(0.25)
            old.boundary_release.set()
            worker.join(2.0)
            assert not worker.is_alive()
            assert results[0].status == "succeeded"
        finally:
            old.boundary_release.set()
            worker.join(2.0)


def test_hung_boundary_cancel_is_short_and_keeps_input_unlocked():
    with tempfile.TemporaryDirectory() as tmp:
        factory = BoundaryFactory()
        store, coordinator = _coordinator(factory, Path(tmp) / "run")
        coordinator.step_timeouts = replace(coordinator.step_timeouts, round_cancel_s=0.15)
        coordinator.cancel_grace_s = 0.05
        assert coordinator.start("nethack").status == "succeeded"
        old = factory.adapters[("nethack", 1)]
        entered, release = threading.Event(), threading.Event()

        def hung_cancel(request_id, deadline, cancel):
            entered.set()
            release.wait(3.0)
            return False

        old.cancel_round_boundary = hung_cancel
        results = []
        started = time.monotonic()
        worker = threading.Thread(target=lambda: results.append(coordinator.switch("robots", timeout_s=0.2)))
        worker.start()
        try:
            assert entered.wait(1.0)
            with store.lock(exclusive=False, blocking=False):
                state, _ = store.canonical.load()
                assert state["phase"] == "draining"
                assert state["active"]["game"] == "nethack"
            worker.join(1.0)
            assert not worker.is_alive()
            assert time.monotonic() - started < 1.2
            assert results[0].error_code == game_switch.ERROR_RECOVERY_REQUIRED
            assert store.canonical.load()[0]["phase"] == "draining"
            assert "stop_agent" not in old.runtime.events
        finally:
            release.set()
            old.boundary_release.set()
            worker.join(2.0)

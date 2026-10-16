"""Live-drain cancellation gated on an adapter-proven terminal round.

The durable drain deadline is normally the only bound for a live ``draining``
phase: the current game must keep accepting input and the original boundary
driver may still be waiting.  An adapter that can positively prove the active
round already ended (``round_terminal``) has no boundary left to wait for, so
that one case may be cancelled early -- still only through the adapter's own
``cancel_round_boundary`` acknowledgement.

These tests pin both halves of that contract: every unproven, negative, failed
or timed-out report keeps the drain exactly as it was, and only a positive
report (with an acknowledged cancel) clears it while retaining the active
runtime.
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import game_switch  # noqa: E402


class TerminalRuntime:
    def __init__(self, spec):
        self.spec = spec
        self.alive = False
        self.events: list[str] = []


class TerminalAdapter:
    name = "cli"
    agent_enabled = False

    def __init__(self, spec, *, capability=False, terminal=False, cancel_ok=True,
                 cancel_releases=True, terminal_error=None, terminal_hangs=False):
        self.spec = spec
        self.runtime = TerminalRuntime(spec)
        self.requires_round_boundary = True
        self.round_boundary_timeout_s = None
        self.capability = capability
        if not capability:
            # getattr() must not find a callable: the capability is optional.
            self.round_terminal = None
        self.terminal = terminal
        self.cancel_ok = cancel_ok
        self.cancel_releases = cancel_releases
        self.terminal_error = terminal_error
        self.terminal_hangs = terminal_hangs
        self.boundary_request_ids: list[str] = []
        self.cancel_request_ids: list[str] = []
        self.terminal_calls = 0
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
        self.boundary_request_ids.append(request_id)
        self.boundary_entered.set()
        while not self.boundary_release.wait(0.01):
            if cancel.is_set() or time.monotonic() >= deadline:
                raise game_switch.ReadinessTimeoutError("boundary wait cancelled")
        self.runtime.events.append("boundary")

    def cancel_round_boundary(self, request_id, deadline, cancel):
        self.cancel_request_ids.append(request_id)
        self.runtime.events.append("cancel_boundary")
        if self.cancel_releases:
            self.boundary_release.set()
        return self.cancel_ok

    def round_terminal(self, deadline, cancel):
        self.terminal_calls += 1
        if self.terminal_hangs:
            while not cancel.wait(0.01):
                if time.monotonic() >= deadline:
                    raise game_switch.ReadinessTimeoutError("terminal probe timed out")
            raise game_switch.ReadinessTimeoutError("terminal probe cancelled")
        if self.terminal_error is not None:
            raise self.terminal_error
        return self.terminal


class TerminalFactory:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.adapters: dict[tuple[str, int], TerminalAdapter] = {}

    def __call__(self, spec):
        key = (spec.game, spec.generation)
        if key not in self.adapters:
            self.adapters[key] = TerminalAdapter(spec, **self.kwargs)
        return self.adapters[key]


def _coordinator(factory, state_dir, *, round_boundary_s=60.0):
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
            round_boundary_s=round_boundary_s,
            stop_agent_s=0.5,
            start_s=0.5,
            agent_start_s=0.5,
            cleanup_s=0.5,
            probe_s=0.2,
        ),
    )
    return store, coordinator


def _wait_for_phase(store, phase):
    limit = time.monotonic() + 2.0
    while time.monotonic() < limit:
        state, _ = store.canonical.load()
        if state["phase"] == phase:
            return state
        time.sleep(0.01)
    raise AssertionError(f"phase {phase!r} was not reached")


def _live_drain(factory, store, coordinator, *, request_id):
    """Start a boundary-requiring game and switch away, leaving a live drain."""

    assert coordinator.start("nethack").status == "succeeded"
    old = factory.adapters[("nethack", 1)]
    result_box: list[object] = []
    worker = threading.Thread(
        target=lambda: result_box.append(
            coordinator.switch("robots", request_id=request_id, timeout_s=60.0)
        )
    )
    worker.start()
    _wait_for_phase(store, "draining")
    assert old.boundary_entered.wait(2.0)
    return old, worker, result_box


def test_live_drain_without_terminal_capability_stays_busy():
    with tempfile.TemporaryDirectory() as tmp:
        factory = TerminalFactory(capability=False)
        store, coordinator = _coordinator(factory, Path(tmp) / "run")
        request_id = str(uuid.uuid4())
        old, worker, result_box = _live_drain(factory, store, coordinator, request_id=request_id)

        recovered = coordinator.recover()
        assert recovered.status == "busy"
        assert recovered.error_code == game_switch.ERROR_BUSY
        assert store.canonical.load()[0]["phase"] == "draining"
        assert old.cancel_request_ids == []
        assert old.terminal_calls == 0

        old.boundary_release.set()
        worker.join(3.0)
        assert not worker.is_alive()
        assert result_box[0].status == "succeeded"


def test_live_drain_with_live_round_stays_busy():
    with tempfile.TemporaryDirectory() as tmp:
        factory = TerminalFactory(capability=True, terminal=False)
        store, coordinator = _coordinator(factory, Path(tmp) / "run")
        request_id = str(uuid.uuid4())
        old, worker, result_box = _live_drain(factory, store, coordinator, request_id=request_id)

        recovered = coordinator.recover()
        assert recovered.status == "busy"
        assert old.terminal_calls == 1
        assert old.cancel_request_ids == []
        assert store.canonical.load()[0]["phase"] == "draining"

        old.boundary_release.set()
        worker.join(3.0)
        assert not worker.is_alive()
        assert result_box[0].status == "succeeded"


def test_live_drain_terminal_probe_error_stays_busy():
    with tempfile.TemporaryDirectory() as tmp:
        factory = TerminalFactory(capability=True, terminal_error=RuntimeError("probe boom"))
        store, coordinator = _coordinator(factory, Path(tmp) / "run")
        request_id = str(uuid.uuid4())
        old, worker, _ = _live_drain(factory, store, coordinator, request_id=request_id)

        recovered = coordinator.recover()
        assert recovered.status == "busy"
        assert old.terminal_calls == 1
        assert old.cancel_request_ids == []
        assert store.canonical.load()[0]["phase"] == "draining"

        old.boundary_release.set()
        worker.join(3.0)
        assert not worker.is_alive()


def test_live_drain_terminal_probe_timeout_stays_busy():
    with tempfile.TemporaryDirectory() as tmp:
        factory = TerminalFactory(capability=True, terminal_hangs=True)
        store, coordinator = _coordinator(factory, Path(tmp) / "run")
        request_id = str(uuid.uuid4())
        old, worker, _ = _live_drain(factory, store, coordinator, request_id=request_id)

        recovered = coordinator.recover()
        assert recovered.status == "busy"
        assert old.cancel_request_ids == []
        assert store.canonical.load()[0]["phase"] == "draining"

        old.boundary_release.set()
        worker.join(3.0)
        assert not worker.is_alive()


def test_live_drain_with_terminal_round_is_cancelled_early():
    with tempfile.TemporaryDirectory() as tmp:
        # The acknowledging driver is released only after recovery, so the
        # cancellation's identity check is not raced by the blocked driver.
        factory = TerminalFactory(capability=True, terminal=True, cancel_releases=False)
        store, coordinator = _coordinator(factory, Path(tmp) / "run")
        request_id = str(uuid.uuid4())
        old, worker, result_box = _live_drain(factory, store, coordinator, request_id=request_id)

        recovered = coordinator.recover()
        assert recovered.status == "failed"
        assert recovered.error_code == game_switch.ERROR_TIMEOUT
        state, _ = store.canonical.load()
        assert state["phase"] == "ready"
        assert state["active"]["game"] == "nethack"
        assert state["request_id"] is None
        assert state["deadline_at"] is None
        assert state["last_result"]["error_code"] == game_switch.ERROR_TIMEOUT
        assert old.cancel_request_ids == [request_id]
        assert old.runtime.alive
        assert "stop_agent" not in old.runtime.events
        assert "cleanup" not in old.runtime.events

        old.boundary_release.set()
        worker.join(3.0)
        assert not worker.is_alive()
        assert result_box[0].status == "failed"


def test_live_drain_terminal_but_cancel_refused_keeps_draining():
    with tempfile.TemporaryDirectory() as tmp:
        # A refusing adapter does not release the boundary driver either: the
        # request stays latched exactly as a real un-acknowledged cancel does.
        factory = TerminalFactory(
            capability=True, terminal=True, cancel_ok=False, cancel_releases=False
        )
        store, coordinator = _coordinator(factory, Path(tmp) / "run")
        request_id = str(uuid.uuid4())
        old, worker, _ = _live_drain(factory, store, coordinator, request_id=request_id)

        recovered = coordinator.recover()
        assert recovered.status == "failed"
        assert recovered.error_code == game_switch.ERROR_RECOVERY_REQUIRED
        assert store.canonical.load()[0]["phase"] == "draining"
        assert old.cancel_request_ids == [request_id]
        assert old.runtime.alive

        old.boundary_release.set()
        worker.join(3.0)
        assert not worker.is_alive()


def test_expired_drain_recovery_still_uses_the_deadline_path():
    with tempfile.TemporaryDirectory() as tmp:
        factory = TerminalFactory(capability=True, terminal=False, cancel_releases=False)
        store, coordinator = _coordinator(factory, Path(tmp) / "run")
        request_id = str(uuid.uuid4())
        old, worker, result_box = _live_drain(factory, store, coordinator, request_id=request_id)

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
        # The terminal capability is an extra early path only: the expired
        # deadline must not start consulting it.
        assert old.terminal_calls == 0
        assert old.cancel_request_ids == [request_id]

        old.boundary_release.set()
        worker.join(3.0)
        assert not worker.is_alive()
        assert result_box[0].status == "failed"


@pytest.mark.parametrize("reported", ["true", "false", 1, 0, {"terminal": True}, [], None])
def test_terminal_report_must_be_a_strict_bool(reported):
    """Truthiness must never cancel a live drain, on either recovery path."""

    with tempfile.TemporaryDirectory() as tmp:
        factory = TerminalFactory(capability=True, terminal=reported)
        store, coordinator = _coordinator(factory, Path(tmp) / "run")
        request_id = str(uuid.uuid4())
        old, worker, _ = _live_drain(factory, store, coordinator, request_id=request_id)

        recovered = coordinator.recover()
        assert recovered.status == "busy"
        assert recovered.error_code == game_switch.ERROR_BUSY
        maintained = coordinator.maintain_fifo(timeout_s=0.5)
        assert maintained.status == "busy"
        assert maintained.error_code == game_switch.ERROR_BUSY
        assert old.cancel_request_ids == []
        assert store.canonical.load()[0]["phase"] == "draining"

        old.boundary_release.set()
        worker.join(3.0)
        assert not worker.is_alive()


def test_maintain_fifo_cancels_a_proven_terminal_drain_early():
    """The timer-driven path must reach the same early cancel as recover()."""

    with tempfile.TemporaryDirectory() as tmp:
        factory = TerminalFactory(capability=True, terminal=True, cancel_releases=False)
        store, coordinator = _coordinator(factory, Path(tmp) / "run")
        request_id = str(uuid.uuid4())
        old, worker, _ = _live_drain(factory, store, coordinator, request_id=request_id)

        maintained = coordinator.maintain_fifo(timeout_s=0.5)
        assert maintained.status == "failed"
        assert maintained.error_code == game_switch.ERROR_TIMEOUT
        state, _ = store.canonical.load()
        assert state["phase"] == "ready"
        assert state["active"]["game"] == "nethack"
        assert state["deadline_at"] is None
        assert old.cancel_request_ids == [request_id]
        assert old.runtime.alive
        assert "stop_agent" not in old.runtime.events
        assert "cleanup" not in old.runtime.events

        old.boundary_release.set()
        worker.join(3.0)
        assert not worker.is_alive()


def test_maintain_fifo_keeps_busy_without_a_terminal_proof():
    with tempfile.TemporaryDirectory() as tmp:
        factory = TerminalFactory(capability=False)
        store, coordinator = _coordinator(factory, Path(tmp) / "run")
        request_id = str(uuid.uuid4())
        old, worker, _ = _live_drain(factory, store, coordinator, request_id=request_id)

        maintained = coordinator.maintain_fifo(timeout_s=0.5)
        assert maintained.status == "busy"
        assert maintained.error_code == game_switch.ERROR_BUSY
        assert "deadline前" in (maintained.detail or "")
        assert old.cancel_request_ids == []
        assert store.canonical.load()[0]["phase"] == "draining"

        old.boundary_release.set()
        worker.join(3.0)
        assert not worker.is_alive()

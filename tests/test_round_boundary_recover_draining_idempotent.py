"""#2145 obs3: expired-drain recovery must be idempotent once ready.

`_recover_draining_locked` releases the writer lock while the adapter cancel
runs.  A concurrent recovery (the stale driver's own failure path or another
request) can complete the ``draining -> ready`` transition in that window.
The recovery must then finish its receipt without raising
``InvalidTransitionError: phase ready から ready へ遷移できません`` (#2385).

These tests use the real store and the real coordinator; only the adapter
cancel ack is patched, so the guard in ``_recover_draining_locked`` is the
code under test.
"""

from __future__ import annotations

import sys
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import game_switch  # noqa: E402
from test_round_boundary import BoundaryFactory, _assert_started, _coordinator  # noqa: E402


def _expired_draining(tmp):
    """Accept a request, then force canonical into an expired drain.

    Returns ``(store, coordinator, request_id, draining_state, factory)`` with
    a real receipt for ``request_id`` already on disk.
    """

    state_dir = Path(tmp) / "run"
    factory = BoundaryFactory()
    store, coordinator = _coordinator(factory, state_dir, round_boundary_s=5.0)
    _assert_started(coordinator)
    request_id = str(uuid.uuid4())
    with store.transaction(blocking=False) as tx:
        tx.accept_request(request_id, "switch", "robots")
    with store.lock(exclusive=True, blocking=True):
        state, _migrated = store.canonical.load()
        assert state["active"]["game"] == "nethack"
        state.update(
            {
                "phase": "draining",
                "request_id": request_id,
                "operation": "switch",
                "deadline_at": "2000-01-01T00:00:00Z",
            }
        )
        store.canonical.save(state)
    draining_state, _migrated = store.canonical.load()
    return store, coordinator, request_id, draining_state, factory


def _processes_gone(adapter, verdict):
    """Give a fixture adapter the docich#4693 death-proof capability."""

    adapter.runtime_processes_gone = lambda deadline, cancel: verdict


def test_recover_draining_skips_ready_transition_when_completed_elsewhere():
    """Another recovery already finished; this one must not crash on ready->ready."""

    with tempfile.TemporaryDirectory() as tmp:
        store, coordinator, request_id, draining_state, _factory = _expired_draining(tmp)
        # Model the racing recovery that won the window: canonical is ready
        # again while this caller still holds the stale ``draining`` snapshot.
        with store.lock(exclusive=True, blocking=True):
            state, _migrated = store.canonical.load()
            state.update(
                {
                    "phase": "ready",
                    "request_id": None,
                    "candidate": None,
                    "previous": None,
                    "operation": None,
                    "deadline_at": None,
                }
            )
            store.canonical.save(state)

        with patch.object(
            coordinator, "_cancel_round_boundary_locked", return_value=(True, None)
        ):
            with store.transaction(blocking=False) as tx:
                result = coordinator._recover_draining_locked(
                    tx, draining_state, time.monotonic() + 2.0
                )

        assert result.status == "failed"
        assert result.error_code == game_switch.ERROR_TIMEOUT
        current, _migrated = store.canonical.load()
        assert current["phase"] == "ready"
        assert current["request_id"] is None
        assert store.receipts.load(request_id)["status"] == "failed"


def test_recover_draining_still_transitions_a_live_drain():
    """The idempotency guard must not skip a drain nobody else recovered."""

    with tempfile.TemporaryDirectory() as tmp:
        store, coordinator, request_id, draining_state, _factory = _expired_draining(tmp)
        with patch.object(
            coordinator, "_cancel_round_boundary_locked", return_value=(True, None)
        ):
            with store.transaction(blocking=False) as tx:
                result = coordinator._recover_draining_locked(
                    tx, draining_state, time.monotonic() + 2.0
                )

        assert result.status == "failed"
        current, _migrated = store.canonical.load()
        assert current["phase"] == "ready"
        assert current["request_id"] is None
        assert store.receipts.load(request_id)["status"] == "failed"


def test_recover_draining_retires_a_proven_dead_runtime():
    """docich#4693: a cancelled drain over a dead runtime is collected.

    The adapter's death proof reports zero surviving runtime-owned
    processes, so the recovery must not republish the corpse as ``active``:
    it moves the runtime through ``retiring``, tears it down and finishes the
    receipt with ``retained_active=None`` + ``active_retired=True`` -- the
    same failed-recovery receipt shape ``_retire_dead_active_locked`` emits
    on the boundary-failure path.  Rotation can then resume from ``idle``.
    """

    with tempfile.TemporaryDirectory() as tmp:
        store, coordinator, request_id, draining_state, factory = _expired_draining(tmp)
        old = factory.adapters[("nethack", 1)]
        _processes_gone(old, True)
        with patch.object(
            coordinator, "_cancel_round_boundary_locked", return_value=(True, None)
        ):
            with store.transaction(blocking=False) as tx:
                result = coordinator._recover_draining_locked(
                    tx, draining_state, time.monotonic() + 2.0
                )

        assert result.status == "failed"
        assert result.error_code == game_switch.ERROR_TIMEOUT
        assert result.cleanup_pending is False
        current, _migrated = store.canonical.load()
        assert current["phase"] == "idle"
        assert current["active"] is None
        assert current["retiring"] == []
        receipt = store.receipts.load(request_id)
        assert receipt["status"] == "failed"
        assert receipt["result"]["retained_active"] is None
        assert receipt["result"]["active_retired"] is True
        assert receipt["result"]["boundary_cancelled"] is True
        assert receipt["result"]["failure_phase"] == "round_boundary"
        # The dead runtime was actually collected, not just untracked.
        assert "stop_agent" in old.runtime.events
        assert "cleanup" in old.runtime.events
        # Rotation resumes: a fresh start from idle succeeds.
        _assert_started(coordinator, game="robots")


def test_recover_draining_retains_when_processes_survive():
    """A live survivor keeps the conservative retained-active path."""

    with tempfile.TemporaryDirectory() as tmp:
        store, coordinator, request_id, draining_state, factory = _expired_draining(tmp)
        old = factory.adapters[("nethack", 1)]
        _processes_gone(old, False)
        with patch.object(
            coordinator, "_cancel_round_boundary_locked", return_value=(True, None)
        ):
            with store.transaction(blocking=False) as tx:
                result = coordinator._recover_draining_locked(
                    tx, draining_state, time.monotonic() + 2.0
                )

        assert result.status == "failed"
        current, _migrated = store.canonical.load()
        assert current["phase"] == "ready"
        assert current["active"]["game"] == "nethack"
        receipt = store.receipts.load(request_id)
        assert receipt["result"]["retained_active"]["game"] == "nethack"
        assert receipt["result"].get("active_retired") is not True
        assert "cleanup" not in old.runtime.events


def test_recover_draining_dead_path_skips_retire_when_completed_elsewhere():
    """docich#4693: the retire path is idempotent like the ready path.

    A racing recovery that already collected the runtime leaves canonical in
    ``idle`` with a terminal receipt; this caller -- still holding the stale
    ``draining`` snapshot -- must not raise ``InvalidTransitionError`` or
    retire the runtime a second time.
    """

    with tempfile.TemporaryDirectory() as tmp:
        store, coordinator, request_id, draining_state, factory = _expired_draining(tmp)
        old = factory.adapters[("nethack", 1)]
        _processes_gone(old, True)
        with store.lock(exclusive=True, blocking=True):
            state, _migrated = store.canonical.load()
            state.update(
                {
                    "phase": "idle",
                    "active": None,
                    "request_id": None,
                    "candidate": None,
                    "previous": None,
                    "operation": None,
                    "deadline_at": None,
                }
            )
            store.canonical.save(state)

        with patch.object(
            coordinator, "_cancel_round_boundary_locked", return_value=(True, None)
        ):
            with store.transaction(blocking=False) as tx:
                result = coordinator._recover_draining_locked(
                    tx, draining_state, time.monotonic() + 2.0
                )

        assert result.status == "failed"
        current, _migrated = store.canonical.load()
        assert current["phase"] == "idle"
        assert current["active"] is None
        assert store.receipts.load(request_id)["status"] == "failed"
        assert "cleanup" not in old.runtime.events

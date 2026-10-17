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

    Returns ``(store, coordinator, request_id, draining_state)`` with a real
    receipt for ``request_id`` already on disk.
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
    return store, coordinator, request_id, draining_state


def test_recover_draining_skips_ready_transition_when_completed_elsewhere():
    """Another recovery already finished; this one must not crash on ready->ready."""

    with tempfile.TemporaryDirectory() as tmp:
        store, coordinator, request_id, draining_state = _expired_draining(tmp)
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
        store, coordinator, request_id, draining_state = _expired_draining(tmp)
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

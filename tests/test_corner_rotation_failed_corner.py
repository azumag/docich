"""One failed corner must not stop the other corners (#2187).

The scheduler releases a failed automatic reservation as soon as the *shared*
program slot provably holds nothing from that request, records a fixed anomaly
row and keeps rotating. Only an unprovable shared slot keeps the fail-closed
latch, and even then the timer retries the release every tick.
"""
from contextlib import nullcontext
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from docich.config import load_global
from docich.corner_catalog import Corner
from docich.corner_rotation import (
    CornerRotationManager,
    FAILED_CORNER_LIMIT,
    RELEASED_REASON,
    RotationError,
)

CLEAN_CANONICAL = {
    "phase": "ready",
    "operation": None,
    "request_id": None,
    "deadline_at": None,
    "candidate": None,
    "previous": None,
    "retiring": [],
    "active": {"game": "sorengame", "runtime_id": "g1-abcdef", "generation": 1,
               "lease_id": "lease"},
}
TERMINAL_RECEIPT = {"status": "rolled_back", "result": {"status": "rolled_back"}}


def owned_canonical(game):
    """A plane that still owns the corner's own game is never reusable."""
    return {**CLEAN_CANONICAL, "active": {**CLEAN_CANONICAL["active"], "game": game}}


class _Canonical:
    def __init__(self, data, missing=False):
        self.data, self.missing = data, missing

    def load(self):
        return (self.data, self.missing)


class _Receipts:
    def __init__(self, rows=None, queued=None, default=None):
        self.rows = dict(rows or {})
        self.queue = list(queued or [])
        self.default = default

    def load(self, request_id):
        return self.rows.get(request_id, self.default)

    def queued(self):
        return list(self.queue)


class Store:
    def __init__(self, canonical=None, *, missing=False, queued=None, default_receipt=None,
                 rows=None):
        self.canonical = _Canonical(CLEAN_CANONICAL if canonical is None else canonical, missing)
        self.receipts = _Receipts(rows=rows, queued=queued, default=default_receipt)

    def lock(self, exclusive=False):
        return nullcontext()


class Adapter:
    def __init__(self, g, corner, *, store=None, states=None, released=True, available=True,
                 state_path=None):
        self.corner = corner
        self.manager = SimpleNamespace(store=store) if store is not None else None
        self.states = list(states or [])
        self.released = released
        self.available = available
        self.state_path = state_path

    def eligible(self):
        return self.available

    def observations(self):
        return list(self.states)

    def resources_released(self):
        return self.released


class RestoreAdapter(Adapter):
    """Adapter that exposes the reviewed timer restore replay (#2187)."""

    def __init__(self, *args, restore_recovery=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._restore_recovery = restore_recovery

    def recover_failed_restore(self):
        if self._restore_recovery is None:
            return None
        return self._restore_recovery(self)


class Executor:
    def __init__(self, result="completed"):
        self.calls = []
        self.result = result

    def execute(self, adapter, request):
        self.calls.append(dict(request))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def build(tmp_path, *, storages, states=None, released=True, executor=None,
          restore_recovery=None, soren_root=None):
    """Build a two-corner queue rotation with per-corner fake stores."""

    path = tmp_path / "config.toml"
    path.write_text(
        '[corner_rotation]\nenabled=true\nschedule_mode="queue"\n[paths]\nstate_dir="run"\n'
        + (f'[webui]\nsoren_root="{soren_root}"\n' if soren_root else '')
    )
    g = load_global(tmp_path, path)
    clock = [1000000.0]
    catalog = [Corner("a", "game", "gamea"), Corner("b", "game", "gameb")]
    restore_recovery = restore_recovery or {}
    adapters = {}
    for corner in catalog:
        common = dict(store=storages.get(corner.id),
                      states=(states or {}).get(corner.id, []),
                      released=released)
        if corner.id in restore_recovery:
            adapters[corner.id] = RestoreAdapter(
                g, corner, restore_recovery=restore_recovery[corner.id], **common)
        else:
            adapters[corner.id] = Adapter(g, corner, **common)
    executor = executor or Executor()
    manager = CornerRotationManager(
        g, clock=lambda: clock[0], seed="seed", catalog=catalog,
        adapter_factory=lambda g, corner: adapters[corner.id], executor=executor,
    )
    return manager, clock, executor, adapters


def ledger(manager):
    return json.loads(manager.path.read_text())


def last_failure(state):
    rows = state["failed_corners"]
    assert rows
    return rows[-1]


def test_failed_corner_is_released_and_the_next_corner_runs(tmp_path):
    manager, _clock, executor, _adapters = build(
        tmp_path,
        storages={"a": Store(default_receipt=TERMINAL_RECEIPT),
                  "b": Store(default_receipt=TERMINAL_RECEIPT)},
        executor=Executor(result="failed"),
    )

    outcome = manager.tick()

    # The failure settles the reservation without latching the rotation.
    assert outcome == {"status": "waiting", "corner": outcome["corner"],
                       "reason": RELEASED_REASON, "result": "unverified"}
    state = ledger(manager)
    failed = last_failure(state)
    assert failed["resolution"] == "released-unverified"
    assert failed["error_kind"] == "execution-unverified"
    assert failed["corner"] == outcome["corner"]
    assert state["status"] == "waiting"
    assert state["pending"] is None
    # The failed use still counts toward the rolling cooldown.
    assert any(row["corner"] == failed["corner"] and row["source"] == "failure"
               for row in state["history"])

    # The very next tick runs a different corner instead of stopping.
    executor.result = "completed"
    result = manager.tick()

    assert result["corner"] != failed["corner"]
    assert result["status"] == "ready"
    assert ledger(manager)["status"] == "ready"


def test_failed_corner_keeps_the_latch_while_the_slot_is_not_proven(tmp_path):
    manager, _clock, _executor, _adapters = build(
        tmp_path,
        storages={"a": Store(canonical=owned_canonical("gamea")),
                  "b": Store(canonical=owned_canonical("gameb"))},
        executor=Executor(result="failed"),
    )

    with pytest.raises(RotationError):
        manager.tick()

    state = ledger(manager)
    assert state["status"] == "recovery_required"
    assert state["pending"] is not None
    failed = last_failure(state)
    assert failed["resolution"] == "recovery-required"
    assert failed["corner"] == state["pending"]["corner"]


def test_failed_corner_stays_latched_for_an_unreadable_plane(tmp_path):
    manager, _clock, _executor, _adapters = build(
        tmp_path,
        storages={"a": Store(missing=True, default_receipt=TERMINAL_RECEIPT),
                  "b": Store(missing=True, default_receipt=TERMINAL_RECEIPT)},
        executor=Executor(result="failed"),
    )

    with pytest.raises(RotationError):
        manager.tick()

    assert ledger(manager)["status"] == "recovery_required"


def test_released_failure_does_not_block_the_other_corner(tmp_path):
    manager, _clock, executor, _adapters = build(
        tmp_path,
        storages={"a": Store(default_receipt=TERMINAL_RECEIPT),
                  "b": Store(default_receipt=TERMINAL_RECEIPT)},
        executor=Executor(result="failed"),
    )
    manager.tick()
    released_corner = last_failure(ledger(manager))["corner"]
    request_id = last_failure(ledger(manager))["request_id"]
    assert ledger(manager)["released_requests"][released_corner]["request_id"] == request_id

    # The released corner keeps a stale BUSY record on disk …
    failed_adapter = manager.adapters[released_corner]
    failed_adapter.states = [{"status": "failed", "rotation_request_id": request_id}]
    settled = []
    failed_adapter.settle_released_failure = lambda owner: settled.append(owner) or True
    executor.result = "completed"

    # … and the other corner still runs.
    result = manager.tick()
    assert result["corner"] != released_corner
    assert settled == [request_id]

    # A later owner of that corner's game stops the skip (fail closed again).
    manager.adapters[released_corner].manager.store.canonical = _Canonical(
        owned_canonical(failed_adapter.corner.game)
    )
    waited = manager.tick()
    assert waited["status"] == "waiting"
    assert waited["reason"] == "other-corner-needs-finish-or-recovery"
    assert settled == [request_id]


def test_older_released_owner_is_settled_after_a_later_failed_request(tmp_path):
    manager, _clock, executor, _adapters = build(
        tmp_path,
        storages={"a": Store(default_receipt=TERMINAL_RECEIPT),
                  "b": Store(default_receipt=TERMINAL_RECEIPT)},
        executor=Executor(result="failed"),
    )
    manager.tick()
    state = ledger(manager)
    old = last_failure(state)
    corner = old["corner"]
    old_request = old["request_id"]
    adapter = manager.adapters[corner]
    adapter.states = [{"status": "failed", "rotation_request_id": old_request}]
    settled = []
    adapter.settle_released_failure = lambda owner: settled.append(owner) or True

    # A later attempt can fail before the adapter writes its own new state.
    # The per-corner release pointer now names that new request, while the
    # adapter still owns the older failure recorded in the bounded audit.
    state["released_requests"][corner]["request_id"] = str(uuid.uuid4())
    manager.save(state)
    executor.result = "completed"
    result = manager.tick()

    assert result["corner"] != corner
    assert settled == [old_request]


def test_a_deferred_release_settlement_keeps_the_family_out_of_selection(tmp_path):
    """#2354 review: keep the shared state file out of selection while the
    released owner cannot be retired.

    Dispatching another corner on the same state file only fails again and
    pushes the original request's release evidence out of the bounded audit, so
    the retirement would become impossible once its cause is fixed.
    """

    manager, _clock, executor, _adapters = build(
        tmp_path,
        storages={"a": Store(default_receipt=TERMINAL_RECEIPT),
                  "b": Store(default_receipt=TERMINAL_RECEIPT)},
        executor=Executor(result="failed"),
    )
    manager.tick()
    state = ledger(manager)
    old = last_failure(state)
    corner = old["corner"]
    old_request = old["request_id"]
    other = "b" if corner == "a" else "a"
    adapter = manager.adapters[corner]
    adapter.states = [{"status": "failed", "rotation_request_id": old_request}]
    adapter.state_path = tmp_path / "shared_corner.json"
    manager.adapters[other].state_path = tmp_path / "other_corner.json"
    calls = []
    adapter.settle_released_failure = lambda owner: calls.append(owner) or None
    executor.result = "completed"

    result = manager.tick()

    assert calls == [old_request]
    # The unrelated corner still runs; only the shared game state file waits.
    assert result["corner"] == other
    assert result["status"] == "ready"
    state = ledger(manager)
    assert state["excluded"][corner] == "released-owner-unretired"
    # No new failed request was written, so the original release evidence stays.
    assert len(state["failed_corners"]) == 1


def test_the_family_returns_to_selection_once_the_owner_is_retired(tmp_path):
    manager, _clock, executor, _adapters = build(
        tmp_path,
        storages={"a": Store(default_receipt=TERMINAL_RECEIPT),
                  "b": Store(default_receipt=TERMINAL_RECEIPT)},
        executor=Executor(result="failed"),
    )
    manager.tick()
    state = ledger(manager)
    old = last_failure(state)
    corner = old["corner"]
    old_request = old["request_id"]
    adapter = manager.adapters[corner]
    adapter.states = [{"status": "failed", "rotation_request_id": old_request}]
    adapter.state_path = tmp_path / "shared_corner.json"
    outcomes = [None, True]
    adapter.settle_released_failure = lambda owner: outcomes.pop(0)

    manager.tick()
    assert ledger(manager)["excluded"][corner] == "released-owner-unretired"

    manager.tick()

    assert ledger(manager)["excluded"].get(corner) != "released-owner-unretired"


def test_a_newer_release_record_never_settles_the_older_failed_owner(tmp_path):
    """#2352 review: only the original request's own release record counts.

    The per-corner pointer names the NEW request, and the bounded audit row for
    the OLDER request is gone. Nothing then proves that the owner still in the
    corner state was released, so it must stay busy and keep blocking the
    rotation instead of being retired by the newer request's record.
    """

    manager, _clock, executor, _adapters = build(
        tmp_path,
        storages={"a": Store(default_receipt=TERMINAL_RECEIPT),
                  "b": Store(default_receipt=TERMINAL_RECEIPT)},
        executor=Executor(result="failed"),
    )
    manager.tick()
    state = ledger(manager)
    old = last_failure(state)
    corner = old["corner"]
    old_request = old["request_id"]
    adapter = manager.adapters[corner]
    adapter.states = [{"status": "failed", "rotation_request_id": old_request}]
    settled = []
    adapter.settle_released_failure = lambda owner: settled.append(owner) or True
    state["released_requests"][corner]["request_id"] = str(uuid.uuid4())
    state["failed_corners"] = [
        row for row in state["failed_corners"]
        if not (row["corner"] == corner and row["request_id"] == old_request)
    ]
    manager.save(state)
    executor.result = "completed"
    dispatched = list(executor.calls)

    result = manager.tick()

    assert settled == []
    assert result["status"] == "waiting"
    assert result["reason"] == "other-corner-needs-finish-or-recovery"
    # The unproven owner keeps blocking: no new corner is dispatched.
    assert executor.calls == dispatched
    assert ledger(manager)["pending"] is None


def test_released_failure_skip_survives_audit_list_rollover(tmp_path):
    manager, _clock, executor, _adapters = build(
        tmp_path,
        storages={"a": Store(default_receipt=TERMINAL_RECEIPT),
                  "b": Store(default_receipt=TERMINAL_RECEIPT)},
        executor=Executor(result="failed"),
    )
    manager.tick()
    state = ledger(manager)
    released_corner = last_failure(state)["corner"]
    request_id = state["released_requests"][released_corner]["request_id"]

    # The bounded audit list rolls over, but the per-corner row keeps the
    # released observation from stalling the rotation.
    rotated = state
    rotated["failed_corners"] = [
        {"corner": "x%d" % n, "request_id": str(n), "at": n,
         "resolution": "released-unverified", "error_kind": "execution-unverified"}
        for n in range(FAILED_CORNER_LIMIT + 4)
    ]
    manager.save(rotated)
    manager.adapters[released_corner].states = [
        {"status": "failed", "rotation_request_id": request_id}
    ]
    executor.result = "completed"

    result = manager.tick()

    assert result["corner"] != released_corner


def test_latch_auto_releases_when_the_slot_becomes_provable(tmp_path):
    manager, _clock, executor, adapters = build(
        tmp_path,
        storages={"a": Store(canonical=owned_canonical("gamea")),
                  "b": Store(canonical=owned_canonical("gameb"))},
        executor=Executor(result="failed"),
    )
    with pytest.raises(RotationError):
        manager.tick()
    latched = ledger(manager)
    assert latched["status"] == "recovery_required"
    blocked_corner = latched["pending"]["corner"]

    # The plane recovers (the rollback finishes) and the timer retries: no
    # operator is needed to clear the latch.
    adapters[blocked_corner].manager.store.canonical = _Canonical(CLEAN_CANONICAL)
    executor.result = "completed"

    result = manager.tick()

    assert result["corner"] != blocked_corner
    after = ledger(manager)
    assert after["status"] == "ready"
    assert last_failure(after)["resolution"] == "released-unverified"


def test_latched_failure_stays_recorded_without_duplicating(tmp_path):
    manager, _clock, _executor, _adapters = build(
        tmp_path,
        storages={"a": Store(canonical=owned_canonical("gamea")),
                  "b": Store(canonical=owned_canonical("gameb"))},
        executor=Executor(result="failed"),
    )
    with pytest.raises(RotationError):
        manager.tick()
    assert len(ledger(manager)["failed_corners"]) == 1

    # Every later tick re-evaluates the latch and must not append a new row
    # per minute, but the failure must remain visible.
    for _ in range(3):
        outcome = manager.tick()
        assert outcome["status"] == "recovery_required"

    rows = ledger(manager)["failed_corners"]
    assert len(rows) == 1
    assert rows[0]["resolution"] == "recovery-required"


def test_latched_end_failure_is_replayed_by_the_timer(tmp_path):
    """A corner end that left its own game on screen is restored by the timer."""

    def replay(adapter):
        adapter.manager.store.canonical = _Canonical(CLEAN_CANONICAL)
        adapter.states = []
        return SimpleNamespace(status="succeeded")

    manager, _clock, executor, adapters = build(
        tmp_path,
        storages={"a": Store(canonical=owned_canonical("gamea")),
                  "b": Store(canonical=owned_canonical("gameb"))},
        executor=Executor(result="failed"),
        restore_recovery={"a": replay, "b": replay},
    )
    with pytest.raises(RotationError):
        manager.tick()
    pending = ledger(manager)["pending"]
    blocked = pending["corner"]
    # The corner ran and ended; its own game still holds the plane.
    adapters[blocked].states = [{"status": "failed", "started_at": 1.0,
                                 "rotation_request_id": pending["request_id"]}]
    executor.result = "completed"

    result = manager.tick()

    assert result["corner"] != blocked
    after = ledger(manager)
    assert after["auto_restore"]["attempts"] == 1
    assert after["auto_restore"]["last_status"] == "succeeded"
    assert after["status"] == "ready"
    assert last_failure(after)["resolution"] == "released-unverified"


def test_auto_restore_respects_backoff_and_then_records_exhaustion(tmp_path):
    calls = []

    def replay(_adapter):
        calls.append(1)
        return SimpleNamespace(status="failed")

    manager, clock, _executor, adapters = build(
        tmp_path,
        # The request's receipt is not terminal yet, so the finished leftover
        # cannot be reclaimed: the replay retries with backoff and then gives up.
        storages={"a": Store(canonical=owned_canonical("gamea"),
                             default_receipt={"status": "queued"}),
                  "b": Store(canonical=owned_canonical("gameb"),
                             default_receipt={"status": "queued"})},
        executor=Executor(result="failed"),
        restore_recovery={"a": replay, "b": replay},
    )
    with pytest.raises(RotationError):
        manager.tick()
    pending = ledger(manager)["pending"]
    adapters[pending["corner"]].states = [
        {"status": "failed", "started_at": 1.0, "rotation_request_id": pending["request_id"]}
    ]

    # First attempt runs immediately.
    assert manager.tick()["status"] == "recovery_required"
    state = ledger(manager)
    assert state["auto_restore"]["attempts"] == 1
    assert state["auto_restore"]["next_attempt_at"] == clock[0] + 300

    # A tick inside the backoff window must not replay again.
    assert manager.tick()["status"] == "recovery_required"
    assert ledger(manager)["auto_restore"]["attempts"] == 1

    clock[0] += 301
    assert manager.tick()["status"] == "recovery_required"
    assert ledger(manager)["auto_restore"]["attempts"] == 2

    clock[0] += 901
    assert manager.tick()["status"] == "recovery_required"
    assert ledger(manager)["auto_restore"]["attempts"] == 3

    # The cap is reached: automation gives up exactly once and the latch stays.
    clock[0] += 2701
    assert manager.tick()["status"] == "recovery_required"
    after = ledger(manager)
    assert after["auto_restore"]["attempts"] == 3
    exhausted = [row for row in after["failed_corners"]
                 if row.get("reason_code") == "restore-recovery-exhausted"]
    assert len(exhausted) == 1
    assert exhausted[0]["resolution"] == "recovery-required"

    clock[0] += 2701
    assert manager.tick()["status"] == "recovery_required"
    assert len([row for row in ledger(manager)["failed_corners"]
                if row.get("reason_code") == "restore-recovery-exhausted"]) == 1
    assert len(calls) == 3


def test_auto_restore_is_skipped_without_the_adapter_entry(tmp_path):
    manager, _clock, _executor, _adapters = build(
        tmp_path,
        storages={"a": Store(canonical=owned_canonical("gamea")),
                  "b": Store(canonical=owned_canonical("gameb"))},
        executor=Executor(result="failed"),
    )
    with pytest.raises(RotationError):
        manager.tick()

    assert manager.tick()["status"] == "recovery_required"

    assert "auto_restore" not in ledger(manager)


def test_finished_corner_leftover_is_reclaimed_and_the_next_corner_runs(tmp_path):
    """A finished corner's leftover game must not stop the rotation (#2187)."""

    manager, _clock, executor, adapters = build(
        tmp_path,
        storages={"a": Store(canonical=owned_canonical("gamea")),
                  "b": Store(canonical=owned_canonical("gameb"))},
        executor=Executor(result="failed"),
    )
    with pytest.raises(RotationError):
        manager.tick()
    pending = ledger(manager)["pending"]
    blocked = pending["corner"]
    # The corner ran and ended, but its own game still holds the plane.
    adapters[blocked].states = [{"status": "failed", "started_at": 1.0,
                                 "rotation_request_id": pending["request_id"]}]
    executor.result = "completed"

    result = manager.tick()

    assert result["corner"] != blocked
    after = ledger(manager)
    assert after["status"] == "ready"
    released = last_failure(after)
    assert released["corner"] == blocked
    assert released["resolution"] == "released-unverified"
    assert released["reason_code"] == "base-game-unrestored"


def test_unstarted_owner_still_keeps_the_latch(tmp_path):
    """The same plane shape with a corner that never started must stay latched."""

    manager, _clock, executor, adapters = build(
        tmp_path,
        storages={"a": Store(canonical=owned_canonical("gamea")),
                  "b": Store(canonical=owned_canonical("gameb"))},
        executor=Executor(result="failed"),
    )
    with pytest.raises(RotationError):
        manager.tick()
    pending = ledger(manager)["pending"]
    blocked = pending["corner"]
    adapters[blocked].states = [{"status": "failed",
                                 "rotation_request_id": pending["request_id"]}]
    executor.result = "completed"

    assert manager.tick()["status"] == "recovery_required"
    assert ledger(manager)["status"] == "recovery_required"
    assert last_failure(ledger(manager))["resolution"] == "recovery-required"


def test_ledger_error_kinds_still_latch(tmp_path):
    manager, _clock, _executor, adapters = build(
        tmp_path,
        storages={"a": Store(default_receipt=TERMINAL_RECEIPT),
                  "b": Store(default_receipt=TERMINAL_RECEIPT)},
        states={"a": [{"status": "not-a-status"}]},
    )

    with pytest.raises(RotationError):
        manager.tick()

    state = ledger(manager)
    assert state["status"] == "recovery_required"
    assert state["error_kind"] == "adapter-state"


def test_released_failure_rows_stay_bounded(tmp_path):
    manager, _clock, _executor, _adapters = build(
        tmp_path,
        storages={"a": Store(default_receipt=TERMINAL_RECEIPT),
                  "b": Store(default_receipt=TERMINAL_RECEIPT)},
    )
    state = {"failed_corners": [{"corner": "a", "request_id": str(n), "at": n,
                                 "resolution": "released-unverified",
                                 "reason_code": RELEASED_REASON,
                                 "error_kind": "execution-unverified"}
                                for n in range(FAILED_CORNER_LIMIT + 4)]}

    manager._record_failed_corner(state, {"corner": "b", "request_id": "x"},
                                  resolution="released-unverified",
                                  error_kind="execution-unverified", now=1.0)

    assert len(state["failed_corners"]) == FAILED_CORNER_LIMIT
    assert state["failed_corners"][-1]["corner"] == "b"


def test_released_failure_drops_the_stale_program_registry_row(tmp_path):
    # #2282: the shared program registry kept naming the released corner's own
    # state file, so ``_owner_free`` observed its untouched ``failed`` record as
    # BUSY forever and livelocked every later corner in the shared slot. The
    # release path must retire that row, because #2187 never rewrites the failed
    # corner's own state.
    registry_dir = tmp_path / "soren" / "tmp" / "state"
    registry_dir.mkdir(parents=True)
    registry = registry_dir / "docich_program_active.json"
    # The pending corner for this fixture is deterministic (fixed seed, catalog
    # and clock); the adapter's state file is the one the release must release.
    state_file = tmp_path / "run" / "a_corner.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(json.dumps({"owner_state": str(state_file)}))

    manager, _clock, _executor, adapters = build(
        tmp_path,
        storages={"a": Store(default_receipt=TERMINAL_RECEIPT),
                  "b": Store(default_receipt=TERMINAL_RECEIPT)},
        executor=Executor(result="failed"),
        soren_root=tmp_path / "soren",
    )
    adapters["a"].state_path = state_file
    adapters["b"].state_path = tmp_path / "run" / "b_corner.json"

    outcome = manager.tick()

    assert outcome["reason"] == RELEASED_REASON
    assert outcome["corner"] == "a"
    assert not registry.exists()


def test_released_failure_keeps_another_corners_registry_row(tmp_path):
    # The release is scoped to the released corner's own state file: a row
    # naming a different corner stays, so a corner that legitimately holds the
    # program slot is never released out from under itself (#2282).
    registry_dir = tmp_path / "soren" / "tmp" / "state"
    registry_dir.mkdir(parents=True)
    registry = registry_dir / "docich_program_active.json"
    state_file = tmp_path / "run" / "a_corner.json"
    other_file = tmp_path / "run" / "b_corner.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(json.dumps({"owner_state": str(other_file)}))

    manager, _clock, _executor, adapters = build(
        tmp_path,
        storages={"a": Store(default_receipt=TERMINAL_RECEIPT),
                  "b": Store(default_receipt=TERMINAL_RECEIPT)},
        executor=Executor(result="failed"),
        soren_root=tmp_path / "soren",
    )
    adapters["a"].state_path = state_file
    adapters["b"].state_path = other_file

    outcome = manager.tick()

    assert outcome["reason"] == RELEASED_REASON
    assert json.loads(registry.read_text())["owner_state"] == str(other_file)


def test_next_tick_clears_registry_from_an_already_released_failure(tmp_path):
    """A row left by an older release must not block a later program slot."""
    registry_dir = tmp_path / "soren" / "tmp" / "state"
    registry_dir.mkdir(parents=True)
    registry = registry_dir / "docich_program_active.json"
    manager, _clock, executor, adapters = build(
        tmp_path,
        storages={"a": Store(default_receipt=TERMINAL_RECEIPT),
                  "b": Store(default_receipt=TERMINAL_RECEIPT)},
        executor=Executor(result="failed"),
        soren_root=tmp_path / "soren",
    )
    for corner_id, adapter in adapters.items():
        adapter.state_path = tmp_path / "run" / f"{corner_id}_corner.json"

    first = manager.tick()
    assert first["reason"] == RELEASED_REASON
    released_corner = first["corner"]
    request_id = ledger(manager)["released_requests"][released_corner]["request_id"]
    released_state = adapters[released_corner].state_path
    adapters[released_corner].states = [{
        "status": "failed", "rotation_request_id": request_id,
    }]
    settled = []
    adapters[released_corner].settle_released_failure = lambda owner: settled.append(owner) or True
    registry.write_text(json.dumps({"owner_state": str(released_state)}))

    executor.result = "completed"
    second = manager.tick()

    assert second["corner"] != released_corner
    assert second["status"] == "ready"
    assert not registry.exists()
    assert settled == [request_id]

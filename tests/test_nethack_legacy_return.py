"""Synthetic filesystem/lock regressions; no game or VM is launched."""
from copy import deepcopy
import datetime as dt
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from docich.corner_rotation import CornerRotationManager, RotationError
from docich.game_switch import GameSwitchBusyError, GameSwitchStore, atomic_write_json, validate_receipt
from docich.corner_adapters import NethackCornerAdapter
from docich.naming import runtime_names
from docich.nethack_corner import NethackCornerManager
from docich.nethack_return import RECORD_KEY

SLOT = "11111111-1111-4111-8111-111111111111"
R0 = "22222222-2222-4222-8222-222222222222"
R = "33333333-3333-4333-8333-333333333333"
LEASE = "44444444-4444-4444-8444-444444444444"
OTHER = "55555555-5555-4555-8555-555555555555"


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    now = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=10)

    def at(seconds):
        return (now + dt.timedelta(seconds=seconds)).isoformat()

    def write(parts, value):
        path = tmp_path.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, value)
        return path

    def receipt(rid, generation, status, result, created, updated):
        runtime_id = f"g{generation}-{'b' if generation == 2 else 'd'}ddddd"
        names = runtime_names(generation)
        value = dict(schema_version=1, request_id=rid, operation="switch", target="sorengame",
                     payload_hash="a" * 64, generation=generation, runtime_id=runtime_id,
                     runtime_dir=str(tmp_path / "runtimes" / runtime_id),
                     game_window=names.game_window, agent_window=names.agent_window,
                     adapter_session=names.adapter_session, status=status, result=result,
                     created_at=at(created), updated_at=at(updated))
        validate_receipt(value, tmp_path)
        write(("game-switch", "requests", f"{rid}.json"), value)
        return value

    old_result = dict(request_id=R0, operation="switch", status="rolled_back", from_game="nethack",
                      to_game="sorengame", generation=2, restored_generation=3, error_code="timeout")
    original = receipt(R0, 2, "rolled_back", old_result, -80, -60)
    active = dict(game="sorengame", adapter="soren", runtime_id="g4-dddddd", generation=4,
                  lease_id=LEASE, adapter_session="docich-game-g4", game_window="game-g4",
                  agent_window="agent-g4", started_at=at(-30))
    result = dict(request_id=R, operation="switch", status="succeeded", from_game="nethack",
                  to_game="sorengame", generation=4,
                  active_runtime={key: active[key] for key in ("game", "runtime_id", "generation", "lease_id")})
    landed = receipt(R, 4, "succeeded", result, -40, -20)
    store = GameSwitchStore(tmp_path)
    state = store.initialize()
    state.update(phase="ready", active=active, next_generation=5, last_result=result)
    canonical = store.canonical.save(state)
    owner = dict(schema_version=1, status="failed", game="nethack", finish_reason="terminal",
                 rotation_request_id=SLOT, rotation_runtime_id="g1-aaaaaa", switch_request_id=R0,
                 started_at=at(-100), completed_at=at(-90), last_error_code="timeout",
                 run_id=OTHER, run_score=0)
    write(("nethack_corner.json",), owner)
    first = dict(schema_version=1, game="nethack", generation=1, runtime_id="g1-aaaaaa",
                 request_id=R0, player_name="fixture_player", outcome="ended", recorded_at=at(-70))
    second = dict(schema_version=1, game="nethack", generation=3, runtime_id="g3-cccccc",
                  request_id=R, player_name="fixture_player", outcome="ended", recorded_at=at(-30))
    write(("runtimes", "g1-aaaaaa", "nethack_boundary.json"), first)
    write(("runtimes", "g3-cccccc", "nethack_boundary.json"), second)
    ledger = dict(schema_version=1, seed="synthetic", slot=1, last_seen_at=now.timestamp() - 100,
                  next_due_at=now.timestamp() - 90, last_slot_at=now.timestamp() - 100,
                  history=[dict(corner="nethack", at=now.timestamp() - 100, source="reservation")],
                  pending=dict(corner="nethack", phase="dispatched", request_id=SLOT,
                               selected_at=now.timestamp() - 100), status="recovery_required")
    write(("corner_rotation.json",), ledger)
    rotation = object.__new__(CornerRotationManager)
    rotation.path = tmp_path / "corner_rotation.json"
    rotation.lock_path = tmp_path / "corner-rotation.lock"
    rotation.clock = lambda: now.timestamp()
    rotation.adapters = {}
    manager = object.__new__(NethackCornerManager)
    manager.g = SimpleNamespace(state_dir=tmp_path)
    manager.state_path = tmp_path / "nethack_corner.json"
    manager.lock_path = tmp_path / "locks/nethack-corner.lock"
    manager.tick_guard_path = tmp_path / "locks/nethack-corner-tick.lock"
    manager.store = store
    manager._local_now = lambda: now
    manager._run_store = SimpleNamespace(settings=SimpleNamespace(player_name="fixture_player"))
    manager.coordinator = Mock()
    manager._runtime_screen = Mock()
    manager._recover_restore_failed = Mock()
    monkeypatch.setattr("docich.corner_rotation.CornerRotationManager", lambda g: rotation)
    return SimpleNamespace(manager=manager, rotation=rotation, root=tmp_path, owner=owner,
                           first=first, second=second, original=original, landed=landed,
                           canonical=canonical, ledger=ledger, write=write, now=now)


def untouched_resources(f):
    assert f.manager.coordinator.mock_calls == []
    f.manager._runtime_screen.assert_not_called()
    f.manager._recover_restore_failed.assert_not_called()


def assert_refused(f):
    before = f.manager.state_path.read_bytes()
    canonical = (f.root / "game_switch.json").read_bytes()
    ledger = f.rotation.path.read_bytes()
    outcome = f.manager.recover_failed_rotation()
    assert outcome.status == "failed"
    assert f.manager.state_path.read_bytes() == before
    assert (f.root / "game_switch.json").read_bytes() == canonical
    assert f.rotation.path.read_bytes() == ledger
    untouched_resources(f)


def test_complete_chain_terminalizes_metadata_and_reuses_the_same_proof(legacy):
    f = legacy
    before_receipts = {p.name: p.read_bytes() for p in (f.root / "game-switch/requests").iterdir()}
    outcome = f.manager.recover_failed_rotation()
    assert outcome.status == "succeeded"
    owner = f.manager._read_state()
    assert owner["status"] == "interrupted" and "previous_game" not in owner
    assert owner[RECORD_KEY]["phase"] == "committed"
    assert owner[RECORD_KEY]["rollback_source"]["runtime_id"] == "g3-cccccc"
    assert owner[RECORD_KEY]["active_runtime"]["lease_id"] == LEASE
    for key, value in f.owner.items():
        if key != "status":
            assert owner[key] == value
    before = f.manager.state_path.read_bytes()
    assert f.manager.recover_failed_rotation().status == "succeeded"
    assert f.manager.state_path.read_bytes() == before
    assert before_receipts == {p.name: p.read_bytes() for p in (f.root / "game-switch/requests").iterdir()}
    assert f.manager.store.canonical.load()[0] == f.canonical
    assert json.loads(f.rotation.path.read_text()) == f.ledger
    untouched_resources(f)


@pytest.mark.parametrize("kind", ["request", "runtime", "player", "outcome", "schema_bool", "schema_float",
                                  "generation_bool", "naive_time", "time_outside_request"])
@pytest.mark.parametrize("which", ["first", "second"])
def test_each_boundary_must_prove_its_exact_request(legacy, kind, which):
    f = legacy
    boundary = deepcopy(getattr(f, which))
    changes = {"request": {"request_id": OTHER}, "runtime": {"runtime_id": "g9-eeeeee"},
               "player": {"player_name": "foreign"}, "outcome": {"outcome": "suspended"},
               "schema_bool": {"schema_version": True}, "schema_float": {"schema_version": 1.0},
               "generation_bool": {"generation": True}, "naive_time": {"recorded_at": "2026-01-01T00:00:00"},
               "time_outside_request": {"recorded_at": (f.now + dt.timedelta(seconds=1)).isoformat()}}
    boundary.update(changes[kind])
    runtime = "g1-aaaaaa" if which == "first" else "g3-cccccc"
    f.write(("runtimes", runtime, "nethack_boundary.json"), boundary)
    assert_refused(f)


@pytest.mark.parametrize("kind", ["foreign_slot", "manual_slot", "legacy_scope", "replay", "stall",
                                  "modern_source", "target_lease", "target_generation", "retiring",
                                  "driver", "last_result", "pending_cleanup", "duplicate_runtime"])
def test_foreign_or_incomplete_evidence_never_releases_the_latch(legacy, kind):
    f = legacy
    if kind == "foreign_slot":
        f.ledger["pending"]["request_id"] = OTHER
    elif kind == "manual_slot":
        f.ledger["manual_pending"] = dict(corner="nethack", request_id=OTHER,
                                         selected_at=f.now.timestamp(), state_file="nethack_corner_manual.json")
    elif kind in {"legacy_scope", "replay", "stall"}:
        f.owner.update({"legacy_scope": {"previous_game": "ninvaders"},
                        "replay": {"restore_recovery": {}}, "stall": {"finish_reason": "stall"}}[kind])
        f.write(("nethack_corner.json",), f.owner)
    elif kind == "modern_source":
        f.landed["result"]["source_runtime"] = {}
        f.write(("game-switch", "requests", f"{R}.json"), f.landed)
    elif kind == "duplicate_runtime":
        (f.root / "runtimes/g3-eeeeee").mkdir()
    else:
        if kind == "target_lease":
            f.canonical["active"]["lease_id"] = OTHER
        elif kind == "target_generation":
            f.canonical["active"]["generation"] = 5
        elif kind == "retiring":
            f.canonical["retiring"] = [dict(f.canonical["active"], runtime_id="g5-eeeeee", generation=5)]
        elif kind == "driver":
            f.canonical.update(request_id=OTHER, operation="switch")
        elif kind == "last_result":
            f.canonical["last_result"]["request_id"] = OTHER
        elif kind == "pending_cleanup":
            f.landed["result"]["cleanup_pending"] = True
            f.write(("game-switch", "requests", f"{R}.json"), f.landed)
        f.write(("game_switch.json",), f.canonical)
    f.write(("corner_rotation.json",), f.ledger)
    assert_refused(f)


@pytest.mark.parametrize("kind", ["file_link", "directory_link", "oversized", "fifo", "duplicate_keys", "missing"])
def test_boundary_io_is_bounded_and_never_follows_links(legacy, kind):
    f = legacy
    path = f.root / "runtimes/g3-cccccc/nethack_boundary.json"
    path.unlink()
    if kind == "file_link":
        other = f.root / "external.json"
        other.write_text(json.dumps(f.second))
        path.symlink_to(other)
    elif kind == "directory_link":
        path.parent.rmdir()
        other = f.root / "external"
        other.mkdir()
        (other / path.name).write_text(json.dumps(f.second))
        path.parent.symlink_to(other, target_is_directory=True)
    elif kind == "oversized":
        path.write_text(" " * 65537)
    elif kind == "fifo":
        import os
        os.mkfifo(path)
    elif kind == "duplicate_keys":
        path.write_text('{"schema_version":1,"schema_version":1}')
    assert_refused(f)


def test_active_manual_owner_blocks(legacy):
    legacy.write(("nethack_corner_manual.json",), dict(schema_version=1, status="active", game="nethack"))
    assert_refused(legacy)


@pytest.mark.parametrize("kind", ["receipt_schema_bool", "receipt_schema_float", "result_generation_float",
                                  "restored_generation_bool", "original_cleanup_integer", "wrong_error",
                                  "return_not_succeeded", "return_wrong_game", "return_result_operation",
                                  "clock_regressed", "canonical_future", "selection_after_start",
                                  "terminal_without_record"])
def test_corrupt_receipts_and_clock_or_phase_conflicts_are_rejected(legacy, kind):
    f = legacy
    if kind == "receipt_schema_bool":
        f.landed["schema_version"] = True
    elif kind == "receipt_schema_float":
        f.landed["schema_version"] = 1.0
    elif kind == "result_generation_float":
        f.landed["result"]["generation"] = 4.0
    elif kind == "restored_generation_bool":
        f.original["result"]["restored_generation"] = True
    elif kind == "original_cleanup_integer":
        f.original["result"]["cleanup_pending"] = 0
    elif kind == "wrong_error":
        f.original["result"]["error_code"] = "foreign"
    elif kind == "return_not_succeeded":
        f.landed["status"] = "accepted"
    elif kind == "return_wrong_game":
        f.landed["result"]["from_game"] = "foreign"
    elif kind == "return_result_operation":
        f.landed["result"]["operation"] = "start"
    elif kind == "clock_regressed":
        f.ledger["last_seen_at"] = f.now.timestamp() + 1
    elif kind == "canonical_future":
        f.canonical["updated_at"] = (f.now + dt.timedelta(seconds=1)).isoformat()
    elif kind == "selection_after_start":
        f.ledger["pending"]["selected_at"] = f.now.timestamp()
    elif kind == "terminal_without_record":
        f.owner["status"] = "interrupted"
    f.write(("game-switch", "requests", f"{R0}.json"), f.original)
    f.write(("game-switch", "requests", f"{R}.json"), f.landed)
    f.write(("game_switch.json",), f.canonical)
    f.write(("corner_rotation.json",), f.ledger)
    f.write(("nethack_corner.json",), f.owner)
    assert_refused(f)


def test_later_clean_landing_does_not_rewrite_original_pending_cleanup_receipt(legacy):
    f = legacy
    f.original["result"]["cleanup_pending"] = True
    original_path = f.write(("game-switch", "requests", f"{R0}.json"), f.original)
    before = original_path.read_bytes()
    assert f.manager.recover_failed_rotation().status == "succeeded"
    assert original_path.read_bytes() == before
    untouched_resources(f)


@pytest.mark.parametrize("kind", ["manual_source", "manual_history", "both", "unknown_source",
                                  "null_source", "missing_dispatch", "future_history"])
def test_only_proven_automatic_dispatch_can_use_legacy_reconciliation(legacy, kind):
    f = legacy
    if kind in {"manual_source", "both"}:
        f.ledger["pending"]["source"] = "manual"
    if kind in {"manual_history", "both"}:
        f.ledger["history"][0]["source"] = "manual-reservation"
    if kind == "unknown_source":
        f.ledger["pending"]["source"] = "unverified"
    if kind == "null_source":
        f.ledger["pending"]["source"] = None
    if kind == "missing_dispatch":
        f.ledger["history"][0]["source"] = "execution"
    if kind == "future_history":
        f.ledger["history"].append(dict(corner="foreign", source="execution", at=f.now.timestamp() + 1))
    f.write(("corner_rotation.json",), f.ledger)
    assert_refused(f)


def test_older_manual_history_is_preserved_and_does_not_impersonate_current_dispatch(legacy):
    f = legacy
    f.ledger["history"].insert(0, dict(corner="nethack", source="manual-reservation",
                                    at=f.now.timestamp() - 200))
    f.write(("corner_rotation.json",), f.ledger)
    assert f.manager.recover_failed_rotation().status == "succeeded"
    assert json.loads(f.rotation.path.read_text()) == f.ledger
    untouched_resources(f)


@pytest.mark.parametrize("kind", ["nonsense", "future", "naive", "missing", "before_request",
                                  "after_receipt", "before_source_exit", "canonical_before_start"])
def test_active_started_at_must_fit_the_successful_request_and_canonical_clock(legacy, kind):
    f = legacy
    changes = {"nonsense": "nonsense", "future": (f.now + dt.timedelta(days=100)).isoformat(),
               "naive": "2026-01-01T00:00:00", "missing": None,
               "before_request": (f.now - dt.timedelta(seconds=41)).isoformat(),
               "after_receipt": (f.now - dt.timedelta(seconds=15)).isoformat(),
               "before_source_exit": (f.now - dt.timedelta(seconds=39)).isoformat()}
    if kind == "canonical_before_start":
        f.canonical["updated_at"] = (f.now - dt.timedelta(seconds=35)).isoformat()
    elif kind == "missing":
        f.canonical["active"].pop("started_at")
    else:
        f.canonical["active"]["started_at"] = changes[kind]
    f.write(("game_switch.json",), f.canonical)
    assert_refused(f)


@pytest.mark.parametrize("which", ["canonical", "boundary", "owner", "owner_coercion", "ledger"])
def test_drift_before_preparation_never_writes_a_reconciliation_record(legacy, monkeypatch, which):
    f = legacy
    from docich import nethack_return
    prove = nethack_return.legacy_return_proof
    calls = []

    def change(*args, **kwargs):
        result = prove(*args, **kwargs)
        calls.append(True)
        if len(calls) == 1:
            if which == "canonical":
                f.canonical["revision"] += 1
                f.write(("game_switch.json",), f.canonical)
            elif which == "boundary":
                f.second["recorded_at"] = (f.now - dt.timedelta(seconds=29)).isoformat()
                f.write(("runtimes", "g3-cccccc", "nethack_boundary.json"), f.second)
            elif which == "owner":
                f.owner["run_id"] = R
                f.write(("nethack_corner.json",), f.owner)
            elif which == "owner_coercion":
                changed = dict(f.owner, run_score=False)
                f.write(("nethack_corner.json",), changed)
            elif which == "ledger":
                f.ledger["history"].append(dict(corner="foreign", at=f.now.timestamp()))
                f.write(("corner_rotation.json",), f.ledger)
        return result

    monkeypatch.setattr(nethack_return, "legacy_return_proof", change)
    assert f.manager.recover_failed_rotation().status == "failed"
    assert RECORD_KEY not in f.manager._read_state()
    assert f.manager._read_state()["status"] == "failed"
    untouched_resources(f)


@pytest.mark.parametrize("which", ["canonical", "rotation", "tick"])
def test_busy_ownership_locks_cannot_write_or_operate(legacy, which):
    f = legacy
    lock = {"canonical": lambda: f.manager.store.lock(exclusive=True),
            "rotation": f.rotation.locked, "tick": f.manager._tick_guard}[which]
    before = f.manager.state_path.read_bytes()
    with lock():
        assert f.manager.recover_failed_rotation().status == "queued"
    assert f.manager.state_path.read_bytes() == before
    untouched_resources(f)


def test_crash_after_preparation_reproves_without_new_request(legacy, monkeypatch):
    f = legacy
    write = f.manager._write_state
    calls = []

    def crash(state):
        calls.append(deepcopy(state))
        if len(calls) == 2:
            raise OSError("synthetic crash before terminal write")
        write(state)

    monkeypatch.setattr(f.manager, "_write_state", crash)
    assert f.manager.recover_failed_rotation().status == "failed"
    prepared = f.manager._read_state()
    assert prepared["status"] == "failed" and prepared[RECORD_KEY]["phase"] == "prepared"
    monkeypatch.setattr(f.manager, "_write_state", write)
    assert f.manager.recover_failed_rotation().status == "succeeded"
    assert f.manager._read_state()[RECORD_KEY]["prepared_at"] == prepared[RECORD_KEY]["prepared_at"]
    untouched_resources(f)


def test_drift_after_preparation_retains_failed_owner_and_refuses_resume(legacy, monkeypatch):
    f = legacy
    write = f.manager._write_state

    def drift(state):
        write(state)
        changed = deepcopy(f.canonical)
        changed["revision"] += 1
        f.write(("game_switch.json",), changed)

    monkeypatch.setattr(f.manager, "_write_state", drift)
    assert f.manager.recover_failed_rotation().status == "failed"
    prepared = f.manager._read_state()
    assert prepared["status"] == "failed" and prepared[RECORD_KEY]["phase"] == "prepared"
    monkeypatch.setattr(f.manager, "_write_state", write)
    assert_refused(f)


@pytest.mark.parametrize("change", [{"schema_version": True}, {"schema_version": 1.0},
                                    {"return_request_id": OTHER}, {"canonical_revision": True}])
def test_committed_record_cannot_be_rebound_or_coerced(legacy, change):
    f = legacy
    assert f.manager.recover_failed_rotation().status == "succeeded"
    owner = f.manager._read_state()
    owner[RECORD_KEY].update(change)
    f.write(("nethack_corner.json",), owner)
    assert_refused(f)


def prepare_rotation_commit(f, monkeypatch):
    adapter = object.__new__(NethackCornerAdapter)
    adapter.manager = f.manager
    adapter.g = f.manager.g
    adapter.corner = SimpleNamespace(game="nethack", adapter="nethack")
    f.rotation.adapters = {"nethack": adapter}
    f.rotation.g = f.manager.g
    f.rotation._remember_catalog = Mock()
    monkeypatch.setattr("docich.corner_rotation.rotation_enabled", lambda g: True)


def test_regular_rotation_commits_same_terminal_reservation_without_launch(legacy, monkeypatch):
    f = legacy
    assert f.manager.recover_failed_rotation().status == "succeeded"
    prepare_rotation_commit(f, monkeypatch)
    outcome = f.rotation.recover()
    assert outcome == dict(status="ready", corner="nethack", result="interrupted", recovered=True)
    ledger = json.loads(f.rotation.path.read_text())
    assert ledger["pending"] is None and ledger["last_result"]["request_id"] == SLOT
    assert ledger["last_result"]["status"] == "interrupted"
    assert ledger["history"][:-1] == f.ledger["history"]
    assert ledger["history"][-1] == dict(corner="nethack", at=f.now.timestamp(), source="completion")
    untouched_resources(f)


def test_rotation_commit_rejects_drift_after_corner_terminalization(legacy, monkeypatch):
    f = legacy
    assert f.manager.recover_failed_rotation().status == "succeeded"
    owner = f.manager.state_path.read_bytes()
    f.canonical["revision"] += 1
    f.write(("game_switch.json",), f.canonical)
    prepare_rotation_commit(f, monkeypatch)
    with pytest.raises(RotationError, match="legacy terminal return unproven"):
        f.rotation.recover()
    ledger = json.loads(f.rotation.path.read_text())
    assert ledger["status"] == "recovery_required" and ledger["pending"] == f.ledger["pending"]
    assert ledger["history"] == f.ledger["history"]
    assert f.manager.state_path.read_bytes() == owner
    untouched_resources(f)


def test_shared_canonical_lock_covers_the_actual_rotation_ledger_write(legacy, monkeypatch):
    f = legacy
    assert f.manager.recover_failed_rotation().status == "succeeded"
    prepare_rotation_commit(f, monkeypatch)
    save = f.rotation.save
    checks = []

    def check_writer_fenced(state):
        with pytest.raises(GameSwitchBusyError):
            with f.manager.store.lock(exclusive=True):
                pytest.fail("canonical writer admitted during ledger commit")
        checks.append(True)
        save(state)

    monkeypatch.setattr(f.rotation, "save", check_writer_fenced)
    assert f.rotation.recover()["status"] == "ready"
    assert checks == [True]
    untouched_resources(f)


@pytest.mark.parametrize("fail_once", [False, True])
def test_ledger_write_failure_retries_the_same_committed_corner(legacy, monkeypatch, fail_once):
    f = legacy
    assert f.manager.recover_failed_rotation().status == "succeeded"
    owner = f.manager.state_path.read_bytes()
    prepare_rotation_commit(f, monkeypatch)
    save = f.rotation.save
    calls = []

    def write_failure(state):
        calls.append(deepcopy(state))
        if not fail_once or len(calls) == 1:
            raise OSError("synthetic write failure")
        save(state)

    monkeypatch.setattr(f.rotation, "save", write_failure)
    with pytest.raises(OSError):
        f.rotation.recover()
    assert json.loads(f.rotation.path.read_text()) == f.ledger
    assert len(calls) == 1
    if not fail_once:
        monkeypatch.setattr(f.rotation, "save", save)
    assert f.rotation.recover()["status"] == "ready"
    if fail_once:
        assert len(calls) == 2
    assert f.manager.state_path.read_bytes() == owner
    assert json.loads(f.rotation.path.read_text())["history"][:-1] == f.ledger["history"]
    untouched_resources(f)


def test_automatic_legacy_guard_does_not_change_manual_recovery_contract(legacy, monkeypatch):
    f = legacy
    prepare_rotation_commit(f, monkeypatch)
    adapter = f.rotation.adapters["nethack"]
    adapter.recovery_guard = Mock(side_effect=AssertionError("automatic guard used for manual owner"))
    adapter.reconcile_failed_start = Mock(return_value=False)
    adapter.observations = lambda: [dict(status="interrupted", rotation_request_id=OTHER,
                                        completed_at=f.now.isoformat())]
    f.ledger.update(pending=None, manual_pending=dict(
        corner="nethack", request_id=OTHER, selected_at=f.now.timestamp() - 100,
        state_file="nethack_corner_manual.json"))
    f.write(("corner_rotation.json",), f.ledger)
    owner = f.manager.state_path.read_bytes()
    assert f.rotation.recover()["status"] == "ready"
    adapter.recovery_guard.assert_not_called()
    assert f.manager.state_path.read_bytes() == owner
    untouched_resources(f)


def test_refusal_discards_in_memory_reservation_and_history_mutations(legacy, monkeypatch):
    f = legacy
    assert f.manager.recover_failed_rotation().status == "succeeded"
    prepare_rotation_commit(f, monkeypatch)

    def partially_mutate_then_refuse(state, reservation, now, *, manual):
        state["pending"] = None
        state["history"].append(dict(corner="foreign", at=now))
        state["last_slot_at"] = now
        raise RotationError("synthetic resolution refusal", kind="execution-unverified")

    monkeypatch.setattr(f.rotation, "_resolve_reservation", partially_mutate_then_refuse)
    with pytest.raises(RotationError):
        f.rotation.recover()
    ledger = json.loads(f.rotation.path.read_text())
    for key in ("pending", "history", "last_slot_at", "last_seen_at", "slot"):
        assert ledger[key] == f.ledger[key]
    assert ledger["status"] == "recovery_required"
    assert ledger == f.ledger
    untouched_resources(f)


@pytest.mark.parametrize("which", ["canonical", "tick"])
def test_transient_guard_lock_refusal_preserves_the_proof_bound_ledger_for_retry(legacy, monkeypatch, which):
    f = legacy
    assert f.manager.recover_failed_rotation().status == "succeeded"
    prepare_rotation_commit(f, monkeypatch)
    ledger_bytes = f.rotation.path.read_bytes()
    owner_bytes = f.manager.state_path.read_bytes()
    lock = (lambda: f.manager.store.lock(exclusive=True)) if which == "canonical" else f.manager._tick_guard
    with lock():
        with pytest.raises(RotationError):
            f.rotation.recover()
    assert f.rotation.path.read_bytes() == ledger_bytes
    assert f.manager.state_path.read_bytes() == owner_bytes
    assert f.manager.recover_failed_rotation().status == "succeeded"
    assert f.rotation.recover()["status"] == "ready"
    assert f.manager.state_path.read_bytes() == owner_bytes
    untouched_resources(f)


@pytest.mark.parametrize("field", ["result", "identity"])
@pytest.mark.parametrize("value", [4.0, True, False])
def test_canonical_result_generations_cannot_match_receipt_by_numeric_coercion(legacy, field, value):
    f = legacy
    if field == "result":
        f.canonical["last_result"]["generation"] = value
    else:
        f.canonical["last_result"]["active_runtime"]["generation"] = value
    f.write(("game_switch.json",), f.canonical)
    assert_refused(f)


def test_result_comparison_keeps_boolean_and_integer_types_distinct(legacy):
    f = legacy
    f.landed["result"]["fixture_flag"] = True
    f.canonical["last_result"]["fixture_flag"] = 1
    f.write(("game-switch", "requests", f"{R}.json"), f.landed)
    f.write(("game_switch.json",), f.canonical)
    assert_refused(f)


def test_error_after_atomic_ledger_replace_does_not_re_latch_consumed_reservation(legacy, monkeypatch):
    f = legacy
    assert f.manager.recover_failed_rotation().status == "succeeded"
    prepare_rotation_commit(f, monkeypatch)
    save = f.rotation.save
    calls = []

    def write_then_report_error(state):
        calls.append(deepcopy(state))
        save(state)
        raise OSError("synthetic error after atomic replace")

    monkeypatch.setattr(f.rotation, "save", write_then_report_error)
    with pytest.raises(OSError):
        f.rotation.recover()
    ledger = json.loads(f.rotation.path.read_text())
    assert len(calls) == 1
    assert ledger["status"] == "ready" and ledger["pending"] is None
    assert ledger["last_result"]["request_id"] == SLOT
    assert ledger["history"][:-1] == f.ledger["history"]
    untouched_resources(f)


def test_owner_recording_previous_game_with_source_less_receipts_uses_the_legacy_contract(legacy, monkeypatch):
    """#1969 production shape: previous_game was persisted before receipts carried
    source identities, so the receipts (not the owner field) decide legacy-ness."""
    f = legacy
    f.owner["previous_game"] = "sorengame"
    f.write(("nethack_corner.json",), f.owner)
    assert f.manager.recover_failed_rotation().status == "succeeded"
    owner = f.manager._read_state()
    assert owner["status"] == "interrupted" and owner["previous_game"] == "sorengame"
    assert owner[RECORD_KEY]["phase"] == "committed"
    before = f.manager.state_path.read_bytes()
    assert f.manager.recover_failed_rotation().status == "succeeded"
    assert f.manager.state_path.read_bytes() == before
    prepare_rotation_commit(f, monkeypatch)
    outcome = f.rotation.recover()
    assert outcome == dict(status="ready", corner="nethack", result="interrupted", recovered=True)
    ledger = json.loads(f.rotation.path.read_text())
    assert ledger["pending"] is None and ledger["last_result"]["request_id"] == SLOT
    untouched_resources(f)


@pytest.mark.parametrize("kind", ["modern_original", "modern_landed"])
def test_previous_game_owner_with_source_identities_stays_on_the_modern_path(legacy, kind):
    f = legacy
    f.owner["previous_game"] = "sorengame"
    f.write(("nethack_corner.json",), f.owner)
    if kind == "modern_original":
        f.original["result"]["source_runtime"] = {}
        f.write(("game-switch", "requests", f"{R0}.json"), f.original)
    else:
        f.landed["result"]["source_runtime"] = {}
        f.write(("game-switch", "requests", f"{R}.json"), f.landed)
    before = f.manager.state_path.read_bytes()
    assert f.manager.recover_failed_rotation().status == "failed"
    assert f.manager.state_path.read_bytes() == before
    assert RECORD_KEY not in f.manager._read_state()


def test_previous_game_owner_with_unproven_chain_is_never_prepared(legacy):
    f = legacy
    f.owner["previous_game"] = "sorengame"
    f.write(("nethack_corner.json",), f.owner)
    f.canonical["active"]["lease_id"] = OTHER
    f.write(("game_switch.json",), f.canonical)
    assert_refused(f)
    assert RECORD_KEY not in f.manager._read_state()

"""Corner reconciliation against receipts produced by the real coordinator."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
import uuid

import pytest

from docich import external_video_corner as corner
from docich.adapters import AdapterError
from docich.game_switch import GameSwitchCoordinator, GameSwitchStore
from test_coordinator import FakeAdapterFactory, FailOn


@pytest.fixture
def real_corner(tmp_path, monkeypatch):
    g = SimpleNamespace(state_dir=tmp_path)
    behaviors = {"sorengame": {}, corner.VIEW_NAME: {"name": "program"}}
    factory = FakeAdapterFactory(behaviors)
    coordinator = GameSwitchCoordinator(GameSwitchStore(tmp_path), factory,
                                        quiesce_verify_timeout_s=0.1, poll_interval_s=0.01)
    assert coordinator.start("sorengame", request_id=str(uuid.uuid4())).status == "succeeded"
    now = [1000.0]
    manager = corner.ExternalVideoCornerManager(g, coordinator=coordinator,
        clock=lambda: now[0], sleep=lambda _: now.__setitem__(0, now[0] + 61))
    receiver = dict(receiver_id=str(uuid.uuid4()), expires_at=10000, fresh=True, audio_present=True)
    monkeypatch.setattr(corner, "read_receiver", lambda *_a, **_k: receiver.copy())
    monkeypatch.setattr(corner, "other_corner_busy", lambda *_: None)
    stopped = []
    monkeypatch.setattr(corner, "request_receiver_stop", lambda _g, rid: stopped.append(rid))

    @contextmanager
    def slot(*_a, **_k):
        yield

    monkeypatch.setattr(corner, "program_slot", slot)
    return SimpleNamespace(manager=manager, coordinator=coordinator, behaviors=behaviors,
        factory=factory, source=corner.identity(manager.canonical()["active"]), stopped=stopped)


@pytest.mark.parametrize("failure", ["preflight_error", "materialize_error"])
def test_start_rollback_settles_real_live_and_replaced_runtime(real_corner, failure):
    s = real_corner
    s.behaviors[corner.VIEW_NAME][failure] = AdapterError("view start failed")
    state = s.manager.run(1)
    receipt = s.manager.store.receipts.load(state["start_request_id"])
    current = s.manager.canonical()
    assert receipt["status"] == "rolled_back"
    assert "active_runtime" not in receipt["result"]
    assert receipt["result"]["restored_generation"] == current["active"]["generation"]
    assert corner.identity(current["active"]) != s.source
    assert state["status"] == "interrupted" and not state.get("recovery_required")
    assert corner.stable(current) and not s.stopped


def test_recover_replays_real_rollback_after_lost_response(real_corner):
    s = real_corner
    rid = str(uuid.uuid4())
    s.behaviors[corner.VIEW_NAME]["materialize_error"] = AdapterError("view start failed")
    result = s.coordinator.switch(corner.VIEW_NAME, request_id=rid, timeout_s=600,
        allow_boundary_timeout_extension=False, payload={"expected_source": s.source})
    assert result.status == "rolled_back" and "active_runtime" not in result.receipt["result"]
    s.manager.save(dict(status="failed", recovery_required=True, start_request_id=rid,
        previous_runtime_identity=s.source, requested_at=1000, receiver_expires_at=10000))
    before = s.manager.canonical()
    state = s.manager.run(recovering=True)
    assert state["status"] == "interrupted" and not state["recovery_required"]
    assert s.manager.canonical() == before
    assert s.manager.store.receipts.load(rid) == result.receipt


def test_recover_start_after_failed_rollback_uses_canonical_result(real_corner):
    s = real_corner
    s.behaviors[corner.VIEW_NAME]["materialize_error"] = AdapterError("view start failed")
    s.behaviors["sorengame"]["materialize_error"] = FailOn(AdapterError("rollback failed"), 1)
    with pytest.raises(corner.ExternalVideoError):
        s.manager.run(1)
    failed = json.loads(s.manager.path.read_text())
    receipt = s.manager.store.receipts.load(failed["start_request_id"])
    assert receipt["status"] == "failed" and receipt["result"]["error_code"] == "rollback_failed"
    state = s.manager.run(recovering=True)
    assert state["status"] == "interrupted" and not state["recovery_required"]
    assert s.manager.store.receipts.load(failed["start_request_id"]) == receipt


def test_real_pending_cleanup_does_not_authorize_recovery(real_corner):
    s = real_corner
    s.behaviors[corner.VIEW_NAME].update(readiness_error=AdapterError("view failed"), immortal=True)
    with pytest.raises(corner.ExternalVideoError):
        s.manager.run(1)
    failed = json.loads(s.manager.path.read_text())
    receipt = s.manager.store.receipts.load(failed["start_request_id"])
    assert receipt["result"]["cleanup_pending"]
    assert s.manager.canonical()["retiring"]
    with pytest.raises(corner.ExternalVideoError, match="did not settle"):
        s.manager.run(recovering=True)
    assert s.manager.canonical()["retiring"] and not s.stopped
    assert [key for key in s.factory.adapters if key[0] == "sorengame"] == [("sorengame", 1)]
    # The immutable failed receipt still reports the original cleanup residue
    # after canonical recovery has positively stopped it and restored Soren.
    s.behaviors[corner.VIEW_NAME]["immortal"] = False
    state = s.manager.run(recovering=True)
    assert state["status"] == "interrupted" and not state["recovery_required"]
    assert corner.stable(s.manager.canonical()) and not s.stopped
    assert s.manager.store.receipts.load(failed["start_request_id"]) == receipt


def test_recover_rejects_a_later_real_operator_generation(real_corner):
    s = real_corner
    rid = str(uuid.uuid4())
    s.behaviors[corner.VIEW_NAME]["preflight_error"] = AdapterError("view failed")
    result = s.coordinator.switch(corner.VIEW_NAME, request_id=rid, timeout_s=600,
        allow_boundary_timeout_extension=False, payload={"expected_source": s.source})
    s.manager.save(dict(status="failed", recovery_required=True, start_request_id=rid,
        previous_runtime_identity=s.source, requested_at=1000, receiver_expires_at=10000))
    assert s.coordinator.stop(request_id=str(uuid.uuid4())).status == "succeeded"
    assert s.coordinator.start("sorengame", request_id=str(uuid.uuid4())).status == "succeeded"
    before = s.manager.canonical()
    with pytest.raises(corner.ExternalVideoError):
        s.manager.run(recovering=True)
    assert s.manager.canonical() == before and not s.stopped
    assert s.manager.store.receipts.load(rid) == result.receipt


def fail_restore(s, monkeypatch, rollback_fails=False):
    original = s.coordinator.switch

    def switch(target, **kwargs):
        result = original(target, **kwargs)
        if target == corner.VIEW_NAME and result.status == "succeeded":
            s.behaviors["sorengame"]["preflight_error"] = AdapterError("return failed")
            if rollback_fails:
                s.behaviors[corner.VIEW_NAME]["readiness_error"] = FailOn(AdapterError("view rollback failed"), 1)
        return result

    monkeypatch.setattr(s.coordinator, "switch", switch)
    with pytest.raises(corner.ExternalVideoError, match="restoration"):
        s.manager.run(1)
    failed = json.loads(s.manager.path.read_text())
    rid = failed["restore_request_id"]
    receipt = s.manager.store.receipts.load(rid)
    assert receipt["status"] == ("failed" if rollback_fails else "rolled_back")
    assert "active_runtime" not in receipt["result"] and not s.stopped
    return failed, receipt


@pytest.mark.parametrize("rollback_fails", [False, True])
def test_restore_failure_retries_only_after_real_view_rollback(real_corner, monkeypatch, rollback_fails):
    s = real_corner
    failed, receipt = fail_restore(s, monkeypatch, rollback_fails)
    rid = failed["restore_request_id"]
    s.behaviors["sorengame"].pop("preflight_error")
    state = s.manager.run(recovering=True)
    assert state["status"] == "completed" and not state["recovery_required"]
    assert state["restore_attempts"] == [rid] and state["restore_request_id"] != rid
    assert state["restored_runtime_identity"]["game"] == "sorengame"
    assert s.manager.store.receipts.load(rid) == receipt
    assert len(s.stopped) == 1 and corner.stable(s.manager.canonical())


def test_real_restore_retries_remain_bounded(real_corner, monkeypatch):
    s = real_corner
    failed, _ = fail_restore(s, monkeypatch)
    previous_id = failed["restore_request_id"]
    for attempt in range(3):
        with pytest.raises(corner.ExternalVideoError, match="restoration"):
            s.manager.run(recovering=True)
        state = json.loads(s.manager.path.read_text())
        assert len(state["restore_attempts"]) == attempt + 1
        assert state["restore_attempts"][-1] == previous_id
        assert state["restore_request_id"] != previous_id
        previous_id = state["restore_request_id"]
    before = s.manager.canonical()
    with pytest.raises(corner.ExternalVideoError, match="retry limit"):
        s.manager.run(recovering=True)
    assert s.manager.canonical() == before and not s.stopped


@pytest.mark.parametrize("mismatch", ["receipt-request", "target", "operation", "last-request",
    "last-generation", "owner", "generation", "retiring", "cleanup"])
def test_restore_retry_rejects_mismatched_real_evidence(real_corner, monkeypatch, mismatch):
    s = real_corner
    failed, receipt = fail_restore(s, monkeypatch)
    rid = failed["restore_request_id"]
    current = s.manager.canonical()
    if mismatch == "receipt-request":
        receipt["request_id"] = str(uuid.uuid4())
    elif mismatch in {"target", "operation"}:
        receipt[mismatch] = "another-game" if mismatch == "target" else "stop"
    elif mismatch == "last-request":
        current["last_result"]["request_id"] = str(uuid.uuid4())
    elif mismatch == "last-generation":
        current["last_result"]["restored_generation"] += 1
    elif mismatch == "owner":
        current["active"]["runtime_id"] = "foreign-owner"
    elif mismatch == "generation":
        current["active"]["generation"] += 1
    elif mismatch == "retiring":
        current["retiring"] = [{"game": "another-game"}]
        monkeypatch.setattr(s.coordinator, "recover", lambda **_k: SimpleNamespace(status="rolled_back"))
    elif mismatch == "cleanup":
        receipt["result"]["cleanup_pending"] = True
    monkeypatch.setattr(s.manager, "canonical", lambda: current)
    monkeypatch.setattr(s.manager.store.receipts, "load", lambda _rid: deepcopy(receipt))
    before = s.coordinator.store.canonical.load()[0]
    with pytest.raises(corner.ExternalVideoError):
        s.manager.run(recovering=True)
    state = json.loads(s.manager.path.read_text())
    assert state["restore_request_id"] == rid and not state.get("restore_attempts")
    assert state["runtime_identity"] == failed["runtime_identity"]
    assert s.coordinator.store.canonical.load()[0] == before and not s.stopped


@pytest.mark.parametrize("location,key,value", [
    ("receipt", "request_id", "another-request"),
    ("receipt", "operation", "start"),
    ("receipt", "target", "another-game"),
    ("receipt", "generation", 90),
    ("body", "request_id", "another-request"),
    ("body", "operation", "stop"),
    ("body", "status", "failed"),
    ("body", "from_game", "another-game"),
    ("body", "to_game", "another-game"),
    ("body", "generation", 90),
    ("body", "restored_generation", 90),
    ("body", "cleanup_pending", True),
    ("last", "request_id", "another-request"),
    ("last", "operation", "stop"),
    ("last", "status", "failed"),
    ("last", "from_game", "another-game"),
    ("last", "to_game", "another-game"),
    ("last", "generation", 90),
    ("last", "restored_generation", 90),
    ("last", "cleanup_pending", True),
    ("current", "phase", "retiring"),
    ("current", "retiring", [{"game": "another-game"}]),
    ("current", "candidate", {"game": "another-game"}),
    ("current", "previous", {"game": "another-game"}),
    ("current", "request_id", "another-request"),
    ("current", "cleanup_pending", True),
    ("active", "generation", 90),
    ("active", "runtime_id", "foreign-owner"),
    ("active", "game", "another-game"),
    ("result", "request_id", "another-request"),
    ("result", "status", "failed"),
    ("result", "operation", "stop"),
    ("result", "target", "another-game"),
    ("result", "generation", 90),
    ("result", "cleanup_pending", True),
])
def test_start_rejects_mismatched_real_rollback_evidence(real_corner, monkeypatch, location, key, value):
    s = real_corner
    rid = str(uuid.uuid4())
    s.behaviors[corner.VIEW_NAME]["preflight_error"] = AdapterError("view start failed")
    result = s.coordinator.switch(corner.VIEW_NAME, request_id=rid, timeout_s=600,
        allow_boundary_timeout_extension=False, payload={"expected_source": s.source})
    receipt, current = deepcopy(result.receipt), s.manager.canonical()
    objects = {"receipt": receipt, "body": receipt["result"], "last": current["last_result"],
               "current": current, "active": current["active"]}
    if location == "result":
        result = replace(result, **{key: value})
    else:
        objects[location][key] = value
    result = replace(result, receipt=receipt)
    monkeypatch.setattr(s.coordinator, "switch", lambda *_a, **_k: result)
    monkeypatch.setattr(s.manager, "canonical", lambda: current)
    state = dict(start_request_id=rid, previous_runtime_identity=s.source, status="starting")
    with pytest.raises(corner.ExternalVideoError):
        s.manager._dispatch(state)
    assert state["status"] == "failed" and state["recovery_required"]
    assert not s.stopped

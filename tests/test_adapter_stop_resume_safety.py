"""Synthetic broker/save fixtures; no VM, controller or NetHack is executed."""
from dataclasses import replace
import time
import copy
import uuid
from unittest.mock import Mock

import pytest

from docich.adapters.base import AdapterError
from docich.game_switch import GameSwitchCoordinator, RuntimeSpec
from test_coordinator import FakeAdapterFactory
from test_coordinator import CoordinatorTestBase, _runtime_dict
from test_nethack_terminal_lifecycle import failed_soren_candidate, nethack


def cleanup_id(adapter):
    identity = ":".join(str(getattr(adapter.spec, k)) for k in
                        ("game", "runtime_id", "generation", "lease_id"))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "docich:soren-candidate-cleanup:" + identity))


def broker(adapter, request_id, status):
    receipt = dict(schema=1, request_id=request_id, game=adapter.spec.game,
                   generation=adapter.spec.generation, deadline_epoch=1893456000.25,
                   deadline_at="2030-01-01T00:00:00.250Z")
    return dict(schema=1, request=receipt, ack={**receipt, "status": status},
                resource={"request_id": request_id, "game": receipt["game"],
                          "generation": receipt["generation"], "status": status})


def retirement(tmp_path, status="stopped", operation="switch"):
    adapter, store = failed_soren_candidate(tmp_path)
    state, _ = store.canonical.load()
    source = state["candidate"]
    active = _runtime_dict(3, "nethack")
    request_id = str(uuid.uuid4())
    state.update(phase="idle", candidate=None, previous=None)
    store.canonical.save(state)
    acceptance = store.accept_request(request_id, operation, None if operation == "rotate" else "nethack")
    active["runtime_id"] = acceptance.receipt["runtime_id"]
    result = {
        "request_id": request_id, "operation": operation, "status": "succeeded",
        "from_game": "sorengame", "to_game": "nethack", "generation": 3,
        "source_runtime": {k: source[k] for k in
                           ("game", "adapter", "runtime_id", "generation", "lease_id")},
        "active_runtime": {k: active[k] for k in
                           ("game", "runtime_id", "generation", "lease_id")},
    }
    source["retirement"] = copy.deepcopy(result)
    state.update(phase="ready", active=active, candidate=None, previous=None,
                 retiring=[source], next_generation=4, last_result=result)
    store.canonical.save(state)
    store.finish_request(request_id, "succeeded", result)
    payload = broker(adapter, request_id, status)
    adapter._status = Mock(return_value=payload)
    adapter.request_round_boundary = Mock()
    adapter._run = Mock(return_value=(0, {}))
    adapter._wait_status = Mock(return_value="stopped")
    return adapter, store, payload, request_id


@pytest.mark.parametrize("status", ["boundary", "stop_requested", "stopping", "stopped"])
@pytest.mark.parametrize("operation", ["switch", "rotate"])
def test_normal_source_retirement_keeps_original_stop_request(tmp_path, status, operation):
    adapter, store, payload, request_id = retirement(tmp_path, status, operation)
    before = store.canonical.load()[0]
    for _ in range(2):  # Reconstructed finalization/recovery must be idempotent.
        fresh = type(adapter)(adapter.g, adapter.game, adapter.spec)
        fresh._retired_singleton_is_superseded = Mock(return_value=False)
        fresh._status = adapter._status
        fresh.request_round_boundary = adapter.request_round_boundary
        fresh._run = adapter._run
        fresh._wait_status = adapter._wait_status
        fresh.cleanup_runtime(time.monotonic() + 10, None)
    assert [c.args[0][-1] for c in adapter._run.call_args_list] == [request_id, request_id]
    adapter.request_round_boundary.assert_not_called()
    assert store.canonical.load()[0] == before
    assert payload["request"]["request_id"] != cleanup_id(adapter)


def test_normal_retirement_with_lost_broker_does_not_create_candidate_stop(tmp_path):
    adapter, _, _, _ = retirement(tmp_path)
    adapter._status.return_value = dict(schema=1, request=None, ack=None, resource=None)
    with pytest.raises(AdapterError):
        adapter.cleanup_runtime(time.monotonic() + 10, None)
    adapter.request_round_boundary.assert_not_called()
    adapter._run.assert_not_called()


def test_normal_retirement_refuses_replacement_candidate_request(tmp_path):
    adapter, _, _, _ = retirement(tmp_path)
    adapter._status.return_value = broker(adapter, cleanup_id(adapter), "stopped")
    with pytest.raises(AdapterError):
        adapter.cleanup_runtime(time.monotonic() + 10, None)
    adapter.request_round_boundary.assert_not_called()
    adapter._run.assert_not_called()


@pytest.mark.parametrize("operation", ["switch", "rotate"])
def test_ready_recover_finalizes_normal_source_with_original_request(tmp_path, operation):
    adapter, store, _, request_id = retirement(tmp_path, operation=operation)
    state, _ = store.canonical.load()
    factory = FakeAdapterFactory({"nethack": {}})
    active = factory(RuntimeSpec.from_runtime(store.state_dir, state["active"]))
    active.runtime.materialized = active.runtime.alive = active.runtime.agent_started = True
    def make(spec):
        if spec.adapter != "soren":
            return factory(spec)
        fresh = type(adapter)(adapter.g, adapter.game, spec)
        fresh._retired_singleton_is_superseded = Mock(return_value=False)
        fresh._status = adapter._status
        fresh._run = adapter._run
        fresh._wait_status = adapter._wait_status
        return fresh
    coordinator = GameSwitchCoordinator(store, make)
    result = coordinator.recover()
    assert result.status == "succeeded"
    assert not result.cleanup_pending
    assert store.canonical.load()[0]["retiring"] == []
    assert adapter._run.call_args.args[0][-1] == request_id
    again = coordinator.recover()
    assert again.status == "succeeded" and not again.cleanup_pending
    adapter._run.assert_called_once()


@pytest.mark.parametrize("status", ["stop_requested", "stopping", "stopped"])
def test_same_candidate_stop_resumes_with_original_deadline(tmp_path, status):
    adapter, _ = failed_soren_candidate(tmp_path)
    payload = broker(adapter, cleanup_id(adapter), status)
    before = dict(payload["request"])
    adapter._status = Mock(return_value=payload)
    adapter.request_round_boundary = Mock()
    adapter._wait_round_boundary = Mock()
    adapter._run = Mock(return_value=(0, {}))
    adapter._wait_status = Mock(return_value="stopped")
    adapter.cleanup_runtime(time.monotonic() + 10, None)
    adapter.request_round_boundary.assert_not_called()
    adapter._wait_round_boundary.assert_not_called()
    assert adapter._run.call_args.args[0][-1] == before["request_id"]
    assert payload["request"] == before


@pytest.mark.parametrize("change", ["lease", "runtime", "request", "generation", "deadline", "resource", "ack", "foreign_owner"])
def test_changed_candidate_identity_cannot_resume_stop(tmp_path, change):
    adapter, store = failed_soren_candidate(tmp_path)
    payload = broker(adapter, cleanup_id(adapter), "stopping")
    if change in {"lease", "runtime"}:
        adapter.spec = replace(adapter.spec, **{change + "_id": "foreign"})
    elif change in {"request", "generation", "deadline"}:
        key = {"request": "request_id", "generation": "generation", "deadline": "deadline_epoch"}[change]
        payload["request"][key] = "foreign" if change == "request" else 999
    elif change in {"resource", "ack"}:
        payload[change]["request_id"] = "foreign"
    else:
        state, _ = store.canonical.load()
        state["previous"] = {**_runtime_dict(1, "sorengame"), "adapter": "soren"}
        store.canonical.save(state)
    adapter._status = Mock(return_value=payload)
    adapter.request_round_boundary = Mock()
    adapter._run = Mock()
    with pytest.raises(AdapterError):
        adapter.cleanup_runtime(time.monotonic() + 10, None)
    adapter.request_round_boundary.assert_not_called()
    adapter._run.assert_not_called()


@pytest.mark.parametrize("change", ["request", "source", "status", "active_lease"])
def test_unproved_normal_retirement_is_not_a_new_candidate_request(tmp_path, change):
    adapter, store, payload, _ = retirement(tmp_path)
    state, _ = store.canonical.load()
    if change == "active_lease":
        state["retiring"][0]["retirement"]["active_runtime"]["lease_id"] = str(uuid.uuid4())
    else:
        key = {"request": "request_id", "source": "from_game", "status": "status"}[change]
        state["retiring"][0]["retirement"][key] = "foreign"
    store.canonical.save(state)
    with pytest.raises(AdapterError):
        adapter.cleanup_runtime(time.monotonic() + 10, None)
    adapter.request_round_boundary.assert_not_called()
    adapter._run.assert_not_called()


@pytest.mark.parametrize("name,data", [
    ("1001docich", b"foreign uid"), ("1000docich.bak", b"backup"),
    ("1000docich", b"corrupt save"), ("1000docich.gz", b"invalid compressed save"),
])
def test_unverified_save_never_authorizes_rollback_newgame(tmp_path, name, data):
    adapter, save = nethack(tmp_path)
    (save / name).write_bytes(data)
    before = (save / name).read_bytes()
    assert adapter.can_restore_stopped_runtime(time.monotonic() + 5, None) is False
    assert (save / name).read_bytes() == before


class TestRealNethackRollbackGate(CoordinatorTestBase):
    def test_rejected_save_blocks_switch_and_recovery_launch(self):
        from pathlib import Path
        for name in ("1001docich", "1000docich.bak", "1000docich", "1000docich.gz"):
            with self.subTest(save=name):
                self.tearDown()
                self.setUp()
                adapter, save = nethack(Path(self.tempdir.name))
                contents = b"synthetic invalid save"
                (save / name).write_bytes(contents)
                self.coordinator.start("nethack")
                old = self.factory.adapter("nethack", 1)
                old.can_restore_stopped_runtime = adapter.can_restore_stopped_runtime
                self.behaviors["robots"]["readiness_error"] = AdapterError("synthetic failure")
                self.assertEqual(self.coordinator.switch("robots").status, "failed")
                self.assertIsNone(self.factory.adapter("nethack", 3))
                self.assertEqual(self.coordinator.recover().status, "failed")
                self.assertIsNone(self.factory.adapter("nethack", 3))
                self.assertEqual(self.canonical()["next_generation"], 3)
                self.assertEqual(old.runtime.events.count("materialize"), 1)
                self.assertEqual((save / name).read_bytes(), contents)

"""A cached cancellation result is not proof of the current canonical owner.

Unit tests for PR #2215's follower path. The real coordinator method is used;
only its store/transaction are controlled to make the unlocked wait explicit.
"""
from __future__ import annotations

import copy
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from docich import game_switch


REQUEST = "11111111-1111-4111-8111-111111111111"


def setup_coordinator():
    state = {"phase": "draining", "request_id": REQUEST,
             "active": {"game": "nethack", "generation": 1,
                        "runtime_id": "g1-11111111", "lease_id": "lease-1"},
             "candidate": None, "previous": None}
    obj = game_switch.GameSwitchCoordinator.__new__(game_switch.GameSwitchCoordinator)
    obj.store = SimpleNamespace(canonical=SimpleNamespace(load=lambda: (copy.deepcopy(state), False)))
    obj.step_timeouts = SimpleNamespace(round_cancel_s=5)
    obj.round_reacquire_timeout_s = 5
    obj.poll_interval_s = 0.01
    obj._boundary_cancel_claims_lock = threading.Lock()
    claim = game_switch._BoundaryCancelClaim()
    obj._boundary_cancel_claims = {REQUEST: claim}
    return obj, state, claim


@pytest.mark.parametrize("key,value", [
    ("request_id", "22222222-2222-4222-8222-222222222222"),
    ("phase", "ready"),
])
def test_cached_cancel_success_does_not_authorize_a_different_owner(key, value):
    obj, state, claim = setup_coordinator()
    claim.finish((True, None))
    state[key] = value
    before = copy.deepcopy(state)
    tx = SimpleNamespace(release_for_wait=lambda: None, reacquire_for_wait=lambda **_: None)
    calls = []
    adapter = SimpleNamespace(cancel_round_boundary=lambda *args: calls.append(args))
    with pytest.raises(game_switch.RoundBoundaryStateChangedError):
        obj._cancel_round_boundary_locked(tx, adapter, REQUEST)
    assert calls == []
    assert state == before


@pytest.mark.parametrize("key,value", [
    ("request_id", "22222222-2222-4222-8222-222222222222"),
    ("active", {"game": "robots", "generation": 2,
                "runtime_id": "g2-22222222", "lease_id": "lease-2"}),
    ("candidate", {"generation": 2}), ("previous", {"generation": 1}),
    ("phase", "ready"),
])
def test_cancel_waiter_revalidates_identity_after_reacquiring_writer(key, value):
    obj, state, claim = setup_coordinator()
    writer = threading.Lock()
    released = threading.Event()
    def release_for_wait():
        writer.release()
        released.set()
    def reacquire_for_wait(**_):
        assert writer.acquire(timeout=5)
    tx = SimpleNamespace(release_for_wait=release_for_wait, reacquire_for_wait=reacquire_for_wait)
    results, errors, calls = [], [], []
    adapter = SimpleNamespace(cancel_round_boundary=lambda *args: calls.append(args))
    def follower():
        with writer:
            try:
                results.append(obj._cancel_round_boundary_locked(tx, adapter, REQUEST))
            except BaseException as exc:
                errors.append(exc)
    worker = threading.Thread(target=follower)
    worker.start()
    try:
        assert released.wait(5)
        # Model another canonical writer completing/replacing the old request.
        with writer:
            state[key] = value
            after = copy.deepcopy(state)
            claim.finish((True, None))
    finally:
        claim.finish((True, None))
        worker.join(5)
    assert not worker.is_alive()
    assert results == []
    assert len(errors) == 1
    assert isinstance(errors[0], game_switch.RoundBoundaryStateChangedError)
    assert state == after
    assert calls == []


def test_cached_success_is_reusable_while_exact_identity_is_unchanged():
    obj, state, claim = setup_coordinator()
    claim.finish((True, None))
    before = copy.deepcopy(state)
    tx = SimpleNamespace(release_for_wait=lambda: None, reacquire_for_wait=lambda **_: None)
    adapter = SimpleNamespace(cancel_round_boundary=lambda *args: pytest.fail("duplicate cancel"))
    assert obj._cancel_round_boundary_locked(tx, adapter, REQUEST) == (True, None)
    assert state == before

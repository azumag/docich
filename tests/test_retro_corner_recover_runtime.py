"""Operator recovery for a dead scripted runtime (Issue #1280).

2026-09-28 g433: a tmux server crash killed the game/agent panes while
canonical still said hanjuku-hero ready.  Every normal ending path refuses
without terminal evidence, and the previous recovery was an ad-hoc relaunch
script.  ``retro-corner recover-runtime`` is the supported path: verify the
recorded identity, re-run the runtime's own preflight/materialize/readiness
contract, then stop through the explicit operator path (save attempt with a
bounded forced unsaved stop).
"""
import fcntl
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.adapters.base import AdapterError  # noqa: E402
from docich.retro_corner import RetroCornerError, _build_parser  # noqa: E402

from test_hanjuku_retro_registration import manager  # noqa: E402

IDENTITY = {
    "game": "hanjuku-hero", "runtime_id": "g433-c9fbf24a",
    "generation": 433, "lease_id": "lease",
}


def _state(status="failed"):
    return {
        "schema_version": 1, "status": status, "game": "hanjuku-hero",
        "previous_game": "sorengame", "bot_identity": dict(IDENTITY),
        "bot_runtime_id": IDENTITY["runtime_id"],
    }


def _canonical(identity=None, phase="ready"):
    return {"phase": phase, "active": {**(identity or IDENTITY), "adapter": "retroarch"}}


class FakeAdapter:
    def __init__(self, events, fail=None):
        self.events, self.fail = events, fail

    def _step(self, name):
        self.events.append(name)
        if self.fail == name:
            raise AdapterError(f"{name} failed")

    def preflight(self, deadline, cancel):
        self._step("preflight")

    def materialize_runtime(self, deadline, cancel):
        self._step("materialize")

    def readiness(self, deadline, cancel):
        self._step("readiness")


def _install_adapter(manager, monkeypatch, adapter):
    seen = {}
    monkeypatch.setattr(
        "docich.retro_corner.make_coordinator_adapter",
        lambda g, spec: (seen.update(spec=spec), adapter)[1],
    )
    return seen


def test_recover_runtime_relaunches_and_then_stops(manager, monkeypatch):
    manager._write_state(_state())
    manager.store.canonical.load = Mock(return_value=(_canonical(), False))
    events = []
    seen = _install_adapter(manager, monkeypatch, FakeAdapter(events))
    stop = Mock(return_value="stopped")
    monkeypatch.setattr(manager, "_stop_direct", stop)

    assert manager.recover_runtime() == "stopped"

    manager._ensure_runtime.assert_called_once_with()
    assert events == ["preflight", "materialize", "readiness"]
    # The rebuilt spec is the recorded runtime, never a newly allocated one.
    assert seen["spec"].runtime_id == IDENTITY["runtime_id"]
    assert seen["spec"].generation == IDENTITY["generation"]
    stop.assert_called_once_with()


def test_recover_runtime_reaches_force_stop_when_readiness_fails(manager, monkeypatch):
    """A presenter that never becomes ready must fail closed, not stop blind."""

    manager._write_state(_state())
    manager.store.canonical.load = Mock(return_value=(_canonical(), False))
    events = []
    _install_adapter(manager, monkeypatch, FakeAdapter(events, fail="readiness"))
    stop = Mock()
    monkeypatch.setattr(manager, "_stop_direct", stop)

    with pytest.raises(AdapterError):
        manager.recover_runtime()

    assert events == ["preflight", "materialize", "readiness"]
    stop.assert_not_called()


@pytest.mark.parametrize("identity", [
    {**IDENTITY, "lease_id": "other"},
    {**IDENTITY, "runtime_id": "g432-deadbeef"},
    {**IDENTITY, "game": "sorengame"},
])
def test_recover_runtime_fails_closed_on_identity_mismatch(manager, monkeypatch, identity):
    manager._write_state(_state())
    manager.store.canonical.load = Mock(return_value=(_canonical(identity), False))
    seen = _install_adapter(manager, monkeypatch, FakeAdapter([]))

    with pytest.raises(RetroCornerError, match="identity"):
        manager.recover_runtime()

    assert "spec" not in seen
    manager._ensure_runtime.assert_not_called()


def test_recover_runtime_refuses_a_non_ready_canonical_phase(manager, monkeypatch):
    manager._write_state(_state())
    manager.store.canonical.load = Mock(return_value=(_canonical(phase="draining"), False))
    seen = _install_adapter(manager, monkeypatch, FakeAdapter([]))

    with pytest.raises(RetroCornerError, match="identity"):
        manager.recover_runtime()

    assert "spec" not in seen


@pytest.mark.parametrize("status", ["completed", "interrupted", "idle"])
def test_recover_runtime_is_a_noop_outside_active_or_failed(manager, status):
    manager._write_state({**_state(), "status": status})
    result = manager.recover_runtime()
    assert result.status == "noop" and result.detail == "not-recoverable"


def test_recover_runtime_refuses_while_a_tick_holds_the_guard(manager, monkeypatch):
    manager._write_state(_state())
    manager.tick_guard_path.parent.mkdir(parents=True, exist_ok=True)
    handle = manager.tick_guard_path.open("a+")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        result = manager.recover_runtime()
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()

    assert result.status == "queued" and result.detail == "already-running"
    manager._ensure_runtime.assert_not_called()


def test_recover_runtime_is_a_cli_command():
    args = _build_parser().parse_args(["recover-runtime"])
    assert args.command == "recover-runtime"

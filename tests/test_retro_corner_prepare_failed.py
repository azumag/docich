"""The inherited game/Meriken lifecycle must consume proved prepare rollbacks."""
from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

import pytest

from docich import config
from docich.game_switch import ERROR_PREPARE_FAILED, runtime_names
from docich.retro_corner import RetroCornerError, RetroCornerManager
from docich.soren91_corner import Soren91CornerManager
from docich.soren91_corner_manual import ManualSoren91CornerManager


class RolledBackPrepareCoordinator:
    """Persist a coordinator-shaped rollback without launching any runtime."""

    def __init__(self):
        self.store = None
        self.calls = []

    def switch(self, target, *, request_id, **kwargs):
        self.calls.append(("switch", target, request_id))
        receipt = self.store.receipts.load(request_id)
        if receipt is None:
            accepted = self.store.accept_request(request_id, "switch", target)
            restored = accepted.generation + 1
            names = runtime_names(restored)
            result = {
                "request_id": request_id, "operation": "switch", "status": "rolled_back",
                "from_game": "sorengame", "to_game": target,
                "generation": accepted.generation, "restored_generation": restored,
                "error_code": ERROR_PREPARE_FAILED, "cleanup_pending": False,
                "detail": "synthetic prepare failure; previous runtime restored",
            }
            canonical, _ = self.store.canonical.load()
            canonical["active"].update(
                generation=restored, runtime_id=f"g{restored}-abcdef",
                game_window=names.game_window, agent_window=names.agent_window,
                adapter_session=names.adapter_session,
            )
            canonical.update(
                phase="ready", next_generation=restored + 1,
                operation=None, request_id=None, deadline_at=None, last_result=result,
            )
            self.store.canonical.save(canonical)
            self.store.finish_request(request_id, "rolled_back", result)
            receipt = self.store.receipts.load(request_id)
        return SimpleNamespace(**receipt["result"])

    def recover(self):
        self.calls.append(("recover",))
        return SimpleNamespace(status="succeeded")


@pytest.fixture(params=[RetroCornerManager, Soren91CornerManager, ManualSoren91CornerManager],
                ids=["retro", "meriken", "manual-meriken"])
def failed_prepare_start(tmp_path, request):
    games = tmp_path / "config" / "games"
    games.mkdir(parents=True)
    (tmp_path / "config" / "docich.toml").write_text('''
[paths]
state_dir = "run"
games_dir = "config/games"
[retro_corner]
enabled = true
games = ["robots"]
[soren91_corner]
enabled = true
''', encoding="utf-8")
    for game, adapter in (("robots", "cli"), ("soren91", "soren91"),
                          ("sorengame", "browser")):
        (games / f"{game}.toml").write_text(
            f'[game]\nname = "{game}"\ntitle = "{game}"\nadapter = "{adapter}"\n'
            '[agent]\nenabled = true\nbrain = "resolver"\n',
            encoding="utf-8",
        )
    coordinator = RolledBackPrepareCoordinator()
    manager = request.param(
        config.load_global(tmp_path), coordinator=coordinator,
        now=lambda: datetime(2026, 10, 16, tzinfo=timezone.utc),
        sleep=lambda _: None, active_game_reader=lambda: "sorengame",
        ensure_runtime=lambda: None,
    )
    coordinator.store = manager.store
    canonical = manager.store.initialize()
    names = runtime_names(1)
    canonical.update(phase="ready", next_generation=2, active={
        "game": "sorengame", "adapter": "browser", "generation": 1,
        "runtime_id": "g1-abcdef", "lease_id": str(uuid.uuid4()),
        "game_window": names.game_window, "agent_window": names.agent_window,
        "adapter_session": names.adapter_session, "started_at": "2026-10-16T00:00:00Z",
    })
    manager.store.canonical.save(canonical)
    request_id = str(uuid.uuid4())
    target = "robots" if request.param is RetroCornerManager else "soren91"
    # Exercise the real run_rotation -> _begin_locked -> transition failure
    # path, rather than manufacturing the manager's failed state by hand.
    with pytest.raises(RetroCornerError):
        manager.run_rotation(request_id, target)
    state = manager._read_state()
    assert state["status"] == "failed"
    assert state["last_error_code"] == ERROR_PREPARE_FAILED
    assert state["rotation_request_id"] == state["switch_request_id"] == request_id
    assert coordinator.calls == (
        [("switch", target, request_id), ("recover",), ("switch", target, request_id)]
        if request.param is Soren91CornerManager else [("switch", target, request_id)]
    )
    return manager, coordinator, request_id


def test_prepare_failed_rollback_reconciles_inherited_failed_start(failed_prepare_start):
    manager, coordinator, request_id = failed_prepare_start
    before = manager._read_state()
    receipt_before = manager.store.receipts.load(request_id)
    canonical_before, _ = manager.store.canonical.load()
    calls_before = list(coordinator.calls)

    assert manager.reconcile_failed_rotation_start(request_id) is True
    after = manager._read_state()
    assert after["status"] == "interrupted"
    assert after["end_reason"] == "switch-terminal-before-corner-active"
    assert after["completed_at"] == before["completed_at"]
    assert after["rotation_request_id"] == after["switch_request_id"] == request_id
    assert after["last_error"] is None
    assert after["last_error_code"] is None
    assert manager.reconcile_failed_rotation_start(request_id) is True
    assert manager._read_state() == after
    assert manager.store.receipts.load(request_id) == receipt_before
    assert manager.store.canonical.load()[0] == canonical_before
    assert coordinator.calls == calls_before


@pytest.mark.parametrize("gate", [
    "receipt-request", "receipt-target", "receipt-code", "receipt-cleanup",
    "draining", "candidate", "previous", "retiring", "latest-cleanup",
])
def test_prepare_failed_keeps_incomplete_or_mismatched_evidence_latched(
        failed_prepare_start, monkeypatch, gate):
    manager, coordinator, request_id = failed_prepare_start
    receipt = manager.store.receipts.load(request_id)
    canonical, _ = manager.store.canonical.load()
    if gate == "receipt-request":
        receipt["result"]["request_id"] = str(uuid.uuid4())
    elif gate == "receipt-target":
        receipt["target"] = "unrelated-game"
    elif gate == "receipt-code":
        receipt["result"]["error_code"] = "start_failed"
    elif gate == "receipt-cleanup":
        receipt["result"]["cleanup_pending"] = True
    elif gate == "draining":
        canonical["phase"] = "draining"
    elif gate == "latest-cleanup":
        canonical["last_result"]["cleanup_pending"] = True
    else:
        runtime = dict(canonical["active"])
        canonical[gate] = [runtime] if gate == "retiring" else runtime
    # Include malformed evidence independently of the store's own validation.
    monkeypatch.setattr(manager.store.receipts, "load", lambda _: receipt)
    monkeypatch.setattr(manager.store.canonical, "load", lambda: (canonical, False))
    before = manager.state_path.read_bytes()
    calls_before = list(coordinator.calls)

    assert manager.reconcile_failed_rotation_start(request_id) is False
    assert manager.state_path.read_bytes() == before
    assert coordinator.calls == calls_before

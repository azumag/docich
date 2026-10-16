"""Shared fail-closed contract for the four view corners' prepare failures."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.doushi_corner import DoushiCornerManager
from docich.game_switch import ERROR_PREPARE_FAILED, runtime_names
from docich.tsuitate_corner import TsuitateCornerManager
from docich.weather_corner import WeatherCornerManager
from docich.yoso_corner import YosoCornerManager


@pytest.fixture(params=[
    (DoushiCornerManager, "doushi-view"),
    (YosoCornerManager, "yosou-view"),
    (WeatherCornerManager, "weather-view"),
    (TsuitateCornerManager, "tsuitate-view"),
], ids=["doushi", "yoso", "weather", "tsuitate"])
def rolled_back_prepare_failure(tmp_path, request):
    manager_type, view = request.param
    # No coordinator method may run: reconciliation consumes durable evidence.
    manager = manager_type(SimpleNamespace(state_dir=tmp_path / "run"),
                           coordinator=object(), clock=lambda: 1002.0)
    request_id = str(uuid.uuid4())
    previous = {
        "game": "robots", "runtime_id": "g1-abcdef", "generation": 1,
        "lease_id": str(uuid.uuid4()),
    }
    names = runtime_names(1)
    runtime = {
        **previous, "adapter": "cli", "game_window": names.game_window,
        "agent_window": names.agent_window, "adapter_session": names.adapter_session,
        "started_at": "2026-10-16T00:00:00Z",
    }
    manager._save({
        "schema_version": 1, "game": view, "status": "failed",
        "rotation_request_id": request_id, "start_request_id": request_id,
        "switch_request_id": request_id, "switch_status": "rolled_back",
        "previous_game": previous["game"], "previous_runtime_identity": previous,
        "requested_at": 1000.0, "completed_at": 1001.0,
        "last_error_code": ERROR_PREPARE_FAILED,
    })
    canonical = manager.store.initialize()
    canonical.update(phase="ready", next_generation=2, active=runtime)
    manager.store.canonical.save(canonical)
    accepted = manager.store.accept_request(request_id, "switch", view)
    result = {
        "request_id": request_id, "operation": "switch", "status": "rolled_back",
        "from_game": previous["game"], "to_game": view,
        "generation": accepted.generation, "restored_generation": 1,
        "error_code": ERROR_PREPARE_FAILED, "cleanup_pending": False,
        "detail": "synthetic prepare failure; previous game restored",
    }
    canonical, _ = manager.store.canonical.load()
    canonical.update(
        phase="ready", operation=None, request_id=None, deadline_at=None,
        candidate=None, previous=None, retiring=[], active=runtime,
        next_generation=accepted.generation + 1, last_result=result,
    )
    manager.store.canonical.save(canonical)
    manager.store.finish_request(request_id, "rolled_back", result)
    return manager, request_id


def test_prepare_failed_rollback_interrupts_each_view_corner(rolled_back_prepare_failure):
    manager, request_id = rolled_back_prepare_failure
    receipt_before = manager.store.receipts.load(request_id)
    canonical_before, _ = manager.store.canonical.load()

    assert manager.reconcile_failed_start(request_id) is True

    state = json.loads(manager.state_path.read_text())
    assert state["status"] == "interrupted"
    assert state["end_reason"] == "switch-terminal-before-corner-active"
    assert state["completed_at"] == 1001.0
    assert state["rotation_request_id"] == state["start_request_id"] == request_id
    assert state["last_error_code"] == ERROR_PREPARE_FAILED
    assert "switch_request_id" not in state
    assert "switch_status" not in state
    assert manager.store.receipts.load(request_id) == receipt_before
    assert manager.store.canonical.load()[0] == canonical_before


@pytest.mark.parametrize("field, value", [
    ("request_id", "11111111-1111-4111-8111-111111111111"),
    ("target", "unrelated-view"),
    ("operation", "stop"),
    ("result.request_id", "11111111-1111-4111-8111-111111111111"),
    ("result.to_game", "unrelated-view"),
    ("result.error_code", "start_failed"),
    ("result.generation", 999),
    ("result.cleanup_pending", True),
])
def test_prepare_failed_stays_latched_for_mismatched_or_pending_receipt(
        rolled_back_prepare_failure, monkeypatch, field, value):
    manager, request_id = rolled_back_prepare_failure
    receipt_before = manager.store.receipts.load(request_id)
    canonical_before, _ = manager.store.canonical.load()
    state_before = manager.state_path.read_bytes()
    receipt = copy.deepcopy(receipt_before)
    if field.startswith("result."):
        receipt["result"][field.split(".")[1]] = value
    else:
        receipt[field] = value
    # Isolate each corner's evidence gate, including malformed identities that
    # the real receipt store also rejects before returning them.
    with monkeypatch.context() as patch:
        patch.setattr(manager.store.receipts, "load", lambda _request_id: receipt)
        assert manager.reconcile_failed_start(request_id) is False

    assert manager.state_path.read_bytes() == state_before
    assert manager.store.receipts.load(request_id) == receipt_before
    assert manager.store.canonical.load()[0] == canonical_before

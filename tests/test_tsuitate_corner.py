import json
from pathlib import Path
import sys
import uuid
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.corner_adapters import CornerExecutionError, TsuitateCornerAdapter
from docich.corner_catalog import Corner
from docich.tsuitate_beta_control import ControlError
from docich.tsuitate_corner import TsuitateCornerManager
from docich.tsuitate_view import VIEW_NAME


REQ = "11111111-1111-4111-8111-111111111111"


def _global(tmp_path):
    state = tmp_path / "run"
    state.mkdir(parents=True)
    return SimpleNamespace(state_dir=state, config_path=None)


def _status(state="stopped", **kw):
    value = {
        "state": state,
        "runId": None,
        "gameId": None,
        "brainVersion": "tsuitate-brain-v2",
        "completedGames": 0,
        "reservedGames": 0,
        "stopRequested": False,
        "readyForNextRun": state in {"stopped", "finished", "queue_timeout"},
    }
    value.update(kw)
    return value


def _active_state():
    identity = {
        "game": VIEW_NAME,
        "runtime_id": "g4-a1b2c3d4",
        "generation": 4,
        "lease_id": str(uuid.uuid4()),
    }
    return {
        "schema_version": 1,
        "game": VIEW_NAME,
        "status": "active",
        "rotation_request_id": REQ,
        "view_runtime_identity": identity,
        "beta_started": True,
        "started_at": 1000.0,
    }, identity


def _canonical(identity):
    return {
        "phase": "ready",
        "active": dict(identity),
        "candidate": None,
        "previous": None,
        "retiring": [],
    }


def test_eligible_requires_ready_singleton(tmp_path):
    g = _global(tmp_path)
    manager = TsuitateCornerManager(g, coordinator=object(), control=lambda *_: _status())
    assert manager.eligible() is True
    manager.control = lambda *_: _status("paused", readyForNextRun=False)
    assert manager.eligible() is False


def test_start_is_idempotent_after_ambiguous_control_response(tmp_path):
    g = _global(tmp_path)
    calls = []
    phase = {"accepted": False}

    def control(action, run_id=None):
        calls.append((action, run_id))
        if action == "status":
            if phase["accepted"]:
                return _status("queued", runId=REQ, reservedGames=1, readyForNextRun=False)
            return _status()
        if action == "start":
            phase["accepted"] = True
            raise ControlError("control_timeout")
        raise AssertionError(action)

    manager = TsuitateCornerManager(g, coordinator=object(), control=control)
    state = {"schema_version": 1, "game": VIEW_NAME, "status": "active",
             "rotation_request_id": REQ, "beta_started": False}
    manager._save(state)

    assert manager._ensure_beta_started(state) == "queued"
    assert manager._ensure_beta_started(state) is None
    assert [action for action, _ in calls].count("start") == 1
    assert state["beta_started"] is True


def test_paused_result_blocks_corner_until_explicit_reconcile(tmp_path, monkeypatch):
    g = _global(tmp_path)
    state, identity = _active_state()
    state["rotation_request_id"] = REQ
    manager = TsuitateCornerManager(
        g, coordinator=object(),
        control=lambda *_: _status("paused", runId=REQ, readyForNextRun=False),
        sleep=lambda *_: None,
    )
    manager._save(state)
    monkeypatch.setattr(manager, "_canonical", lambda: _canonical(identity))
    restored = []
    monkeypatch.setattr(manager, "_restore", lambda *_args, **_kwargs: restored.append(True) or "completed")

    assert manager._wait_and_restore(state) == "queued"
    assert restored == []
    assert json.loads(manager.path.read_text())["beta_state"] == "paused"


def test_finished_match_restores_owned_view(tmp_path, monkeypatch):
    g = _global(tmp_path)
    state, identity = _active_state()
    manager = TsuitateCornerManager(
        g, coordinator=object(),
        control=lambda *_: _status(
            "finished", runId=REQ, completedGames=1, reservedGames=1,
            readyForNextRun=True,
        ),
        sleep=lambda *_: None,
    )
    manager._save(state)
    monkeypatch.setattr(manager, "_canonical", lambda: _canonical(identity))
    seen = []
    monkeypatch.setattr(
        manager, "_restore",
        lambda current, *, end_reason: seen.append((current, end_reason)) or "completed",
    )

    assert manager._wait_and_restore(state) == "completed"
    assert seen[0][1] == "game-completed"


def test_manual_stop_requests_beta_stop_before_restore(tmp_path, monkeypatch):
    g = _global(tmp_path)
    state, identity = _active_state()
    responses = iter([
        _status("playing", runId=REQ, reservedGames=1, readyForNextRun=False),
        _status("stopped", runId=REQ, reservedGames=1, readyForNextRun=True),
    ])
    calls = []

    def control(action, run_id=None):
        calls.append((action, run_id))
        if action == "stop":
            return _status("draining", runId=REQ, reservedGames=1, readyForNextRun=False)
        return next(responses)

    manager = TsuitateCornerManager(g, coordinator=object(), control=control, sleep=lambda *_: None)
    manager._save(state)
    monkeypatch.setattr(manager, "_canonical", lambda: _canonical(identity))
    monkeypatch.setattr(manager, "_stop_requested", lambda: True)
    seen = []
    monkeypatch.setattr(
        manager, "_restore",
        lambda current, *, end_reason: seen.append(end_reason) or "completed",
    )

    assert manager._wait_and_restore(state) == "completed"
    assert ("stop", REQ) in calls
    assert seen == ["manual"]


def test_rotation_adapter_loads_only_beta_control_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    env_file = home / ".config" / "docich" / "webui.env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text(
        "DOCICH_BETA_CONTROL_SECRET=fixture-control-secret-12345678901234567890\n"
        "DOCICH_BETA_CONTROL_URL=https://docich-tsuitate-bot.fixture.workers.dev\n"
        "DOCICH_WEBUI_TOKEN=operator-secret-never-export\n"
        "OTHER_PRIVATE_VALUE=never-read\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    for key in ("DOCICH_BETA_CONTROL_SECRET", "DOCICH_BETA_CONTROL_URL",
                "DOCICH_WEBUI_TOKEN", "DOCICH_WEBUI_READONLY_TOKEN"):
        monkeypatch.delenv(key, raising=False)

    adapter = TsuitateCornerAdapter(
        _global(tmp_path), Corner("tsuitate", "tsuitate", VIEW_NAME, manual_only=True)
    )
    captured = {}

    def eligible():
        import os
        captured["secret"] = os.environ.get("DOCICH_BETA_CONTROL_SECRET")
        captured["url"] = os.environ.get("DOCICH_BETA_CONTROL_URL")
        captured["webui"] = os.environ.get("DOCICH_WEBUI_TOKEN")
        captured["other"] = os.environ.get("OTHER_PRIVATE_VALUE")
        return True

    adapter.manager.eligible = eligible
    assert adapter.eligible() is True
    assert captured == {
        "secret": "fixture-control-secret-12345678901234567890",
        "url": "https://docich-tsuitate-bot.fixture.workers.dev",
        "webui": None,
        "other": None,
    }
    import os
    assert os.environ.get("DOCICH_BETA_CONTROL_SECRET") is None
    assert os.environ.get("DOCICH_BETA_CONTROL_URL") is None


def test_rotation_adapter_rejects_control_secret_reusing_webui_token(tmp_path, monkeypatch):
    home = tmp_path / "home"
    env_file = home / ".config" / "docich" / "webui.env"
    env_file.parent.mkdir(parents=True)
    same = "shared-secret-that-must-not-be-reused-123456"
    env_file.write_text(
        f"DOCICH_BETA_CONTROL_SECRET={same}\n"
        "DOCICH_BETA_CONTROL_URL=https://docich-tsuitate-bot.fixture.workers.dev\n"
        f"DOCICH_WEBUI_TOKEN={same}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    for key in ("DOCICH_BETA_CONTROL_SECRET", "DOCICH_BETA_CONTROL_URL",
                "DOCICH_WEBUI_TOKEN", "DOCICH_WEBUI_READONLY_TOKEN"):
        monkeypatch.delenv(key, raising=False)

    adapter = TsuitateCornerAdapter(
        _global(tmp_path), Corner("tsuitate", "tsuitate", VIEW_NAME, manual_only=True)
    )
    with pytest.raises(CornerExecutionError, match="must not reuse"):
        adapter.eligible()

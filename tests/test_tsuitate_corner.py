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
from docich.tsuitate_corner import TsuitateCornerError, TsuitateCornerManager
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


def _settled_result(**overrides):
    value = {"outcome": "win", "reason": "checkmate", "endedAt": "2026-10-06T00:05:00.000Z",
             "moveNumber": 9, "resultConfidence": "verified"}
    value.update(overrides)
    return value


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


def test_manual_stop_before_beta_start_does_not_start_match(tmp_path, monkeypatch):
    g = _global(tmp_path)
    state, identity = _active_state()
    state["beta_started"] = False
    calls = []

    def control(action, run_id=None):
        calls.append((action, run_id))
        if action == "status":
            return _status()
        raise AssertionError(f"unexpected beta action: {action}")

    manager = TsuitateCornerManager(
        g, coordinator=object(), control=control, sleep=lambda *_: None
    )
    manager._save(state)
    monkeypatch.setattr(manager, "_canonical", lambda: _canonical(identity))
    monkeypatch.setattr(manager, "_stop_requested", lambda: True)
    restored = []
    monkeypatch.setattr(
        manager, "_restore",
        lambda current, *, end_reason: restored.append(end_reason) or "completed",
    )

    assert manager._wait_and_restore(state) == "completed"
    assert restored == ["manual"]
    assert calls == [("status", None)]


def test_finished_match_records_the_settled_result_for_this_run(tmp_path, monkeypatch):
    g = _global(tmp_path)
    state, identity = _active_state()
    manager = TsuitateCornerManager(
        g, coordinator=object(),
        control=lambda *_: _status("finished", runId=REQ, completedGames=1, reservedGames=1,
                                  readyForNextRun=True, gameResult=_settled_result()),
        sleep=lambda *_: None,
    )
    manager._save(state)
    monkeypatch.setattr(manager, "_canonical", lambda: _canonical(identity))
    monkeypatch.setattr(manager, "_restore", lambda *_a, **_k: "completed")

    assert manager._wait_and_restore(state) == "completed"
    saved = json.loads(manager.path.read_text())
    assert saved["beta_result"] == _settled_result()
    assert "yourPieces" not in saved["beta_result"]


@pytest.mark.parametrize("run_id,beta_state", [(None, "finished"), (REQ, "playing"), (REQ, "stopped")])
def test_result_is_not_recorded_without_a_settled_owned_match(tmp_path, run_id, beta_state):
    g = _global(tmp_path)
    state, _identity = _active_state()
    manager = TsuitateCornerManager(
        g, coordinator=object(),
        control=lambda *_: _status(beta_state, runId=run_id,
                                   completedGames=1 if beta_state == "finished" else 0,
                                   reservedGames=1, readyForNextRun=beta_state == "finished",
                                   gameResult=_settled_result()),
    )
    manager._save(state)

    assert manager._control_status(state) is not None
    assert json.loads(manager.path.read_text()).get("beta_result") is None


def test_result_from_another_run_fails_closed_without_recording_it(tmp_path, monkeypatch):
    g = _global(tmp_path)
    state, identity = _active_state()
    manager = TsuitateCornerManager(
        g, coordinator=object(),
        control=lambda *_: _status("finished", runId="other-run", completedGames=1,
                                   reservedGames=1, readyForNextRun=True,
                                   gameResult=_settled_result()),
        sleep=lambda *_: None,
    )
    manager._save(state)
    monkeypatch.setattr(manager, "_canonical", lambda: _canonical(identity))

    with pytest.raises(TsuitateCornerError, match="another run"):
        manager._wait_and_restore(state)
    assert json.loads(manager.path.read_text()).get("beta_result") is None


def test_match_deadline_stops_once_then_restores_with_its_own_reason(tmp_path, monkeypatch):
    g = _global(tmp_path)
    state, identity = _active_state()
    clock = {"now": 1000.0}
    calls = []
    responses = iter([
        _status("playing", runId=REQ, reservedGames=1, readyForNextRun=False),
        _status("draining", runId=REQ, reservedGames=1, readyForNextRun=False),
        _status("stopped", runId=REQ, reservedGames=1, readyForNextRun=True),
    ])

    def control(action, run_id=None):
        calls.append((action, run_id))
        if action == "stop":
            return _status("draining", runId=REQ, reservedGames=1, readyForNextRun=False)
        return next(responses)

    manager = TsuitateCornerManager(g, coordinator=object(), control=control,
                                    sleep=lambda *_: clock.__setitem__("now", clock["now"] + 60),
                                    poll_s=1.0, max_match_seconds=60)
    manager.clock = lambda: clock["now"]
    manager._save(state)
    monkeypatch.setattr(manager, "_canonical", lambda: _canonical(identity))
    monkeypatch.setattr(manager, "_stop_requested", lambda: False)
    seen = []
    monkeypatch.setattr(manager, "_restore",
                        lambda _s, *, end_reason: seen.append(end_reason) or "completed")

    # The bound is measured from the first active observation, so the very first
    # tick cannot already be expired.
    assert manager._wait_and_restore(state) == "completed"
    assert seen == ["match-timeout"]
    assert [action for action, _ in calls].count("stop") == 1
    saved = json.loads(manager.path.read_text())
    assert saved["beta_timeout_requested"] is True
    assert saved["beta_active_since"] == 1000.0


def test_operator_stop_wins_over_the_deadline_in_the_end_reason(tmp_path, monkeypatch):
    g = _global(tmp_path)
    state, identity = _active_state()
    state["beta_timeout_requested"] = True
    state["beta_active_since"] = 0.0
    clock = {"now": 100_000.0}
    responses = iter([
        _status("playing", runId=REQ, reservedGames=1, readyForNextRun=False),
        _status("stopped", runId=REQ, reservedGames=1, readyForNextRun=True),
    ])

    def control(action, run_id=None):
        if action == "stop":
            return _status("draining", runId=REQ, reservedGames=1, readyForNextRun=False)
        return next(responses)

    manager = TsuitateCornerManager(g, coordinator=object(), control=control,
                                    sleep=lambda *_: None, max_match_seconds=60)
    manager.clock = lambda: clock["now"]
    manager._save(state)
    monkeypatch.setattr(manager, "_canonical", lambda: _canonical(identity))
    monkeypatch.setattr(manager, "_stop_requested", lambda: True)
    seen = []
    monkeypatch.setattr(manager, "_restore",
                        lambda _s, *, end_reason: seen.append(end_reason) or "completed")

    assert manager._wait_and_restore(state) == "completed"
    assert seen == ["manual"]


def test_match_deadline_never_expires_before_the_match_is_active(tmp_path, monkeypatch):
    g = _global(tmp_path)
    state, identity = _active_state()
    manager = TsuitateCornerManager(g, coordinator=object(),
                                    control=lambda *_: _status("stopped", runId=REQ,
                                                                reservedGames=1, readyForNextRun=True),
                                    sleep=lambda *_: None, max_match_seconds=1)
    manager.clock = lambda: 10_000.0
    manager._save(state)
    monkeypatch.setattr(manager, "_canonical", lambda: _canonical(identity))
    monkeypatch.setattr(manager, "_stop_requested", lambda: False)
    monkeypatch.setattr(manager, "_restore", lambda *_a, **_k: "completed")

    assert manager._wait_and_restore(state) == "completed"
    saved = json.loads(manager.path.read_text())
    assert saved.get("beta_timeout_requested") is None
    assert saved.get("beta_active_since") is None


def test_out_of_range_max_match_seconds_is_rejected(tmp_path):
    g = _global(tmp_path)
    for bad in [0, -1, float("nan"), float("inf"), 86401, "600", True]:
        with pytest.raises(TsuitateCornerError, match="out of range"):
            TsuitateCornerManager(g, coordinator=object(), control=lambda *_: _status(),
                                  max_match_seconds=bad)


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


def _failed_rollback_setup(tmp_path):
    """Craft the prod latch shape on disk: a start that failed *and* whose
    rollback failed (``rollback_failed``), since recovered by a later
    reviewed recovery back to the previous game."""
    from docich.game_switch import runtime_names

    g = _global(tmp_path)
    manager = TsuitateCornerManager(g, coordinator=object())
    request_id = str(uuid.uuid4())
    previous_identity = {
        "game": "sorengame",
        "runtime_id": "g1-abcdef",
        "generation": 1,
        "lease_id": str(uuid.uuid4()),
    }
    manager._save({
        "schema_version": 1,
        "game": VIEW_NAME,
        "status": "failed",
        "rotation_request_id": request_id,
        "start_request_id": request_id,
        "switch_request_id": request_id,
        "previous_game": "sorengame",
        "previous_runtime_identity": previous_identity,
        "requested_at": 1000.0,
        "starting_at": 1000.0,
        "completed_at": 1001.0,
        "last_error_code": "rollback_failed",
    })
    names = runtime_names(1)
    canonical = manager.store.initialize()
    canonical.update(phase="ready", next_generation=2, active={
        **previous_identity,
        "adapter": "cli",
        "game_window": names.game_window,
        "agent_window": names.agent_window,
        "adapter_session": names.adapter_session,
        "started_at": "2026-10-07T05:19:02Z",
    })
    manager.store.canonical.save(canonical)
    accepted = manager.store.accept_request(request_id, "switch", VIEW_NAME)
    result = {
        "request_id": request_id,
        "operation": "switch",
        "status": "failed",
        "from_game": None,
        "to_game": VIEW_NAME,
        "generation": accepted.generation,
        "error_code": "rollback_failed",
    }
    # The failed receipt never changed the owner: the previous game is still
    # the stable active runtime after the reviewed recovery.
    canonical, _ = manager.store.canonical.load()
    canonical.update(phase="ready", operation=None, request_id=None,
                     deadline_at=None, candidate=None, previous=None,
                     retiring=[], next_generation=accepted.generation + 1,
                     active={
                         **previous_identity,
                         "adapter": "cli",
                         "game_window": names.game_window,
                         "agent_window": names.agent_window,
                         "adapter_session": names.adapter_session,
                         "started_at": "2026-10-07T05:19:02Z",
                     },
                     last_result=result)
    manager.store.canonical.save(canonical)
    manager.store.finish_request(request_id, "failed", result)
    return manager, request_id, previous_identity


def test_rollback_failed_start_terminalizes_the_exact_previous_game(tmp_path):
    manager, request_id, _previous = _failed_rollback_setup(tmp_path)

    assert manager.reconcile_failed_start(request_id) is True

    state = json.loads(manager.path.read_text())
    assert state["status"] == "interrupted"
    assert state["end_reason"] == "switch-terminal-before-corner-active"
    assert state["completed_at"] == 1001.0
    assert state["rotation_request_id"] == request_id
    # The terminal failed receipt is the evidence and is never rewritten.
    assert manager.store.receipts.load(request_id)["status"] == "failed"


def test_rollback_failed_start_stays_latched_when_another_game_owns_the_canonical(tmp_path):
    from docich.game_switch import runtime_names

    manager, request_id, _previous = _failed_rollback_setup(tmp_path)
    canonical, _ = manager.store.canonical.load()
    names = runtime_names(2)
    canonical["active"] = {
        "game": "nethack", "runtime_id": "g2-abcdef", "generation": 2,
        "lease_id": str(uuid.uuid4()), "adapter": "cli",
        "game_window": names.game_window, "agent_window": names.agent_window,
        "adapter_session": names.adapter_session,
        "started_at": "2026-10-07T05:20:00Z",
    }
    canonical["next_generation"] = 3
    manager.store.canonical.save(canonical)

    assert manager.reconcile_failed_start(request_id) is False
    assert json.loads(manager.path.read_text())["status"] == "failed"


def test_rollback_failed_start_stays_latched_when_the_previous_game_generation_regressed(tmp_path):
    manager, request_id, previous = _failed_rollback_setup(tmp_path)
    state = json.loads(manager.path.read_text())
    state["previous_runtime_identity"] = {**previous, "generation": 5}
    manager._save(state)

    assert manager.reconcile_failed_start(request_id) is False
    assert json.loads(manager.path.read_text())["status"] == "failed"


def test_rollback_failed_start_stays_latched_when_the_receipt_error_code_disagrees(tmp_path):
    manager, request_id, _previous = _failed_rollback_setup(tmp_path)
    receipt = manager.store.receipts.load(request_id)
    receipt["result"]["error_code"] = "start_failed"
    manager.store.receipts.save(receipt)

    assert manager.reconcile_failed_start(request_id) is False
    assert json.loads(manager.path.read_text())["status"] == "failed"


def test_rollback_failed_start_stays_latched_while_the_plane_needs_recovery(tmp_path):
    manager, request_id, _previous = _failed_rollback_setup(tmp_path)
    canonical, _ = manager.store.canonical.load()
    canonical.update(phase="failed", operation="switch", request_id=str(uuid.uuid4()),
                     deadline_at=None)
    manager.store.canonical.save(canonical)

    assert manager.reconcile_failed_start(request_id) is False
    assert json.loads(manager.path.read_text())["status"] == "failed"


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

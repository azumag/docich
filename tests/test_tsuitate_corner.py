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
    state.mkdir(parents=True, exist_ok=True)
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


def test_rotation_run_scope_carries_only_beta_control_env(tmp_path, monkeypatch):
    """The match — not just eligibility — runs with the control capability.

    ``TsuitateCornerManager`` calls status/start/stop from this process, and
    ``TsuitateProgramViewAdapter._game_session_env`` reads this same process
    environment while the switch materializes the view's tmux session.
    """
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
    monkeypatch.setenv("DOCICH_WEBUI_TOKEN", "operator-secret-never-export")
    for key in ("DOCICH_BETA_CONTROL_SECRET", "DOCICH_BETA_CONTROL_URL",
                "DOCICH_WEBUI_READONLY_TOKEN"):
        monkeypatch.delenv(key, raising=False)

    adapter = TsuitateCornerAdapter(
        _global(tmp_path), Corner("tsuitate", "tsuitate", VIEW_NAME, manual_only=True)
    )
    captured = {}

    def run_rotation(request_id):
        import os
        captured["request_id"] = request_id
        captured["secret"] = os.environ.get("DOCICH_BETA_CONTROL_SECRET")
        captured["url"] = os.environ.get("DOCICH_BETA_CONTROL_URL")
        captured["webui"] = os.environ.get("DOCICH_WEBUI_TOKEN")
        captured["other"] = os.environ.get("OTHER_PRIVATE_VALUE")
        return "completed"

    adapter.manager.run_rotation = run_rotation
    assert adapter.run({"request_id": REQ}) == "completed"
    assert captured == {
        "request_id": REQ,
        "secret": "fixture-control-secret-12345678901234567890",
        "url": "https://docich-tsuitate-bot.fixture.workers.dev",
        "webui": "operator-secret-never-export",
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


def _runtime(identity, names):
    return {
        **identity,
        "adapter": "cli",
        "game_window": names.game_window,
        "agent_window": names.agent_window,
        "adapter_session": names.adapter_session,
        "started_at": "2026-10-07T05:19:02Z",
    }


def _rolled_back_setup(tmp_path, code=None):
    """Craft the production #2187 shape.

    The start switch exceeded its request-wide deadline, so game-switch ran the
    switch's own rollback and finished the receipt as ``rolled_back`` with
    ``error_code="timeout"`` (``game_switch._failure_code`` maps the deadline
    exception to ``ERROR_TIMEOUT``). The corner never became active and only
    the previous game owns the canonical.
    """
    from docich.game_switch import ERROR_TIMEOUT, runtime_names

    if code is None:
        code = ERROR_TIMEOUT
    g = _global(tmp_path)
    manager = TsuitateCornerManager(g, coordinator=object())
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
        "rotation_request_id": REQ,
        "start_request_id": REQ,
        "switch_request_id": REQ,
        "previous_game": "sorengame",
        "previous_runtime_identity": previous_identity,
        "requested_at": 1000.0,
        "starting_at": 1000.0,
        "completed_at": 1001.0,
        "last_error_code": code,
    })
    names = runtime_names(1)
    canonical = manager.store.initialize()
    canonical.update(phase="ready", next_generation=2,
                     active=_runtime(previous_identity, names))
    manager.store.canonical.save(canonical)
    accepted = manager.store.accept_request(REQ, "switch", VIEW_NAME)
    result = {
        "request_id": REQ,
        "operation": "switch",
        "status": "rolled_back",
        "from_game": "sorengame",
        "to_game": VIEW_NAME,
        "generation": accepted.generation,
        "restored_generation": 1,
        "error_code": code,
        "detail": "request-wide deadline exceeded; rollback restored the previous game",
        "cleanup_pending": False,
    }
    canonical, _ = manager.store.canonical.load()
    canonical.update(phase="ready", operation=None, request_id=None, deadline_at=None,
                     candidate=None, previous=None, retiring=[],
                     next_generation=accepted.generation + 1,
                     active=_runtime(previous_identity, names),
                     last_result=result)
    manager.store.canonical.save(canonical)
    manager.store.finish_request(REQ, "rolled_back", result)
    return manager, REQ, previous_identity


def test_rolled_back_start_timeout_terminalizes_the_previous_game(tmp_path):
    """#2187: a deadline timeout that rolled back must clear the slot.

    Accepting only the four switch-step codes left this exact shape latched
    even though the receipt proved the previous game was restored.
    """
    manager, request_id, _previous = _rolled_back_setup(tmp_path)

    assert manager.reconcile_failed_start(request_id) is True

    state = json.loads(manager.path.read_text())
    assert state["status"] == "interrupted"
    assert state["end_reason"] == "switch-terminal-before-corner-active"
    assert state["completed_at"] == 1001.0
    assert state["rotation_request_id"] == request_id
    # The terminal rolled-back receipt is the evidence and is never rewritten.
    assert manager.store.receipts.load(request_id)["status"] == "rolled_back"


def test_rolled_back_start_stays_latched_for_an_unreviewed_error_code(tmp_path):
    """Widening must stay bounded: only reviewed switch-start codes qualify."""
    manager, request_id, _previous = _rolled_back_setup(tmp_path, code="prepare_failed")

    assert manager.reconcile_failed_start(request_id) is False
    assert json.loads(manager.path.read_text())["status"] == "failed"


def test_rolled_back_start_timeout_stays_latched_when_another_game_owns_the_canonical(tmp_path):
    from docich.game_switch import runtime_names

    manager, request_id, _previous = _rolled_back_setup(tmp_path)
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


def test_rolled_back_start_timeout_stays_latched_while_cleanup_is_pending(tmp_path):
    manager, request_id, _previous = _rolled_back_setup(tmp_path)
    receipt = manager.store.receipts.load(request_id)
    receipt["result"]["cleanup_pending"] = True
    manager.store.receipts.save(receipt)

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


def test_tsuitate_restore_replay_is_a_noop_without_a_recorded_restore(tmp_path):
    g = _global(tmp_path)
    manager = TsuitateCornerManager(g, coordinator=object(), control=lambda *_: _status())

    assert manager.recover_failed_restore() == "noop"

    state, _ = _active_state()
    state["status"] = "completed"
    manager._save(state)

    assert manager.recover_failed_restore() == "noop"


def test_tsuitate_restore_replay_waits_for_an_in_flight_switch(tmp_path):
    # #2199: a recorded failed restore is replayed by the common scheduler, but
    # never on top of another switch that still owns the slot.
    g = _global(tmp_path)
    manager = TsuitateCornerManager(g, coordinator=object(), control=lambda *_: _status())
    state, _identity = _active_state()
    restore_request_id = str(uuid.uuid4())
    state.update(status="restoring", restore_request_id=restore_request_id,
                 switch_request_id=restore_request_id)
    manager._save(state)
    manager._canonical = lambda: {"phase": "starting"}

    assert manager.recover_failed_restore() == "queued"


def _review_fixture():
    """A synthetic public replay in the site's flat ``__data.json`` shape."""
    table = [{"game": 1, "senteName": None, "goteName": None}, None]

    def add(value):
        table.append(value)
        return len(table) - 1

    move_refs = []
    for index in range(50):
        move_refs.append(add({"usi": add("7g7f"), "byColor": add("sente"),
                              "ms": add(300), "foulsBefore": add(0)}))
    game = {
        "id": add("fixtureGame01"), "result": add("gote_win"), "reason": add("foul_limit"),
        "moves": add(move_refs), "foulAttempts": add([]), "senteFouls": add(10),
        "goteFouls": add(0), "senteRatingBefore": add(1005), "senteRatingAfter": add(1003),
        "goteRatingBefore": add(1471), "goteRatingAfter": add(1473),
        "startedAt": add(["Date", "2026-10-14T20:36:48.743Z"]),
        "endedAt": add(["Date", "2026-10-14T20:38:05.153Z"]),
    }
    table[1] = game
    table[0]["senteName"] = add("DoCiAI")
    table[0]["goteName"] = add("kakuren_bot")
    return {"type": "data", "nodes": [{"type": "data", "data": [{"user": 1}, None]},
                                      {"type": "data", "data": table}]}



# --- narration: the corner introduction and the 感想戦 ----------------------

_COUNTS = {"overlay": {"sent": 1, "failed": 0, "skipped": 0},
           "speech": {"sent": 1, "failed": 0, "skipped": 0}}


def test_corner_introduction_is_announced_once_at_activation(tmp_path):
    g = _global(tmp_path)
    state, _identity = _active_state()
    calls = []
    manager = TsuitateCornerManager(
        g, coordinator=object(), control=lambda *_: _status(), audio_enabled=True,
        announce_intro=lambda g, *, request_id, audio_enabled: (
            calls.append((request_id, audio_enabled)) or dict(_COUNTS)),
    )
    manager._save(state)

    manager._announce_intro_once(state)
    manager._announce_intro_once(state)

    assert calls == [(REQ, True)]
    saved = json.loads(manager.path.read_text())
    assert saved["intro_request_id"] == REQ
    assert saved["overlay_announcements"] == {"sent": 1, "failed": 0, "skipped": 0}
    assert saved["narration"] == {"enqueued": 1, "delivery_failed": 0, "skipped": 0}


def test_corner_introduction_is_silent_when_audio_is_off(tmp_path):
    g = _global(tmp_path)
    state, _identity = _active_state()
    manager = TsuitateCornerManager(
        g, coordinator=object(), control=lambda *_: _status(),
        announce_intro=lambda g, *, request_id, audio_enabled: dict(_COUNTS),
    )
    manager._save(state)

    manager._announce_intro_once(state)

    saved = json.loads(manager.path.read_text())
    assert "narration" not in saved
    assert saved["overlay_announcements"]["sent"] == 1


def test_review_is_announced_once_from_the_verified_public_replay(tmp_path):
    g = _global(tmp_path)
    state, _identity = _active_state()
    state.update(
        beta_result=_settled_result(outcome="loss", reason="foul_limit", moveNumber=50),
        beta_game_id="fixtureGame01", beta_color="sente", beta_active_since=1000.0,
    )
    summary = {"gameId": "fixtureGame01", "color": "sente", "outcome": "loss",
               "reason": "foul_limit", "moveCount": 50, "ownMoves": 25,
               "opponentMoves": 25, "fouls": 10, "opponentFouls": 0, "foulAttempts": 10,
               "ratingBefore": 1005, "ratingAfter": 1003, "opponentName": "kakuren_bot",
               "own": {"totalMs": 9169, "maxMs": 1561}, "opponent": {"totalMs": 65641, "maxMs": 3418},
               "promotions": 8, "drops": 13, "lastMove": {"usi": "5g5h", "by": "gote", "ms": 3059}}
    reads, generated, published = [], [], []
    manager = TsuitateCornerManager(
        g, coordinator=object(), control=lambda *_: _status(), audio_enabled=True,
        wait_boundary=lambda g: "quiet",
        read_review=lambda game_id, terminal, *, color: (
            reads.append((game_id, color, terminal.get("durationMs"))) or summary),
        review_text=lambda g, summary, terminal, *, agents: (
            generated.append((summary is not None, terminal.get("durationMs")))
            or ("AIは反則を重ねてしまいましたが、持ち時間の使い方は落ち着いていました。次は反則を減らして勝ちを狙います。", "ai")),
        announce_review=lambda g, text, *, request_id, body, audio_enabled: (
            published.append((request_id, text, body, audio_enabled)) or dict(_COUNTS)),
    )
    manager.clock = lambda: 1076.41
    manager._save(state)

    manager._announce_review_once(state)
    manager._announce_review_once(state)

    assert reads == [("fixtureGame01", "sente", 76410)]
    assert generated == [(True, 76410)]
    assert len(published) == 1 and published[0][0] == REQ
    assert published[0][1].startswith("AIは反則を重ねて")
    assert published[0][2].startswith("AIの負け / 反則負け / 50手")
    saved = json.loads(manager.path.read_text())
    assert saved["review"] == {"source": "replay", "kind": "ai", "failure": "ok",
                               "boundary": "quiet", "moves": 50}
    assert saved["review_request_id"] == REQ
    # The raw move list, the board and the model prompt never reach the state.
    raw = manager.path.read_text()
    for forbidden in ("5g5h", "usi", "sfen", "prompt", "kakuren_bot"):
        assert forbidden not in raw


def test_review_falls_back_to_the_settled_result_without_a_replay(tmp_path):
    g = _global(tmp_path)
    state, _identity = _active_state()
    state.update(beta_result=_settled_result(outcome="win", reason="checkmate"),
                 beta_game_id="fixtureGame01", beta_color="gote", beta_active_since=1000.0)
    published = []

    def broken_reader(*_args, **_kwargs):
        raise RuntimeError("site down")

    manager = TsuitateCornerManager(
        g, coordinator=object(), control=lambda *_: _status(),
        read_review=broken_reader,
        review_text=lambda g, summary, terminal, *, agents: (
            "感想戦です。今回の対局は9手、AIの勝ち、終局理由は詰みでした。"
            "公開棋譜を確認できなかったため、結果だけをお伝えします。", "fallback"),
        announce_review=lambda g, text, *, request_id, body, audio_enabled: (
            published.append(text) or dict(_COUNTS)),
    )
    manager._save(state)

    manager._announce_review_once(state)

    assert published and published[0].startswith("感想戦です。")
    saved = json.loads(manager.path.read_text())
    assert saved["review"]["source"] == "result-only"
    assert saved["review"]["kind"] == "fallback"


def test_review_waits_for_a_settled_owned_result(tmp_path):
    g = _global(tmp_path)
    state, _identity = _active_state()
    published = []
    manager = TsuitateCornerManager(
        g, coordinator=object(), control=lambda *_: _status(),
        read_review=lambda *a, **k: {},
        announce_review=lambda *a, **k: published.append(1) or dict(_COUNTS),
    )
    manager._save(state)

    manager._announce_review_once(state)

    assert published == []
    saved = json.loads(manager.path.read_text())
    assert "review_request_id" not in saved and "review" not in saved


def test_narration_options_are_validated(tmp_path):
    g = _global(tmp_path)
    for kwargs, message in [
        ({"audio_enabled": "yes"}, "audio_enabled"),
        ({"review_agents": 7}, "review_agents"),
        ({"announce_intro": "nope"}, "announce_intro"),
        ({"announce_review": 1}, "announce_review"),
        ({"read_review": object()}, "read_review"),
        ({"review_text": []}, "review_text"),
    ]:
        with pytest.raises(TsuitateCornerError, match=message):
            TsuitateCornerManager(g, coordinator=object(), control=lambda *_: _status(), **kwargs)


def test_announcement_counters_accumulate_over_the_run(tmp_path):
    """A delivered run shows two overlay events and two utterances."""
    g = _global(tmp_path)
    state, _identity = _active_state()
    state.update(beta_result=_settled_result(), beta_game_id="fixtureGame01",
                 beta_color="sente", beta_active_since=1000.0)
    manager = TsuitateCornerManager(
        g, coordinator=object(), control=lambda *_: _status(), audio_enabled=True,
        announce_intro=lambda g, *, request_id, audio_enabled: dict(_COUNTS),
        read_review=lambda *a, **k: None,
        review_text=lambda g, summary, terminal, *, agents: (
            "感想戦です。今回の対局は9手、AIの勝ち、終局理由は詰みでした。"
            "持ち時間の使い方も落ち着いていました。次もお付き合いください。", "fallback"),
        announce_review=lambda g, text, *, request_id, body, audio_enabled: dict(_COUNTS),
    )
    manager._save(state)

    manager._announce_intro_once(state)
    manager._announce_review_once(state)

    saved = json.loads(manager.path.read_text())
    assert saved["overlay_announcements"] == {"sent": 2, "failed": 0, "skipped": 0}
    assert saved["narration"] == {"enqueued": 2, "delivery_failed": 0, "skipped": 0}


def test_announcement_counters_survive_a_failed_sink(tmp_path):
    g = _global(tmp_path)
    state, _identity = _active_state()
    manager = TsuitateCornerManager(
        g, coordinator=object(), control=lambda *_: _status(), audio_enabled=True,
        announce_intro=lambda *a, **k: {"overlay": {"sent": 0, "failed": 1, "skipped": 0},
                                        "speech": {"sent": 0, "failed": 1, "skipped": 0}},
    )
    manager._save(state)

    manager._announce_intro_once(state)

    saved = json.loads(manager.path.read_text())
    assert saved["overlay_announcements"] == {"sent": 0, "failed": 1, "skipped": 0}
    assert saved["narration"] == {"enqueued": 0, "delivery_failed": 1, "skipped": 0}


def test_review_reads_the_public_replay_end_to_end(tmp_path, monkeypatch):
    """Regression: the corner must hand the game id to the identity check.

    The settled-result projection has no `gameId`, so passing it alone made every
    real 感想戦 fall back to the result-only text.  This test runs the real
    reader (only the HTTP fetch is stubbed) so the whole path is exercised.
    """
    from docich import tsuitate_review as review_module

    g = _global(tmp_path)
    state, _identity = _active_state()
    state.update(
        beta_result={"outcome": "loss", "reason": "foul_limit",
                     "endedAt": "2026-10-14T20:38:05.153Z", "moveNumber": 50,
                     "resultConfidence": "verified"},
        beta_game_id="fixtureGame01", beta_color="sente", beta_active_since=1000.0,
    )
    payload = review_module.parse_review(_review_fixture(), "fixtureGame01")
    monkeypatch.setattr(review_module, "read_replay", lambda game_id, **kwargs: payload)
    published = []
    manager = TsuitateCornerManager(
        g, coordinator=object(), control=lambda *_: _status(),
        review_text=lambda g, summary, terminal, *, agents: (
            "感想戦です。今回は50手、反則10回で反則負けでした。"
            "持ち時間の使い方を見直して次につなげます。", "fallback"),
        announce_review=lambda g, text, *, request_id, body, audio_enabled: (
            published.append((text, body)) or dict(_COUNTS)),
    )
    manager._save(state)

    manager._announce_review_once(state)

    saved = json.loads(manager.path.read_text())
    assert saved["review"] == {"source": "replay", "kind": "fallback",
                               "failure": "ok", "boundary": "skipped", "moves": 50}
    assert published and published[0][1].startswith("AIの負け / 反則負け / 50手")


def test_review_records_why_the_public_replay_was_unusable(tmp_path, monkeypatch):
    from docich import tsuitate_review as review_module

    g = _global(tmp_path)
    state, _identity = _active_state()
    state.update(beta_result={"outcome": "loss", "reason": "foul_limit",
                              "endedAt": "2026-10-14T20:38:05.153Z", "moveNumber": 50},
                 beta_game_id="fixtureGame01", beta_color="sente", beta_active_since=1000.0)
    monkeypatch.setattr(review_module, "read_replay",
                        lambda game_id, **kwargs: (_ for _ in ()).throw(
                            review_module.ReviewError("site down")))
    manager = TsuitateCornerManager(
        g, coordinator=object(), control=lambda *_: _status(),
        review_text=lambda g, summary, terminal, *, agents: ("定型の感想戦です。" * 5, "fallback"),
        announce_review=lambda *a, **k: dict(_COUNTS),
    )
    manager._save(state)

    manager._announce_review_once(state)

    saved = json.loads(manager.path.read_text())
    assert saved["review"]["source"] == "result-only"
    assert saved["review"]["failure"] == "read-failed"

    # Without a game id there is nothing to read at all.
    state.pop("review_request_id", None)
    state["beta_game_id"] = None
    manager._save(state)
    manager._announce_review_once(state)
    assert json.loads(manager.path.read_text())["review"]["failure"] == "invalid-game-id"


def _boundary_manager(tmp_path, *, audio_enabled, wait_boundary):
    g = _global(tmp_path)
    state, _identity = _active_state()
    state.update(beta_result=_settled_result(), beta_game_id="fixtureGame01",
                 beta_color="sente", beta_active_since=1000.0)
    published = []
    manager = TsuitateCornerManager(
        g, coordinator=object(), control=lambda *_: _status(), audio_enabled=audio_enabled,
        wait_boundary=wait_boundary,
        read_review=lambda *a, **k: None,
        review_text=lambda g, summary, terminal, *, agents: (
            "感想戦です。今回の対局は9手、AIの勝ち、終局理由は詰みでした。"
            "持ち時間の使い方も落ち着いていました。次もお付き合いください。", "fallback"),
        announce_review=lambda g, text, *, request_id, body, audio_enabled: (
            published.append(request_id) or dict(_COUNTS)),
    )
    manager._save(state)
    return manager, published


def test_review_waits_for_the_audio_boundary_before_speaking(tmp_path):
    calls = []

    def wait(g):
        calls.append(1)
        return "quiet"

    manager, published = _boundary_manager(tmp_path, audio_enabled=True, wait_boundary=wait)
    manager._announce_review_once(manager._read_state())

    assert calls == [1]
    assert published
    assert json.loads(manager.path.read_text())["review"]["boundary"] == "quiet"


def test_review_skips_the_boundary_wait_when_speech_is_off(tmp_path):
    calls = []

    def wait(g):
        calls.append(1)
        return "quiet"

    manager, published = _boundary_manager(tmp_path, audio_enabled=False, wait_boundary=wait)
    manager._announce_review_once(manager._read_state())

    assert calls == []
    assert published
    assert json.loads(manager.path.read_text())["review"]["boundary"] == "skipped"


def test_review_speaks_even_when_the_boundary_wait_fails(tmp_path):
    def broken(g):
        raise RuntimeError("queue unreadable")

    manager, published = _boundary_manager(tmp_path, audio_enabled=True, wait_boundary=broken)
    manager._announce_review_once(manager._read_state())

    assert published  # fail-open: the 感想戦 is never lost to a queue problem
    assert json.loads(manager.path.read_text())["review"]["boundary"] == "unavailable"

    def unexpected(g):
        return "later"

    manager, published = _boundary_manager(tmp_path, audio_enabled=True, wait_boundary=unexpected)
    manager._announce_review_once(manager._read_state())
    assert json.loads(manager.path.read_text())["review"]["boundary"] == "unavailable"


def test_boundary_outcome_vocabulary_matches_the_narration_module():
    from docich import tsuitate_narration as narration

    assert TsuitateCornerManager.BOUNDARY_OUTCOMES == narration.BOUNDARY_OUTCOMES

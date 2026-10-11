"""Regression tests for lifecycle pause provenance in collect_diagnostics (#187).

Uses a fake soren runtime root (tmp_path) so no production state is touched.
Covers: lifecycle-owned, terminal stopped (fresh-launch wait), operator-owned,
expired (stale), mismatched, and no-lifecycle cases.  Diagnostics must stay
read-only: markers and records must survive collection, and request ids, game
names and deadlines must never appear in the emitted JSON.
"""

import importlib.util
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"


def load_collector():
    spec = importlib.util.spec_from_file_location(
        "collect_diagnostics_lifecycle_pause", str(COLLECTOR)
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SECRET_REQUEST_ID = "REQ-DO-NOT-PUBLISH-1"
SECRET_GAME = "SECRET-GAME-DO-NOT-PUBLISH"
SECRET_DEADLINE_AT = "2030-01-01T00:00:00Z-DO-NOT-PUBLISH"


def _write_pair(state_dir, now, ack_status, deadline_offset=600, ack_request_id=None):
    lifecycle = state_dir / "game_lifecycle"
    lifecycle.mkdir(parents=True, exist_ok=True)
    deadline = now + deadline_offset
    request = {
        "schema": 1,
        "request_id": SECRET_REQUEST_ID,
        "game": SECRET_GAME,
        "generation": 7,
        "deadline_epoch": deadline,
        "deadline_at": SECRET_DEADLINE_AT,
    }
    ack = dict(request)
    ack["request_id"] = ack_request_id or SECRET_REQUEST_ID
    ack["status"] = ack_status
    (lifecycle / "request.json").write_text(json.dumps(request))
    (lifecycle / "ack.json").write_text(json.dumps(ack))


def _write_owned_marker(state_dir, name, record_name, owner_field):
    (state_dir / f"{name}.paused").write_text(f"lifecycle:{SECRET_REQUEST_ID}\n")
    (state_dir / "game_lifecycle" / record_name).write_text(
        json.dumps({"request_id": SECRET_REQUEST_ID, owner_field: True})
    )


def _collect(module, soren_root, now):
    workers = module._collect_workers(soren_root, now)
    return workers, module._collect_lifecycle_pause(soren_root, now, workers)


def _assert_no_secrets(payload):
    text = json.dumps(payload, sort_keys=True)
    assert SECRET_REQUEST_ID not in text
    assert SECRET_GAME not in text
    assert SECRET_DEADLINE_AT not in text


def test_lifecycle_owned_pause_reports_handover_wait(tmp_path):
    module = load_collector()
    now = int(time.time())
    state_dir = tmp_path / "tmp" / "state"
    state_dir.mkdir(parents=True)
    _write_pair(state_dir, now, "boundary")
    _write_owned_marker(
        state_dir, "improve_daemon", "improvement_pause.json", "improvement_marker_created"
    )
    _write_owned_marker(state_dir, "soren_loop", "loop_pause.json", "loop_marker_created")

    workers, lifecycle = _collect(module, tmp_path, now)

    assert workers["details"]["improve_daemon"]["pause_owner"] == "lifecycle_owned"
    assert workers["details"]["soren_loop"]["pause_owner"] == "lifecycle_owned"
    assert lifecycle["active"] is True
    assert lifecycle["request_status"] == "present"
    assert lifecycle["ack_status"] == "boundary"
    assert lifecycle["pause_owned_by_lifecycle"] == ["improve_daemon", "soren_loop"]
    assert lifecycle["waiting_for"] == "lifecycle_handover"
    _assert_no_secrets({"workers": workers, "lifecycle_pause": lifecycle})
    # Read-only: markers and records survive collection.
    assert (state_dir / "improve_daemon.paused").is_file()
    assert (state_dir / "soren_loop.paused").is_file()


def test_terminal_stopped_reports_fresh_launch_wait(tmp_path):
    module = load_collector()
    now = int(time.time())
    state_dir = tmp_path / "tmp" / "state"
    state_dir.mkdir(parents=True)
    # Terminal stopped parks without a deadline: even an old deadline stays governed.
    _write_pair(state_dir, now, "stopped", deadline_offset=-7200)
    _write_owned_marker(state_dir, "soren_loop", "loop_pause.json", "loop_marker_created")

    workers, lifecycle = _collect(module, tmp_path, now)

    assert workers["details"]["soren_loop"]["pause_owner"] == "lifecycle_owned"
    assert lifecycle["active"] is True
    assert lifecycle["ack_status"] == "stopped"
    assert lifecycle["pause_owned_by_lifecycle"] == ["soren_loop"]
    assert lifecycle["waiting_for"] == "fresh_launch"
    _assert_no_secrets({"workers": workers, "lifecycle_pause": lifecycle})


def test_operator_owned_pause_reports_operator_action(tmp_path):
    module = load_collector()
    now = int(time.time())
    state_dir = tmp_path / "tmp" / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "improve_daemon.paused").write_text(
        json.dumps({"paused": True, "source": "webui"})
    )

    workers, lifecycle = _collect(module, tmp_path, now)

    assert workers["details"]["improve_daemon"]["pause_owner"] == "operator_owned"
    assert lifecycle["active"] is False
    assert lifecycle["request_status"] == "absent"
    assert lifecycle["ack_status"] == "absent"
    assert lifecycle["pause_owned_by_lifecycle"] == []
    assert lifecycle["waiting_for"] == "operator_action"


def test_expired_lifecycle_pause_is_stale_unknown(tmp_path):
    module = load_collector()
    now = int(time.time())
    state_dir = tmp_path / "tmp" / "state"
    state_dir.mkdir(parents=True)
    _write_pair(state_dir, now, "boundary", deadline_offset=-600)
    _write_owned_marker(state_dir, "soren_loop", "loop_pause.json", "loop_marker_created")

    workers, lifecycle = _collect(module, tmp_path, now)

    # The marker/record match, but the handover deadline passed: not governed.
    assert workers["details"]["soren_loop"]["pause_owner"] == "unknown"
    assert lifecycle["active"] is False
    assert lifecycle["request_status"] == "present"
    assert lifecycle["ack_status"] == "boundary"
    assert lifecycle["pause_owned_by_lifecycle"] == []
    assert lifecycle["waiting_for"] == "unknown"
    _assert_no_secrets({"workers": workers, "lifecycle_pause": lifecycle})


def test_mismatched_ack_is_not_active(tmp_path):
    module = load_collector()
    now = int(time.time())
    state_dir = tmp_path / "tmp" / "state"
    state_dir.mkdir(parents=True)
    _write_pair(state_dir, now, "boundary", ack_request_id="REQ-OTHER-GENERATION")
    _write_owned_marker(state_dir, "soren_loop", "loop_pause.json", "loop_marker_created")

    workers, lifecycle = _collect(module, tmp_path, now)

    assert workers["details"]["soren_loop"]["pause_owner"] == "unknown"
    assert lifecycle["active"] is False
    assert lifecycle["request_status"] == "present"
    assert lifecycle["ack_status"] == "mismatched"
    assert lifecycle["pause_owned_by_lifecycle"] == []
    assert lifecycle["waiting_for"] == "unknown"
    _assert_no_secrets({"workers": workers, "lifecycle_pause": lifecycle})


def test_no_lifecycle_no_pause_reports_none(tmp_path):
    module = load_collector()
    now = int(time.time())
    (tmp_path / "tmp" / "state").mkdir(parents=True)

    workers, lifecycle = _collect(module, tmp_path, now)

    assert lifecycle == {
        "active": False,
        "request_status": "absent",
        "ack_status": "absent",
        "pause_owned_by_lifecycle": [],
        "waiting_for": "none",
    }
    assert set(lifecycle) == {
        "active",
        "request_status",
        "ack_status",
        "pause_owned_by_lifecycle",
        "waiting_for",
    }

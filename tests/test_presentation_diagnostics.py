"""#2097: the active runtime's projector state is visible, bounded, read-only."""

import importlib.util
import json
import os
import shutil
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"


def load_collector():
    spec = importlib.util.spec_from_file_location("collect_diagnostics", str(COLLECTOR))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _state_dir(tmp_path: Path, runtime_id: str = "g7-abcdef12") -> Path:
    (tmp_path / "runtimes" / runtime_id).mkdir(parents=True)
    (tmp_path / "game_switch.json").write_text(json.dumps({
        "phase": "ready",
        "active": {"game": "hanjuku-hero", "runtime_id": runtime_id, "generation": 7},
    }))
    return tmp_path


def test_active_runtime_presentation_is_projected_without_pids(tmp_path):
    module = load_collector()
    state_dir = _state_dir(tmp_path)
    record = state_dir / "runtimes" / "g7-abcdef12" / "presentation.json"
    record.write_text(json.dumps({
        "status": "ready", "display": ":1", "window": "2097160", "width": 299, "height": 224,
        "groups": [4242, 4243, 4244], "secret": "DO-NOT-PUBLISH"}))
    (state_dir / "runtimes" / "g7-abcdef12").mkdir(exist_ok=True)

    output = module._collect_runtime_presentation(state_dir, time.time())

    assert output["present"] is True and output["readable"] is True
    assert output["active_runtime"] is True
    assert output["status"] == "ready" and output["display"] == ":1"
    assert output["window"] == "2097160"
    assert output["width"] == 299 and output["height"] == 224
    assert output["age_sec"] is not None and output["age_sec"] >= 0
    assert "DO-NOT-PUBLISH" not in json.dumps(output)
    assert "4242" not in json.dumps(output)


def test_failed_projection_is_reported_for_the_active_runtime(tmp_path):
    module = load_collector()
    state_dir = _state_dir(tmp_path)
    (state_dir / "runtimes" / "g7-abcdef12" / "presentation.json").write_text(json.dumps({
        "status": "presentation_failed", "display": ":99", "window": "11",
        "width": 960, "height": 540, "groups": [1, 2]}))

    output = module._collect_runtime_presentation(state_dir, time.time())

    assert output["status"] == "presentation_failed"
    assert output["display"] == ":99"


def test_unknown_status_and_hostile_values_are_dropped(tmp_path):
    module = load_collector()
    state_dir = _state_dir(tmp_path)
    (state_dir / "runtimes" / "g7-abcdef12" / "presentation.json").write_text(json.dumps({
        "status": "root-shell", "display": "; rm -rf /", "window": "../../etc/passwd",
        "width": -1, "height": 99999}))

    output = module._collect_runtime_presentation(state_dir, time.time())

    assert output["status"] is None and output["display"] is None
    assert output["window"] is None and output["width"] is None and output["height"] is None


def test_without_an_active_runtime_nothing_is_read(tmp_path):
    module = load_collector()
    (tmp_path / "game_switch.json").write_text(json.dumps({"phase": "ready", "active": None}))
    (tmp_path / "runtimes" / "g7-abcdef12").mkdir(parents=True)
    (tmp_path / "runtimes" / "g7-abcdef12" / "presentation.json").write_text(
        json.dumps({"status": "ready"}))

    output = module._collect_runtime_presentation(tmp_path, time.time())

    assert output["present"] is False and output["active_runtime"] is False


def test_a_stale_runtime_record_is_never_reported_as_the_active_one(tmp_path):
    module = load_collector()
    state_dir = _state_dir(tmp_path, runtime_id="g8-newer")
    (state_dir / "runtimes" / "g7-abcdef12").mkdir(parents=True)
    (state_dir / "runtimes" / "g7-abcdef12" / "presentation.json").write_text(
        json.dumps({"status": "ready", "display": ":1", "window": "11"}))

    output = module._collect_runtime_presentation(state_dir, time.time())

    assert output["present"] is False, "only the active runtime's record is read"
    assert output["active_runtime"] is True









def test_symlinked_runtime_directory_is_never_descended(tmp_path):
    # A runtime_id entry that is itself a symlink must not be followed: the
    # glob this replaced reached outside the runtimes tree through it, so a
    # planted file fed the projected `reason` field (#2768).
    module = load_collector()
    state_dir = _state_dir(tmp_path, runtime_id="g9-aaaa1111")
    runtime_dir = state_dir / "runtimes" / "g9-aaaa1111"
    shutil.rmtree(runtime_dir)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "presentation_failure.json").write_text(json.dumps({
        "status": "viewer_timeout",
        "reason": "OUTSIDE-PLANTED-REASON-0123456789",
        "viewer_wait_sec": 7,
    }))
    runtime_dir.symlink_to(outside, target_is_directory=True)

    output = module._project_soren91_corner({}, state_dir=state_dir)

    dumped = json.dumps(output)
    assert "OUTSIDE-PLANTED-REASON-0123456789" not in dumped
    failure = output.get("presentation_failure") or {}
    assert failure.get("status") != "viewer_timeout"
    assert failure.get("reason") is None


# --- #2768: presentation_failure projected from the real runtimes layout ----
# Writer layout: src/docich/adapters/soren91.py (--failure-state) writes
# <state_dir>/runtimes/<runtime_id>/presentation_failure.json. These fixtures
# mirror that production layout and pin issue #2768's acceptance contract:
# only the fixed five keys may surface, unknown or mistyped values become
# null, and neither paths nor request ids ever leak into the projection.

_REQUEST_ID_TOKEN = "70eb5f95-DO-NOT-PUBLISH"

_FAILURE_KEYS = {
    "status", "reason", "viewer_wait_sec", "elapsed_s", "viewer_exit_code",
}

_VIEWER_TIMEOUT_FAILURE = {
    "status": "viewer_timeout",
    "reason": "no first frame within 240s",
    "viewer_wait_sec": 240,
    "elapsed_s": 240.089,
    "viewer_exit_code": None,
}


def _write_failure(state_dir, runtime_id="g7-abcdef12", payload=None, text=None):
    runtime_dir = state_dir / "runtimes" / runtime_id
    runtime_dir.mkdir(parents=True, exist_ok=True)
    target = runtime_dir / "presentation_failure.json"
    if text is not None:
        target.write_text(text, encoding="utf-8")
    else:
        target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def _assert_fixed_projection(output, tmp_path, *needles):
    # Acceptance 2: fixed keys only, flat aliases agree with the nested
    # values, and no path or request id leaks into the projection.
    failure = output["presentation_failure"]
    assert set(failure) == _FAILURE_KEYS
    assert output["presentation_failure_status"] == failure["status"]
    assert output["presentation_failure_reason"] == failure["reason"]
    assert output["presentation_failure_viewer_wait_sec"] == failure["viewer_wait_sec"]
    assert output["presentation_failure_elapsed_s"] == failure["elapsed_s"]
    assert output["presentation_failure_viewer_exit_code"] == failure["viewer_exit_code"]
    dumped = json.dumps(output)
    assert str(tmp_path) not in dumped
    assert _REQUEST_ID_TOKEN not in dumped
    for needle in needles:
        assert needle not in dumped


def test_production_layout_viewer_timeout_is_projected(tmp_path):
    # Acceptance 1: the real writer layout makes the last switch failure
    # readable in the read-only diagnostics (issue #2768).
    module = load_collector()
    state_dir = _state_dir(tmp_path)
    _write_failure(state_dir, payload=dict(_VIEWER_TIMEOUT_FAILURE,
                                          request_id=_REQUEST_ID_TOKEN))

    output = module._project_soren91_corner({}, state_dir=state_dir)

    _assert_fixed_projection(output, tmp_path)
    assert output["presentation_failure"] == {
        "status": "viewer_timeout",
        "reason": "no first frame within 240s",
        "viewer_wait_sec": 240,
        "elapsed_s": 240.089,
        "viewer_exit_code": None,
    }


def test_missing_failure_record_projects_nulls(tmp_path):
    module = load_collector()
    state_dir = _state_dir(tmp_path)

    output = module._project_soren91_corner({}, state_dir=state_dir)

    _assert_fixed_projection(output, tmp_path)
    assert output["presentation_failure"] == {key: None for key in _FAILURE_KEYS}


def test_broken_json_failure_record_is_ignored(tmp_path):
    module = load_collector()
    state_dir = _state_dir(tmp_path)
    _write_failure(state_dir, text="{not json at all")

    output = module._project_soren91_corner({}, state_dir=state_dir)

    _assert_fixed_projection(output, tmp_path)
    assert output["presentation_failure"] == {key: None for key in _FAILURE_KEYS}


def test_oversized_failure_record_is_rejected(tmp_path):
    module = load_collector()
    state_dir = _state_dir(tmp_path)
    target = _write_failure(state_dir, payload=dict(
        _VIEWER_TIMEOUT_FAILURE, reason="OVERSIZE-4K-PAD " + "x" * 5000))
    assert target.stat().st_size > 4096

    output = module._project_soren91_corner({}, state_dir=state_dir)

    _assert_fixed_projection(output, tmp_path, "OVERSIZE-4K-PAD")
    assert output["presentation_failure"] == {key: None for key in _FAILURE_KEYS}


def test_symlinked_failure_file_is_refused(tmp_path):
    # Companion to test_symlinked_runtime_directory_is_never_descended: the
    # candidate file itself must not be followed when it is a symlink.
    module = load_collector()
    state_dir = _state_dir(tmp_path)
    planted = tmp_path / "planted-failure.json"
    planted.write_text(json.dumps(dict(
        _VIEWER_TIMEOUT_FAILURE,
        reason="PLANTED-OUTSIDE-REASON-987654321",
        viewer_wait_sec=19)), encoding="utf-8")
    (state_dir / "runtimes" / "g7-abcdef12" / "presentation_failure.json").symlink_to(planted)

    output = module._project_soren91_corner({}, state_dir=state_dir)

    _assert_fixed_projection(output, tmp_path, "PLANTED-OUTSIDE-REASON-987654321")
    assert output["presentation_failure"] == {key: None for key in _FAILURE_KEYS}


def test_unknown_status_is_dropped_but_valid_keys_survive(tmp_path):
    module = load_collector()
    state_dir = _state_dir(tmp_path)
    _write_failure(state_dir, payload={
        "status": "root-shell",
        "reason": "bounded reason",
        "viewer_wait_sec": 30,
        "elapsed_s": 12.5,
        "viewer_exit_code": None,
    })

    output = module._project_soren91_corner({}, state_dir=state_dir)

    _assert_fixed_projection(output, tmp_path)
    assert output["presentation_failure"] == {
        "status": None,
        "reason": "bounded reason",
        "viewer_wait_sec": 30,
        "elapsed_s": 12.5,
        "viewer_exit_code": None,
    }


def test_mistyped_values_are_nullified_key_by_key(tmp_path):
    module = load_collector()
    state_dir = _state_dir(tmp_path)
    expected = {
        "status": "viewer_timeout",
        "reason": "bounded reason",
        "viewer_wait_sec": 30,
        "elapsed_s": 12.5,
        "viewer_exit_code": 3,
    }
    overrides = [
        ({"status": 123}, {"status": None}),
        ({"status": ["viewer_timeout"]}, {"status": None}),
        ({"viewer_wait_sec": "240"}, {"viewer_wait_sec": None}),
        ({"viewer_wait_sec": True}, {"viewer_wait_sec": None}),
        ({"viewer_wait_sec": 0}, {"viewer_wait_sec": None}),
        ({"reason": {"not": "a string"}}, {"reason": None}),
        ({"viewer_exit_code": True}, {"viewer_exit_code": None}),
        ({"elapsed_s": True}, {"elapsed_s": None}),
        ({"elapsed_s": float("nan")}, {"elapsed_s": None}),
        ({"elapsed_s": float("inf")}, {"elapsed_s": None}),
        ({"viewer_exit_code": -1}, {"viewer_exit_code": -1}),
    ]
    for payload_override, expected_override in overrides:
        _write_failure(state_dir, payload=dict(expected, **payload_override))

        output = module._project_soren91_corner({}, state_dir=state_dir)

        _assert_fixed_projection(output, tmp_path)
        assert output["presentation_failure"] == dict(expected, **expected_override)


def test_newest_runtime_failure_wins_by_mtime(tmp_path):
    module = load_collector()
    state_dir = _state_dir(tmp_path, runtime_id="g7-older")
    old = _write_failure(state_dir, runtime_id="g7-older", payload=dict(
        _VIEWER_TIMEOUT_FAILURE,
        status="viewer_exited",
        reason="viewer died mid-run",
        viewer_wait_sec=30,
        elapsed_s=30.25,
        viewer_exit_code=3,
        request_id=_REQUEST_ID_TOKEN))
    new = _write_failure(state_dir, runtime_id="g8-newer",
                         payload=dict(_VIEWER_TIMEOUT_FAILURE))
    stamp = time.time()
    os.utime(old, (stamp - 7200, stamp - 7200))
    os.utime(new, (stamp - 3600, stamp - 3600))

    output = module._project_soren91_corner({}, state_dir=state_dir)

    _assert_fixed_projection(output, tmp_path, "viewer died mid-run")
    assert output["presentation_failure"] == {
        "status": "viewer_timeout",
        "reason": "no first frame within 240s",
        "viewer_wait_sec": 240,
        "elapsed_s": 240.089,
        "viewer_exit_code": None,
    }


def test_presentation_failure_is_visible_through_collect_programs(tmp_path):
    # Acceptance 1 through the real collector wiring: the read-only
    # diagnostics payload exposes the projected status from the production
    # layout, and neither the state dir path nor a planted request id leaks.
    module = load_collector()
    state_dir = tmp_path / "run"
    state_dir.mkdir()
    (state_dir / "soren91_corner.json").write_text(json.dumps({
        "status": "active",
        "game": "soren91",
        "switch_request_id": _REQUEST_ID_TOKEN,
    }), encoding="utf-8")
    _write_failure(state_dir, payload=dict(_VIEWER_TIMEOUT_FAILURE,
                                          request_id=_REQUEST_ID_TOKEN))

    payload = module._collect_programs(state_dir, tmp_path / "soren", int(time.time()))

    entry = payload["soren91_corner"]
    assert entry["present"] is True and entry["readable"] is True
    _assert_fixed_projection(entry, tmp_path)
    assert entry["presentation_failure"] == {
        "status": "viewer_timeout",
        "reason": "no first frame within 240s",
        "viewer_wait_sec": 240,
        "elapsed_s": 240.089,
        "viewer_exit_code": None,
    }
    assert entry["presentation_failure_status"] == "viewer_timeout"
    dumped = json.dumps(payload)
    assert _REQUEST_ID_TOKEN not in dumped
    assert str(tmp_path) not in dumped

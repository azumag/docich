"""#2097: the active runtime's projector state is visible, bounded, read-only."""

import importlib.util
import json
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

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIAG_PATH = ROOT / "ops" / "vm_actions" / "collect_diagnostics.py"
spec = importlib.util.spec_from_file_location("collect_diagnostics_moon_buggy_test", DIAG_PATH)
collect_diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collect_diagnostics)


def test_moon_buggy_ab_diagnostics_expose_only_sanitized_score_summary(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "moon_buggy_ab.json").write_text(json.dumps({
        "status": "completed",
        "results": [{}, {}, {}],
        "winner": "B",
        "means": {"A": 10.5, "B": 20.0},
        "baseline": {"laser_period": 7.0},
        "candidate": {"laser_period": 8.0},
        "baseline_sha256": "do-not-project-this-hash",
        "candidate_sha256": "do-not-project-this-hash-either",
    }))

    projection = collect_diagnostics._collect_rotation_evidence(state_dir)["moon_buggy_ab"]

    assert projection == {
        "present": True,
        "readable": True,
        "status": "completed",
        "matches": 3,
        "target_matches": 4,
        "winner": "B",
        "baseline_mean": 10.5,
        "candidate_mean": 20.0,
    }
    assert "do-not-project" not in json.dumps(projection)
    assert "laser_period" not in json.dumps(projection)

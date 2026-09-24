import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich import moon_buggy_ab as ab  # noqa: E402


BASELINE = {"laser_period": 7.0}
CANDIDATE = {"laser_period": 8.0}


def _stage(state_dir):
    return ab.stage(
        state_dir,
        BASELINE,
        CANDIDATE,
        source_date="2026-09-24",
        headless_baseline_mean=100.0,
        headless_candidate_mean=10.0,
    )


def _record_block(state_dir, scorelog, scores):
    selected = []
    for score in scores:
        arm = ab.select_arm(state_dir)
        selected.append(arm)
        ab.record_score(state_dir, scorelog, score)
    return selected


def test_lower_headless_candidate_is_staged_and_live_abba_can_promote_it(tmp_path):
    state_dir = tmp_path / "run"
    scorelog = state_dir / "scores" / "moon-buggy.jsonl"
    staged = _stage(state_dir)
    assert staged["status"] == "staged"
    assert staged["headless_candidate_mean"] < staged["headless_baseline_mean"]
    assert ab.pending_matches(state_dir) == 4

    selected = _record_block(state_dir, scorelog, [10, 100, 90, 20])
    assert [item["arm"] for item in selected] == list("ABBA")
    assert [item["weights_sha256"] for item in selected] == [
        staged["baseline_sha256"], staged["candidate_sha256"],
        staged["candidate_sha256"], staged["baseline_sha256"],
    ]

    completed = ab.read_experiment(state_dir)
    assert completed["status"] == "completed"
    assert completed["means"] == {"A": 15.0, "B": 95.0}
    assert completed["winner"] == "B"
    rows = [json.loads(line) for line in scorelog.read_text().splitlines()]
    assert [(row["ab_arm"], row["weights_sha256"]) for row in rows] == [
        (item["arm"], item["weights_sha256"]) for item in selected
    ]
    assert all(row["ab_experiment_id"] == staged["experiment_id"] for row in rows)

    resolved = ab.finish(state_dir, status="promoted")
    assert resolved["status"] == "promoted"
    assert "candidate" not in resolved


def test_tied_scores_keep_the_incumbent(tmp_path):
    state_dir = tmp_path / "run"
    _stage(state_dir)
    _record_block(state_dir, state_dir / "scores.jsonl", [12, 12, 12, 12])
    completed = ab.read_experiment(state_dir)
    assert completed["means"] == {"A": 12.0, "B": 12.0}
    assert completed["winner"] == "A"


def test_incomplete_block_resumes_at_next_unrecorded_arm(tmp_path):
    state_dir = tmp_path / "run"
    scorelog = state_dir / "scores.jsonl"
    _stage(state_dir)
    first = ab.select_arm(state_dir)
    assert (first["index"], first["arm"]) == (0, "A")
    ab.record_score(state_dir, scorelog, 31)
    assert ab.pending_matches(state_dir) == 3

    resumed = ab.select_arm(state_dir)
    assert (resumed["index"], resumed["arm"]) == (1, "B")
    assert resumed["match_id"].endswith(":1")
    assert ab.read_experiment(state_dir)["status"] == "running"


def test_score_record_retry_after_active_snapshot_unlink_is_idempotent(tmp_path):
    state_dir = tmp_path / "run"
    scorelog = state_dir / "scores.jsonl"
    _stage(state_dir)
    ab.select_arm(state_dir)
    first = ab.record_score(state_dir, scorelog, 44)
    retry = ab.record_score(state_dir, scorelog, 44)
    assert len(first["results"]) == len(retry["results"]) == 1
    assert len(scorelog.read_text().splitlines()) == 1


def test_malformed_active_index_and_boolean_result_index_are_rejected(tmp_path):
    state_dir = tmp_path / "run"
    _stage(state_dir)
    ab.select_arm(state_dir)
    active = ab.active_path(state_dir)
    snapshot = json.loads(active.read_text())
    snapshot["index"] = 4
    snapshot["match_id"] = f"{snapshot['experiment_id']}:4"
    active.write_text(json.dumps(snapshot))
    with pytest.raises(ab.MoonBuggyABError):
        ab.record_score(state_dir, state_dir / "scores.jsonl", 1)

    active.unlink()
    ab.select_arm(state_dir)
    ab.record_score(state_dir, state_dir / "scores.jsonl", 1)
    state = ab.state_path(state_dir)
    value = json.loads(state.read_text())
    value["results"][0]["index"] = False
    state.write_text(json.dumps(value))
    with pytest.raises(ab.MoonBuggyABError):
        ab.read_experiment(state_dir)

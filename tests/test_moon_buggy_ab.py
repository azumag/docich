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


def test_live_abba_winner_is_automatically_promoted(tmp_path, monkeypatch):
    from docich import corner_improve

    state_dir = tmp_path / "run"
    scorelog = state_dir / "scores" / "moon-buggy.jsonl"
    monkeypatch.setenv("DOCICH_BOT_BRAIN_DIR", str(tmp_path / "brain"))
    original_promote = corner_improve._promote

    def verify_scorelog_not_yet_complete(*args):
        assert len(scorelog.read_text().splitlines()) == 3
        return original_promote(*args)

    monkeypatch.setattr(corner_improve, "_promote", verify_scorelog_not_yet_complete)
    staged = _stage(state_dir)
    assert staged["status"] == "staged"
    assert staged["headless_candidate_mean"] < staged["headless_baseline_mean"]
    assert ab.pending_matches(state_dir) == 4

    # Final adoption is independent of the shared LLM/evaluation lane.
    with corner_improve.improve_lane(state_dir, timeout=0) as lane:
        assert lane
        selected = _record_block(state_dir, scorelog, [10, 100, 90, 20])
    assert [item["arm"] for item in selected] == list("ABBA")
    assert [item["weights_sha256"] for item in selected] == [
        staged["baseline_sha256"], staged["candidate_sha256"],
        staged["candidate_sha256"], staged["baseline_sha256"],
    ]

    promoted = ab.read_experiment(state_dir)
    assert promoted["status"] == "promoted"
    assert promoted["means"] == {"A": 15.0, "B": 95.0}
    assert promoted["winner"] == "B"
    rows = [json.loads(line) for line in scorelog.read_text().splitlines()]
    assert [(row["ab_arm"], row["weights_sha256"]) for row in rows] == [
        (item["arm"], item["weights_sha256"]) for item in selected
    ]
    assert all(row["ab_experiment_id"] == staged["experiment_id"] for row in rows)
    assert json.loads(
        (tmp_path / "brain" / "moon-buggy" / "weights.json").read_text()
    ) == CANDIDATE
    retry = ab.record_score(state_dir, scorelog, 20)
    assert retry["status"] == "promoted"
    assert len(scorelog.read_text().splitlines()) == 4


def test_tied_scores_keep_the_incumbent(tmp_path):
    state_dir = tmp_path / "run"
    _stage(state_dir)
    _record_block(state_dir, state_dir / "scores.jsonl", [12, 12, 12, 12])
    resolved = ab.read_experiment(state_dir)
    assert resolved["status"] == "kept"
    assert resolved["means"] == {"A": 12.0, "B": 12.0}
    assert resolved["winner"] == "A"


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

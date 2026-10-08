"""The ``python -m docich.eval`` CLI: offline, sealed-locked and file-free (#1308)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.eval import __main__ as cli  # noqa: E402
from docich.eval import candidates, runner  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "evals/comment/v1"


def test_build_prints_manifest_without_sealed_case_ids(capsys):
    assert cli.main(["build", "--suite", str(SUITE)]) == 0
    out = json.loads(capsys.readouterr().out)
    split = out["manifest"]["split"]
    assert split["counts"]["sealed_test"] >= 1
    assert "case_ids" not in split
    assert out["manifest"]["split"]["split_manifest_hash"].startswith("sha256:")


def test_run_heuristic_baseline_prints_a_report(capsys):
    assert cli.main(["run", "--suite", str(SUITE), "--candidate", "heuristic",
                     "--split", "validation"]) == 0
    out = capsys.readouterr().out
    assert "category" in out and "hard fails" in out


def test_sealed_split_is_refused_without_the_flag(capsys):
    assert cli.main(["run", "--suite", str(SUITE), "--candidate", "heuristic",
                     "--split", "sealed_test"]) == 1
    assert "sealed_test_locked" in capsys.readouterr().err


def test_unknown_candidate_spec_is_rejected(capsys):
    assert cli.main(["run", "--suite", str(SUITE), "--candidate", "nope"]) == 1
    assert "unknown_candidate" in capsys.readouterr().err


def test_compare_two_jsonl_candidates(tmp_path, capsys):
    from docich.eval import load_suite
    suite = load_suite(SUITE)
    cases = suite["by_split"]["validation"]
    rows = [{"case_id": case["case_id"], "response": None, "side_effects": 0,
             "category": "chitchat", "screen_need": "not_required",
             "scores": {"quality": 0.5 if index % 2 else 0.9}}
            for index, case in enumerate(cases)]
    base = tmp_path / "base.jsonl"
    base.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    cand = tmp_path / "cand.jsonl"
    cand.write_text("".join(json.dumps({**row, "scores": {"quality": 0.9}}) + "\n"
                            for row in rows), encoding="utf-8")
    assert cli.main(["compare", "--suite", str(SUITE), "--base-jsonl", str(base),
                     "--candidate-jsonl", str(cand), "--split", "validation"]) == 0
    assert "comparison" in capsys.readouterr().out


def test_hillclimb_dry_run_and_report(tmp_path, capsys):
    experiment = tmp_path / "campaign.jsonl"
    assert cli.main(["hillclimb", "--suite", str(SUITE), "--target", "comment-prompt",
                     "--rounds", "3", "--experiment", str(experiment)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True and payload["report"]["rounds"] == 0
    assert cli.main(["report", "--campaign", str(experiment)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["rounds"] == 0


def test_round_trip_capture_outputs_is_replayable(tmp_path):
    from docich.eval import load_suite
    suite = load_suite(SUITE)
    cases = suite["by_split"]["validation"]
    run = runner.run_cases(cases, candidates.heuristic_candidate(), candidate_name="heuristic")
    path = tmp_path / "out.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in candidates.capture_outputs(run.results)),
                    encoding="utf-8")
    replay = runner.run_cases(cases, candidates.jsonl_candidate(path), candidate_name="replay")
    assert [r.output.get("category") for r in replay.results] == \
        [r.output.get("category") for r in run.results]

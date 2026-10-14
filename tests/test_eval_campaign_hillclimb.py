"""Campaign decisions, experiment records and the offline hillclimb loop (#1308)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.eval import campaign, contracts, hillclimb, runner  # noqa: E402

MUTATION_PATH = "src/docich/comment/prompts/comment_response_game.md"


def test_decide_gates_on_hard_failures_before_quality():
    verdict = campaign.decide(hard_fail_regressions=["unsupported_screen_claim"],
                              primary={"n": 5, "mean": 0.5, "lo": 0.4, "hi": 0.6})
    assert verdict == {"decision": "revert", "reason": "hard_gate",
                       "detail": {"hard_fail_regressions": ["unsupported_screen_claim"],
                                  "critical_regressions": []}}


@pytest.mark.parametrize("primary,reason", [
    ({"n": 0, "mean": None, "lo": None, "hi": None}, "no_paired_data"),
    ({"n": 4, "mean": -0.1, "lo": -0.2, "hi": 0.0}, "primary_regression"),
    ({"n": 4, "mean": 0.05, "lo": -0.05, "hi": 0.15}, "interval_crosses_zero"),
])
def test_decide_reverts_or_stays_inconclusive(primary, reason):
    assert campaign.decide(primary=primary)["reason"] == reason


def test_decide_keeps_only_positive_interval_and_blocks_secondary_regression():
    keep = campaign.decide(primary={"n": 6, "mean": 0.2, "lo": 0.05, "hi": 0.35})
    assert keep["decision"] == "keep"
    blocked = campaign.decide(primary={"n": 6, "mean": 0.2, "lo": 0.05, "hi": 0.35},
                              latency_regression=True)
    assert blocked == {"decision": "revert", "reason": "secondary_objective_regression",
                       "detail": {"n": 6, "mean": 0.2, "lo": 0.05, "hi": 0.35,
                                  "cost_regression": False, "latency_regression": True}}


def test_record_roundtrip_and_campaign_report(tmp_path):
    with pytest.raises(contracts.ContractError):
        campaign.make_record(campaign_id="c1", round=1, decision="keep")
    record = campaign.make_record(
        campaign_id="c1", round=1, suite="comment-v1", suite_version="v1",
        rubric_version="reply-semantic-v1", split_manifest_hash="sha256:x",
        mutation_target="comment-prompt", hypothesis="h", diff_hash="d", decision="keep",
        decision_reason="primary_improvement")
    path = tmp_path / "experiments.jsonl"
    campaign.append_experiment(path, record)
    campaign.append_experiment(path, {**record, "round": 2, "decision": "revert"})
    rows = campaign.read_experiments(path)
    summary = campaign.campaign_report(rows)
    assert summary["rounds"] == 2 and summary["decisions"]["keep"] == 1
    assert summary["kept"][0]["round"] == 1


def make_case(case_id, group, split, comment=None):
    case = contracts.validate_case({
        "case_id": case_id, "group_id": group,
        "input": {"comment": comment or f"コメント {case_id} です"},
        "expected": {"category": "chitchat", "screen_need": "not_required"}, "tags": []})
    return {**case, "split": split}


def suite_split(comment="コメント t1 です"):
    train = [make_case("t1", "tg1", "train", comment), make_case("t2", "tg2", "train")]
    validation = [make_case("v1", "vg1", "validation"), make_case("v2", "vg2", "validation")]
    sealed = [make_case("s1", "sg1", "sealed_test")]
    return {"train": train, "validation": validation, "sealed_test": sealed}


def test_validate_mutation_rejects_arbitrary_targets_and_paths():
    with pytest.raises(contracts.ContractError):
        hillclimb.assert_target("arbitrary-file")
    with pytest.raises(contracts.ContractError):
        hillclimb.validate_mutation("comment-prompt", {"path": "/etc/passwd", "text": "x"})
    with pytest.raises(contracts.ContractError):
        hillclimb.validate_mutation("comment-prompt", {"path": "../prompts/x.md", "text": "x"})
    ok = hillclimb.validate_mutation("model-profile", {"value": "medium"})
    assert ok["kind"] == "enum"


def test_hillclimb_loop_is_train_only_and_hides_sealed():
    cases = suite_split()
    calls, generated = [], []
    state = {"n": 0}

    def evaluate(selected):
        calls.append([case["case_id"] for case in selected])
        state["n"] += 1
        quality = 0.4 if state["n"] == 1 else 0.7

        def candidate(case):
            return {"response": None, "side_effects": 0, "category": "chitchat",
                    "screen_need": "not_required", "scores": {"quality": quality}}
        return runner.run_cases(selected, candidate, suite="comment-v1", candidate_name="cand")

    def generate(round_index, train_cases):
        generated.append([case["case_id"] for case in train_cases])
        return {"hypothesis": f"hyp-{round_index}",
                "mutation": {"path": MUTATION_PATH,
                             "text": f"一般ルールを明確化する 第{round_index}版",
                             "diff_hash": f"d{round_index}"}}

    sealed_calls = []
    result = hillclimb.run_campaign(
        campaign_id="c1", target="comment-prompt", cases_by_split=cases,
        split_manifest_hash="sha256:x", generate=generate, evaluate=evaluate, rounds=2,
        dry_run=False, experiment_path=None,
        sealed_evaluate=lambda sealed: sealed_calls.append([c["case_id"] for c in sealed]))

    assert generated == [["t1", "t2"], ["t1", "t2"]]  # train only, every round
    assert all("s1" not in ids for ids in calls)       # sealed never evaluated in-loop
    assert sealed_calls == [["s1"]]                    # only the campaign-final step saw sealed
    assert result["report"]["decisions"]["keep"] == 1
    assert [record["decision"] for record in result["rounds"]] == ["keep", "inconclusive"]
    # Validation is reduced to aggregates: no case bodies or transcripts leak back.
    for record in result["rounds"]:
        assert "results" not in record["validation"]
        assert "コメント" not in json.dumps(record, ensure_ascii=False)
        assert record["mutation_target"] == "comment-prompt"
        assert record["split_manifest_hash"] == "sha256:x"


def test_hillclimb_reverts_a_leaking_mutation_before_validation():
    cases = suite_split(comment="右上の赤いやつは敵のボスなので近づかないほうがいいとおもう")
    calls = {"n": 0}

    def evaluate(selected):
        calls["n"] += 1
        return runner.run_cases(selected, lambda case: {"response": None, "side_effects": 0},
                                candidate_name="cand")

    def generate(round_index, train_cases):
        return {"hypothesis": "copy the case",
                "mutation": {"path": MUTATION_PATH,
                             "text": "右上の赤いやつは敵のボスなので近づかないほうがいいとおもう",
                             "diff_hash": "leak"}}

    result = hillclimb.run_campaign(campaign_id="c1", target="comment-prompt",
                                    cases_by_split=cases, split_manifest_hash="sha256:x",
                                    generate=generate, evaluate=evaluate, rounds=3, dry_run=False)
    assert calls["n"] == 1  # only the baseline validation ran; the leak stopped the round
    assert [record["decision"] for record in result["rounds"]] == ["revert"]
    assert result["rounds"][0]["decision_reason"] == "leakage_detected"
    assert result["rounds"][0]["leakage"]["violations"]


def test_hillclimb_defaults_to_a_side_effect_free_dry_run():
    cases = suite_split()
    result = hillclimb.run_campaign(
        campaign_id="c1", target="comment-prompt", cases_by_split=cases,
        split_manifest_hash="sha256:x", generate=None,
        evaluate=lambda selected: runner.run_cases(selected, lambda case: {"response": None}),
        rounds=8)
    assert result["dry_run"] is True and result["rounds"] == []


def test_hillclimb_refuses_a_sealed_case_in_the_loop():
    cases = suite_split()
    cases["validation"].append(make_case("s9", "sg9", "sealed_test"))
    with pytest.raises(contracts.ContractError):
        hillclimb.run_campaign(campaign_id="c1", target="comment-prompt", cases_by_split=cases,
                               split_manifest_hash="sha256:x", generate=None,
                               evaluate=lambda selected: None, rounds=1)

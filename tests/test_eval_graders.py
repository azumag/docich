"""Grader behaviour: deterministic hard fails, classifier metrics, semantic gate."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.eval import contracts  # noqa: E402
from docich.eval.graders import classifier, deterministic, reply_semantic  # noqa: E402

SUITE = ROOT / "evals/comment/v1"
VALID_REPLY = "今の盤面だと、そこには置けませんよ。"


def make_case(**expected):
    base = {"intent_family": "game", "category": "game_question", "screen_need": "required",
            "must_use_game_context": True, "must_not_claim_screen_seen_without_image": True}
    base.update(expected)
    return contracts.validate_case({
        "case_id": "c-1", "group_id": "t-1",
        "input": {"comment": "そこに置くんじゃない？"},
        "expected": base, "tags": []})


@pytest.mark.parametrize("output,code", [
    ({"error": "boom"}, "generation_failure"),
    ({"response": "   "}, "response_empty"),
    ({"response": "api error: invalid bearer token"}, "provider_error_leak"),
    ({"response": "abc"}, "invalid_generation_candidate"),
    ({"response": VALID_REPLY, "timed_out": True}, "timeout"),
    ({"response": VALID_REPLY, "parse_ok": False}, "parse_failure"),
    ({"response": VALID_REPLY, "category": "not_a_category"}, "unsupported_label"),
    ({"response": VALID_REPLY, "side_effects": 1}, "production_side_effect"),
    ({"response": VALID_REPLY, "capture_requested": False}, "required_context_omitted"),
    ({"response": "画面を見ると、赤いボスがいます。"}, "unsupported_screen_claim"),
])
def test_deterministic_hard_fails(output, code):
    case = make_case()
    assert code in deterministic.evaluate(case, output)


def test_deterministic_required_and_forbidden_content():
    case = make_case(response_must_include=["ボス"], response_must_not_include=["無理です"])
    fails = deterministic.evaluate(case, {"response": "それは無理です。"})
    assert "missing_required_content" in fails
    assert "forbidden_content" in fails


def test_deterministic_unnecessary_capture_and_clean_reply():
    case = make_case(screen_need="not_required", must_not_claim_screen_seen_without_image=False)
    assert deterministic.evaluate(case, {"response": VALID_REPLY, "capture_requested": True}) == \
        ["unnecessary_capture"]
    clean = make_case(screen_need=None, must_not_claim_screen_seen_without_image=False,
                      must_use_game_context=False, intent_family=None, category=None)
    assert deterministic.evaluate(clean, {"response": VALID_REPLY, "side_effects": 0}) == []


def test_claims_screen_seen_is_explicit_not_fuzzy():
    assert deterministic.claims_screen_seen("画面を見ると赤いです")
    assert not deterministic.claims_screen_seen("画面を見ないと分かりません")


def test_classifier_grader_reports_precision_recall_and_game_intent():
    cases = [
        contracts.validate_case({"case_id": "a", "group_id": "g1",
                                 "input": {"comment": "右上の赤いやつ"},
                                 "expected": {"category": "game_question", "screen_need": "required",
                                              "intent_family": "game"}, "tags": []}),
        contracts.validate_case({"case_id": "b", "group_id": "g2",
                                 "input": {"comment": "声が小さい"},
                                 "expected": {"category": "stream_bug_report",
                                              "screen_need": "not_required",
                                              "intent_family": "stream_ops"}, "tags": []}),
    ]
    outputs = {
        "a": {"category": "game_question", "screen_need": "not_required"},
        "b": {"category": "chitchat", "screen_need": "not_required"},
    }
    result = classifier.evaluate(cases, outputs)
    assert result["category"]["per_label"]["game_question"]["recall"] == 1.0
    assert result["category"]["correct_fraction_all"] == 0.5
    # screen_need required missed as not_required is a critical false negative.
    assert result["screen_need"]["critical_false_negative"] == 1
    assert result["game_intent"] == {"support": 1, "hits": 1, "recall": 1.0}
    assert result["grader_version"] == contracts.GRADER_VERSIONS["classifier"]


def test_semantic_grader_loads_rubric_and_prompts_blindly():
    rubric = reply_semantic.load_rubric(SUITE / "rubric.json")
    case = make_case()
    request = reply_semantic.axis_request(rubric, case, VALID_REPLY, "intent_relevance")
    assert request["axis"] == "intent_relevance"
    assert "candidate" not in request and "label" not in request
    assert request["comment"] == case["input"]["comment"]


def test_semantic_grader_defaults_to_missing_and_reliability_gate():
    case = make_case()
    with pytest.raises(contracts.ContractError):
        reply_semantic.evaluate(case, VALID_REPLY, None, rubric=reply_semantic.load_rubric(
            SUITE / "rubric.json"))
    assert reply_semantic.reliability_ok({"repeat_agreement": 0.95, "gold_hit_rate": 0.9,
                                          "order_flip_rate": 0.0})
    assert not reply_semantic.reliability_ok({"repeat_agreement": 0.5})
    assert not reply_semantic.reliability_ok({"repeat_agreement": 0.95, "gold_hit_rate": 0.9,
                                              "order_flip_rate": 0.2})


def test_semantic_grader_scores_axes_via_injected_judge():
    rubric = reply_semantic.load_rubric(SUITE / "rubric.json")
    result = reply_semantic.evaluate(make_case(), VALID_REPLY,
                                     lambda request: {"score": 0.75, "reason": "ok"},
                                     rubric=rubric)
    assert result["coverage"] == 1.0
    assert result["mean_of_available"] == 0.75
    assert set(result["axes"]) == set(reply_semantic.AXES)

"""Metrics, the offline runner and report assembly (#1308 PR-2)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.eval import candidates, contracts, load_suite, metrics, report, runner  # noqa: E402

SUITE = ROOT / "evals/comment/v1"


def test_quantiles_and_ratio():
    assert metrics.ratio(1, 0) is None
    assert metrics.quantiles([])["n"] == 0
    summary = metrics.quantiles([1, 2, 3, 4, 5])
    assert summary["n"] == 5 and summary["p50"] >= 3


def test_score_predictions_counts_abstentions():
    scored = metrics.score_predictions([("a", "a"), ("b", None), ("b", "a")])
    assert scored["n"] == 3 and scored["available_n"] == 2
    assert scored["coverage"] == pytest.approx(2 / 3)
    assert scored["correct_fraction_all"] == pytest.approx(1 / 3)
    assert scored["confusion"]["b->unavailable"] == 1


def test_screen_need_metrics_flags_critical_misses():
    scored = metrics.screen_need_metrics([("required", "not_required"),
                                          ("not_required", "uncertain")])
    assert scored["critical_false_negative"] == 1
    assert scored["required_support"] == 1 and scored["abstain_rate"] == 0.5


def test_bootstrap_ci_is_deterministic_and_detects_zero_crossing():
    first = metrics.bootstrap_ci([0.2] * 10, seed=1)
    assert first == metrics.bootstrap_ci([0.2] * 10, seed=1)
    assert first["lo"] > 0 and not first["zero_crossing"]
    assert metrics.bootstrap_ci([0.2, -0.2] * 5, seed=1)["n"] == 10


def test_runner_refuses_sealed_without_opt_in():
    suite = load_suite(SUITE)
    with pytest.raises(contracts.ContractError):
        runner.run_cases(suite["by_split"]["sealed_test"], candidates.echo_candidate(),
                         allow_sealed=False)


def test_runner_records_candidate_defects_and_runs_heuristic_baseline():
    suite = load_suite(SUITE)

    def broken(case):
        raise RuntimeError("boom")

    run = runner.run_cases(suite["by_split"]["validation"], broken, suite="comment-v1")
    assert run.deterministic_failures["by_code"].get("generation_failure") == len(run.results)
    assert all(result.error == "RuntimeError" for result in run.results)

    baseline = runner.run_cases(suite["by_split"]["validation"], candidates.heuristic_candidate(),
                                suite="comment-v1", candidate_name="heuristic")
    assert baseline.classifier is not None
    assert baseline.classifier["category"]["n"] >= 1
    assert baseline.cost["unknown_cases"] == len(baseline.results)


def test_report_keeps_hard_fails_separate_and_compares_cost_and_latency():
    suite = load_suite(SUITE)
    cases = suite["by_split"]["validation"][:2]
    base = runner.run_cases(cases, lambda case: {"response": None, "side_effects": 0,
                                                "scores": {"quality": 0.4}}, candidate_name="base")
    cand = runner.run_cases(cases, lambda case: {"response": None, "side_effects": 1,
                                                 "scores": {"quality": 0.6}}, candidate_name="cand")
    built = report.build_report(cand, base=base)
    assert built["hard_failures"]["by_code"].get("production_side_effect") == len(cases)
    assert built["comparison"]["hard_fail_regressions"] == ["production_side_effect"]
    assert built["comparison"]["metrics"]["quality"]["n"] == len(cases)
    assert "latency" in built and "cost" in built
    text = report.render_text(built)
    assert "hard fails" in text and "comparison" in text
    # Case bodies never appear in a report unless explicitly requested.
    assert all("output" not in row for row in built["per_case"])

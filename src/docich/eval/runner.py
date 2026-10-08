"""Side-effect-free offline runner for the eval base (#1308 PR-2 / PR-4).

A runner drives an injected ``candidate`` callable over a set of cases, times
each call, records usage/cost and applies the deterministic grader. It owns no
network client and writes no production state: a candidate that mutates
production must declare it in ``side_effects`` and is failed by the grader.

Sealed cases are refused unless ``allow_sealed=True`` is passed by the
campaign-final step, which is exactly why an ordinary round can never see them.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import metrics
from .contracts import SPLIT_SEALED, ContractError
from .graders import classifier as classifier_grader
from .graders import deterministic as deterministic_grader


@dataclass
class CaseResult:
    case_id: str
    group_id: str
    split: str | None
    output: dict
    latency_ms: float | None = None
    usage: dict | None = None
    cost_usd: float | None = None
    hard_fails: list = field(default_factory=list)
    scores: dict = field(default_factory=dict)
    error: str | None = None


@dataclass
class RunResult:
    suite: str
    candidate: str
    split: str | None
    results: list
    deterministic_failures: dict
    latency: dict
    cost: dict
    usage: dict
    classifier: dict | None = None

    @property
    def case_ids(self) -> list:
        return [result.case_id for result in self.results]


def _numeric_scores(output: dict, latency_ms) -> dict:
    scores = {}
    raw = output.get("scores")
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                scores[str(key)] = float(value)
    scores["deterministic_hard_fails"] = float(len(output.get("_hard_fails", [])))
    if latency_ms is not None:
        scores["latency_ms"] = latency_ms
    return scores


def run_cases(cases, candidate, *, suite: str = "", candidate_name: str = "baseline",
              split: str | None = None, allow_sealed: bool = False, clock=time.monotonic,
              grader=deterministic_grader) -> RunResult:
    cases = list(cases)
    if not allow_sealed and any(case.get("split") == SPLIT_SEALED for case in cases):
        raise ContractError("sealed_test_locked")
    results = []
    for case in cases:
        started = clock()
        error = None
        try:
            output = candidate(case)
        except Exception as exc:  # a candidate defect must not abort the run
            output, error = None, type(exc).__name__
        latency_ms = (clock() - started) * 1000
        if not isinstance(output, dict):
            error = error or "invalid_output"
            output = {}
        output = dict(output)
        if error:
            output["error"] = error
        fails = grader.evaluate(case, output)
        output["_hard_fails"] = fails
        results.append(CaseResult(
            case_id=case["case_id"],
            group_id=case["group_id"],
            split=case.get("split"),
            output=output,
            latency_ms=latency_ms,
            usage=output.get("usage") if isinstance(output.get("usage"), dict) else None,
            cost_usd=output.get("cost_usd") if isinstance(output.get("cost_usd"), (int, float))
            and not isinstance(output.get("cost_usd"), bool) else None,
            hard_fails=fails,
            scores=_numeric_scores(output, latency_ms),
            error=error or output.get("error"),
        ))
    run = RunResult(
        suite=suite,
        candidate=candidate_name,
        split=split,
        results=results,
        deterministic_failures=metrics.hard_fail_summary(results),
        latency=metrics.latency_summary(results),
        cost=metrics.cost_summary(results),
        usage=metrics.usage_summary(results),
    )
    if all(case["expected"]["category"] is None
           and case["expected"]["screen_need"] is None for case in cases):
        return run
    outputs = {result.case_id: result.output for result in results}
    run.classifier = classifier_grader.evaluate(cases, outputs)
    return run

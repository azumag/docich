"""Report assembly for the offline eval base (#1308 sections 6 and 9).

Turns a :class:`~docich.eval.runner.RunResult` (optionally paired with a
baseline) into a serialisable dict plus a short plain-text rendering. Hard
failures are reported separately from quality scores, and case bodies are not
copied into the report by default, so a report never becomes a transcript of
the corpus. The sealed split only appears here if it was explicitly run.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import metrics, contracts


def _score_keys(results) -> list:
    keys = set()
    for result in results:
        keys.update(result.scores)
    return sorted(keys)


def build_report(run, *, base=None, label=None, include_case_outputs=False,
                 seed=contracts.DEFAULT_SEED) -> dict:
    report = {
        "schema": "docich.eval.report.v1",
        "label": label or run.candidate,
        "suite": run.suite,
        "candidate": run.candidate,
        "split": run.split,
        "case_count": len(run.results),
        "hard_failures": run.deterministic_failures,
        "classifier": run.classifier,
        "latency": run.latency,
        "cost": run.cost,
        "usage": run.usage,
        "per_case": [_case_row(result, include_case_outputs) for result in run.results],
    }
    if base is not None:
        report["comparison"] = _comparison(base, run, seed=seed)
    return report


def _case_row(result, include_outputs) -> dict:
    row = {"case_id": result.case_id, "split": result.split,
           "hard_fails": list(result.hard_fails), "error": result.error,
           "scores": {k: v for k, v in result.scores.items()}}
    if include_outputs:
        row["output"] = result.output
    return row


def _comparison(base, candidate, *, seed) -> dict:
    keys = sorted(set(_score_keys(base.results)) & set(_score_keys(candidate.results)))
    comparisons = {key: metrics.paired_compare(base.results, candidate.results, key,
                                               seed=seed) for key in keys}
    base_fails = base.deterministic_failures.get("by_code", {})
    cand_fails = candidate.deterministic_failures.get("by_code", {})
    return {
        "metrics": comparisons,
        "hard_fail_delta": {code: cand_fails.get(code, 0) - base_fails.get(code, 0)
                            for code in sorted(set(base_fails) | set(cand_fails))},
        "hard_fail_regressions": sorted(
            code for code in cand_fails
            if cand_fails.get(code, 0) > base_fails.get(code, 0)),
    }


def render_text(report: dict) -> str:
    lines = [f"suite      : {report['suite']}",
             f"candidate  : {report['candidate']}" + (f" (label {report['label']})"
                                                      if report.get("label") else ""),
             f"split      : {report['split'] or 'all'}",
             f"cases      : {report['case_count']}",
             f"hard fails : {report['hard_failures']['total']} "
             f"({report['hard_failures']['by_code'] or 'none'})",
             f"latency ms : {report['latency']['total_ms']}",
             f"cost usd   : {report['cost']['total_usd']} "
             f"(unknown {report['cost']['unknown_cases']})"]
    classifier = report.get("classifier")
    if classifier:
        cat = classifier["category"]
        screen = classifier["screen_need"]
        lines += [
            f"category   : macro_f1={cat['macro_f1_with_abstentions_as_misses']} "
            f"coverage={cat['coverage']} correct/all={cat['correct_fraction_all']}",
            f"screen_need: required_p={screen['required_precision']} "
            f"required_r={screen['required_recall']} "
            f"critical_FN={screen['critical_false_negative']} "
            f"critical_FP={screen['critical_false_positive']}",
            f"game_intent: {classifier['game_intent']}",
        ]
    comparison = report.get("comparison")
    if comparison:
        lines.append("comparison :")
        for key, row in comparison["metrics"].items():
            lines.append(f"  {key}: delta={row['mean']} ci=[{row['lo']},{row['hi']}] "
                         f"n={row['n']}")
        if comparison["hard_fail_regressions"]:
            lines.append(f"  hard-fail regressions: {comparison['hard_fail_regressions']}")
    return "\n".join(lines)


def write_report(path, report: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")

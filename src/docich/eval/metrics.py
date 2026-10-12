"""Deterministic metrics for the offline eval base (#1308 PR-2).

Pure functions only: no provider, no clock, and no RNG except the seeded
bootstrap interval. These are the numbers a keep/revert decision reads, so the
classifier grader, the deterministic grader and the campaign report all import
them instead of re-implementing scoring at each call site.

Deterministic hard failures are kept in their own counter and are never folded
into a mean score (issue #1308 section 6: a hard gate is not offset by
semantic quality).
"""
from __future__ import annotations

import math
import random
from collections import Counter

from .contracts import DEFAULT_SEED


def ratio(a, b):
    return a / b if b else None


def mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def quantiles(values) -> dict:
    values = sorted(v for v in values if isinstance(v, (int, float)) and math.isfinite(v))
    if not values:
        return {"n": 0, "p50": None, "p95": None, "p99": None}
    return {"n": len(values), **{
        name: values[max(0, math.ceil(len(values) * p) - 1)]
        for name, p in (("p50", .50), ("p95", .95), ("p99", .99))}}


def score_predictions(pairs) -> dict:
    """Generic classifier scoring; an unavailable prediction stays a miss.

    ``pairs`` is a sequence of ``(gold, predicted)`` with ``predicted`` allowed
    to be ``None`` (abstain / parse failure). Abstentions are counted in the
    denominator via ``coverage`` so a runner cannot raise accuracy by answering
    less often.
    """
    pairs = list(pairs)
    available = [(gold, pred) for gold, pred in pairs if pred is not None]
    labels = sorted({gold for gold, _ in pairs} | {pred for _, pred in available})
    per_class = {}
    for label in labels:
        tp = sum(g == p == label for g, p in available)
        fp = sum(g != label and p == label for g, p in available)
        fn = sum(g == label and p != label for g, p in pairs)
        per_class[label] = {"precision": ratio(tp, tp + fp), "recall": ratio(tp, tp + fn),
                            "f1": ratio(2 * tp, 2 * tp + fp + fn), "support": sum(
                                g == label for g, _ in pairs)}
    f1s = [row["f1"] or 0 for row in per_class.values()]
    confusions = Counter(f"{g}->{p if p is not None else 'unavailable'}" for g, p in pairs)
    return {
        "n": len(pairs),
        "available_n": len(available),
        "coverage": ratio(len(available), len(pairs)),
        "accuracy_on_available": ratio(sum(g == p for g, p in available), len(available)),
        "correct_fraction_all": ratio(sum(g == p for g, p in available), len(pairs)),
        "macro_f1_with_abstentions_as_misses": sum(f1s) / len(f1s) if f1s else None,
        "per_label": per_class,
        "confusion": dict(confusions),
    }


def screen_need_metrics(pairs) -> dict:
    """screen_need P/R plus the critical miss counts that a mean would hide."""
    pairs = list(pairs)
    gold_required = [p for g, p in pairs if g == "required"]
    gold_not_required = [p for g, p in pairs if g == "not_required"]
    tp = sum(g == p == "required" for g, p in pairs)
    fp = sum(g != "required" and p == "required" for g, p in pairs)
    fn = sum(g == "required" and p != "required" for g, p in pairs)
    tn = sum(g == "not_required" and p == "not_required" for g, p in pairs)
    return {
        "n": len(pairs),
        "required_precision": ratio(tp, tp + fp),
        "required_recall": ratio(tp, tp + fn),
        "required_f1": ratio(2 * tp, 2 * tp + fp + fn),
        "not_required_precision": ratio(tn, tn + fn),
        "abstain_rate": ratio(sum(p == "uncertain" for _, p in pairs), len(pairs)),
        "unavailable_rate": ratio(sum(p is None for _, p in pairs), len(pairs)),
        "critical_false_negative": sum(g == "required" and p == "not_required"
                                       for g, p in pairs),
        "critical_false_positive": sum(g == "not_required" and p == "required"
                                       for g, p in pairs),
        "required_support": len(gold_required),
        "not_required_support": len(gold_not_required),
        "scores": score_predictions(pairs),
    }


def hard_fail_summary(results) -> dict:
    counts = Counter(code for result in results for code in (result.hard_fails or []))
    return {"total": sum(counts.values()), "by_code": dict(counts),
            "cases_with_hard_fail": sum(bool(r.hard_fails) for r in results)}


def bootstrap_ci(values, *, seed: int = DEFAULT_SEED, iters: int = 2000,
                 alpha: float = 0.05) -> dict:
    """Percentile bootstrap of the mean; deterministic for a given seed."""
    values = [v for v in values if isinstance(v, (int, float)) and math.isfinite(v)]
    if not values:
        return {"n": 0, "mean": None, "lo": None, "hi": None, "zero_crossing": True}
    rng = random.Random(seed)
    size = len(values)
    means = sorted(sum(values[rng.randrange(size)] for _ in range(size)) / size
                   for _ in range(iters))
    lo = means[max(0, int(iters * (alpha / 2)))]
    hi = means[min(iters - 1, int(iters * (1 - alpha / 2)))]
    return {"n": size, "mean": sum(values) / size, "lo": lo, "hi": hi,
            "zero_crossing": lo <= 0 <= hi}


def paired_deltas(base_results, candidate_results, key: str) -> list:
    """Per-case candidate-minus-baseline for one metric, aligned by case_id."""
    base = {r.case_id: r for r in base_results}
    out = []
    for result in candidate_results:
        other = base.get(result.case_id)
        if other is None:
            continue
        a, b = other.scores.get(key), result.scores.get(key)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            out.append(b - a)
    return out


def paired_compare(base_results, candidate_results, key: str, *, seed: int = DEFAULT_SEED) -> dict:
    deltas = paired_deltas(base_results, candidate_results, key)
    return {"metric": key, "n": len(deltas), **bootstrap_ci(deltas, seed=seed)}


def latency_summary(results) -> dict:
    return {"total_ms": quantiles([r.latency_ms for r in results]),
            "per_case_ms": quantiles([r.latency_ms for r in results if r.latency_ms is not None])}


def cost_summary(results) -> dict:
    known = [r.cost_usd for r in results if isinstance(r.cost_usd, (int, float))]
    return {"known_cases": len(known), "unknown_cases": sum(
        r.cost_usd is None for r in results), "total_usd": sum(known) if known else None}


def usage_summary(results) -> dict:
    input_tokens = output_tokens = 0
    known = 0
    for result in results:
        usage = result.usage
        if isinstance(usage, dict) and isinstance(usage.get("input_tokens"), int) \
                and isinstance(usage.get("output_tokens"), int):
            known += 1
            input_tokens += usage["input_tokens"]
            output_tokens += usage["output_tokens"]
    return {"known_cases": known, "unknown_cases": len(results) - known,
            "input_tokens": input_tokens, "output_tokens": output_tokens}

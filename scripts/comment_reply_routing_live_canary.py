#!/usr/bin/env python3
"""Opt-in grouped live canary for comment category + evidence routing.

The 19-item synthetic corpus is submitted in groups of at most eight. Each
group uses one existing Jev request containing both cN category and eN
evidence questions. This measures Jev's labels, confidence, and batch latency;
it does not run research or prove final-answer quality.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import statistics
import tempfile
import time

from docich.comment_classifier import heuristic, jev
from docich.reply_routing import ENABLE_ENV, RUBRIC_VERSION
from docich.semantic_decision.routes import resolve_route

CORPUS = Path(__file__).resolve().parents[1] / "tests/fixtures/reply_routing_canary.json"
CONFIRM = "I_HAVE_APPROVED_POSSIBLE_PROVIDER_COST"
FAILURES = frozenset({"missing_key", "timeout", "rate_limited", "network_error",
                      "server_error", "auth_error", "invalid_response", "invalid_config",
                      "http_error", "overloaded", "state_unavailable", "busy", "input_limit"})


def percentile95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, (95 * len(ordered) + 99) // 100 - 1)], 3)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="send grouped synthetic Jev requests")
    args = parser.parse_args(argv)
    if not args.live:
        parser.error("live requests are disabled unless --live is supplied")
    env = dict(os.environ)
    if env.get("DOCICH_REPLY_CANARY_CONFIRM") != CONFIRM:
        parser.error("explicit possible-cost acknowledgment is required")
    if env.get("DOCICH_ALLOW_REAL_AI") != "1":
        parser.error("DOCICH_ALLOW_REAL_AI=1 is required")
    env[ENABLE_ENV] = "1"
    env["COMMENT_CLASSIFIER_BACKEND"] = "jev"
    env["COMMENT_CLASSIFIER_JEV_LOG_ENABLED"] = "0"
    env["COMMENT_SCREEN_CONTEXT_ENABLED"] = "0"
    try:
        config = replace(jev.Config.from_env(env), evidence_enabled=True)
        if len(config.chain) != 1:
            parser.error("set one existing Jev API route; canary refuses configured route failover")
        route = resolve_route(config.route)
        key_present = bool(env.get(route.credential_env))
    except SystemExit:
        raise
    except Exception:
        key_present = False
    if not key_present:
        print(json.dumps({"status": "missing_key", "requests_attempted": 0}, sort_keys=True))
        return 2

    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    outcomes, latencies = [], []
    with tempfile.TemporaryDirectory(prefix="docich-comment-canary-") as state:
        for start in range(0, len(corpus), jev.MAX_COMMENTS):
            batch = corpus[start:start + jev.MAX_COMMENTS]
            baseline = heuristic.baseline([f"case-{i + start + 1}: {item['text']}"
                                           for i, item in enumerate(batch)])
            started = time.perf_counter()
            try:
                classified, event = jev.classify(baseline, config, env, Path(state))
                elapsed = round((time.perf_counter() - started) * 1000, 3)
                status = event.get("status", "invalid_response")
                details = event.get("rows", [])
            except Exception:
                elapsed, status, details = round((time.perf_counter() - started) * 1000, 3), "invalid_response", []
            latencies.append(elapsed)
            for index, sample in enumerate(batch):
                detail = details[index] if index < len(details) and isinstance(details[index], dict) else {}
                decision_status = detail.get("evidence_status", status)
                scope = detail.get("evidence_scope") if decision_status == "jev" else None
                confidence = detail.get("evidence_confidence")
                answered = decision_status == "jev" and scope in jev.EVIDENCE_CRITERIA and confidence is not None and confidence >= .80
                outcomes.append({"id": sample["id"], "expected": sample["expected"],
                                 "scope": scope, "status": decision_status,
                                 "confidence": confidence, "batch_latency_ms": elapsed,
                                 "correct": bool(answered and scope == sample["expected"])})
            if status in FAILURES:
                break

    answered = [row for row in outcomes if row["status"] == "jev" and row["scope"] is not None]
    count = len(outcomes)
    output = {
        "status": "complete" if count == len(corpus) else (outcomes[-1]["status"] if outcomes else "unavailable"),
        "rubric": RUBRIC_VERSION,
        "combined_request": True,
        "corpus_count": len(corpus),
        "cases_measured": count,
        "requests_attempted": len(latencies),
        "coverage": round(len(answered) / count, 4) if count else 0,
        "accuracy_on_available": round(sum(row["correct"] for row in answered) / len(answered), 4) if answered else None,
        "exact_match_all": round(sum(row["correct"] for row in outcomes) / len(corpus), 4) if corpus else None,
        "low_confidence_count": sum(row["status"] == "low_confidence" for row in outcomes),
        "low_confidence_rate": round(sum(row["status"] == "low_confidence" for row in outcomes) / count, 4) if count else 0,
        "mean_batch_latency_ms": round(statistics.mean(latencies), 3) if latencies else None,
        "p95_batch_latency_ms": percentile95(latencies),
        "results": outcomes,
    }
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0 if count == len(corpus) else 2


if __name__ == "__main__":
    raise SystemExit(main())

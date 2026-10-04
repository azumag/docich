#!/usr/bin/env python3
"""Opt-in live JEV canary over a synthetic, PII-free question corpus.

No request is sent unless --live and DOCICH_REPLY_CANARY_CONFIRM are both set.
This measures Jev's labels, confidence, and latency; it does not test research,
code execution, or final replies. Mock tests are not meaning-accuracy evidence.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import time

from docich.reply_routing import RUBRIC_VERSION, decide
from docich.semantic_decision.routes import parse_route_chain, resolve_route

CORPUS = Path(__file__).resolve().parents[1] / "tests/fixtures/reply_routing_canary.json"
CONFIRM = "I_HAVE_APPROVED_POSSIBLE_PROVIDER_COST"
PROVIDER_FAILURES = frozenset({
    "missing_key", "timeout", "rate_limited", "network_error", "server_error",
    "auth_error", "invalid_response", "invalid_config", "http_error", "overloaded",
})


def percentile95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, (95 * len(ordered) + 99) // 100 - 1)], 3)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="send one synthetic classification request per case")
    args = parser.parse_args(argv)
    if not args.live:
        parser.error("live requests are disabled unless --live is supplied")
    env = dict(os.environ)
    if env.get("DOCICH_REPLY_CANARY_CONFIRM") != CONFIRM:
        parser.error("explicit possible-cost acknowledgment is required")
    if env.get("DOCICH_ALLOW_REAL_AI") != "1":
        parser.error("DOCICH_ALLOW_REAL_AI=1 is required")
    try:
        chain = parse_route_chain(env.get("DOCICH_JEV_ROUTE", "direct"))
        if len(chain) != 1:
            parser.error("set one existing Jev API route; canary refuses configured route failover")
        route = chain[0]
        key_present = bool(env.get(resolve_route(route).credential_env))
    except Exception:
        key_present = False
    if not key_present:
        print(json.dumps({"status": "missing_key", "requests_attempted": 0}, sort_keys=True))
        return 2

    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    rows, latencies = [], []
    for sample in corpus:
        started = time.perf_counter()
        decision = decide([{"role": "user", "text": sample["text"]}], env=env)
        elapsed = round((time.perf_counter() - started) * 1000, 3)
        latencies.append(elapsed)
        rows.append({"id": sample["id"], "expected": sample["expected"],
                     "scope": decision.scope if decision.status == "jev" else None,
                     "status": decision.status, "confidence": decision.confidence,
                     "latency_ms": elapsed,
                     "correct": decision.status == "jev" and decision.scope == sample["expected"]})
        if decision.status in PROVIDER_FAILURES:
            break  # Avoid repeating a provider/configuration failure across the corpus.

    answered = [row for row in rows if row["status"] == "jev"]
    output = {
        "status": "complete" if len(rows) == len(corpus) else rows[-1]["status"],
        "rubric": RUBRIC_VERSION,
        "corpus_count": len(corpus),
        "requests_attempted": len(rows),
        "coverage": round(len(answered) / len(rows), 4) if rows else 0,
        "accuracy_answered": round(sum(row["correct"] for row in answered) / len(answered), 4) if answered else None,
        "exact_match_all": round(sum(row["correct"] for row in rows) / len(corpus), 4) if corpus else None,
        "low_confidence_count": sum(row["status"] == "low_confidence" for row in rows),
        "low_confidence_rate": round(sum(row["status"] == "low_confidence" for row in rows) / len(rows), 4) if rows else 0,
        "mean_latency_ms": round(statistics.mean(latencies), 3) if latencies else None,
        "p95_latency_ms": percentile95(latencies),
        "results": rows,
    }
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0 if len(rows) == len(corpus) else 2


if __name__ == "__main__":
    raise SystemExit(main())

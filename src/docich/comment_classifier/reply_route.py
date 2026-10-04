"""Fail-closed evidence routing for the existing broadcast comment batch.

The category and evidence questions are sent together by the existing Jev
classifier. This module only accepts the fixed evidence labels and invokes the
separate read-only research adapter when the combined batch needs it.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import time
from typing import Callable, Mapping

from . import classify_file as classify_comments_file
from . import jev
from ..reply_routing import CRITERIA, ENABLE_ENV

RESEARCH_SCOPES = frozenset({"web", "code", "web_and_code"})
MAX_ROUTING_BATCH_COMMENTS = 10  # Keep aligned with Twitch/YouTube/Kick fetch --limit 10.


@dataclass(frozen=True)
class BatchDecision:
    status: str
    scope: str
    reason: str
    confidence: float | None = None
    research_comments: tuple[str, ...] = ()


def aggregate(rows: list[dict], details: list[dict] | None) -> BatchDecision:
    """Combine per-comment labels without inferring API-only from a failure."""
    if not isinstance(rows, list) or not rows or not isinstance(details, list) or len(rows) != len(details):
        return BatchDecision("hold", "unknown", "classifier_unavailable")

    scopes: list[str] = []
    research_comments: list[str] = []
    confidences: list[float] = []
    pending: list[tuple[str, str]] = []
    for row, detail in zip(rows, details):
        if not isinstance(row, dict) or not isinstance(detail, dict):
            return BatchDecision("hold", "unknown", "invalid_result")
        status = detail.get("evidence_status")
        scope = detail.get("evidence_scope")
        confidence = detail.get("evidence_confidence")
        if (status != "jev" or scope not in CRITERIA
                or type(confidence) not in (int, float) or not math.isfinite(confidence)
                or confidence < 0.80 or confidence > 1):
            pending.append((status if isinstance(status, str) else "invalid_result",
                            scope if isinstance(scope, str) and scope in CRITERIA else "unknown"))
            continue
        scopes.append(scope)
        confidences.append(float(confidence))
        if scope != "api_only" and isinstance(row.get("comment"), str):
            research_comments.append(row["comment"])

    if any(scope == "runtime" for _, scope in pending) or "runtime" in scopes:
        return BatchDecision("hold", "runtime", "runtime_evidence_unavailable")
    if pending:
        return BatchDecision("hold", "unknown", "classification_unavailable")
    if not scopes:
        return BatchDecision("hold", "unknown", "local_notification" if rows else "empty_result")
    if "unknown" in scopes:
        return BatchDecision("hold", "unknown", "scope_unknown")
    need_web = "web" in scopes or "web_and_code" in scopes
    need_code = "code" in scopes or "web_and_code" in scopes
    scope = "web_and_code" if need_web and need_code else "web" if need_web else "code" if need_code else "api_only"
    return BatchDecision("ready", scope, "jev", min(confidences) if confidences else None,
                         tuple(research_comments))


def classify_file(path, *, env: Mapping[str, str] | None = None, transport=None,
                  researcher: Callable | None = None) -> dict:
    """Classify and, only after a valid non-runtime decision, request evidence."""
    env = os.environ if env is None else env
    if env.get(ENABLE_ENV, "0") != "1" or env.get("DOCICH_ALLOW_REAL_AI") != "1":
        return _hold([], "routing_disabled")
    try:
        rows, event = _classify_route_batch(path, env=env, transport=transport)
    except ValueError as exc:
        if str(exc) == "input_limit":
            return _hold([], "input_limit")
        return _hold([], "classifier_unavailable")
    except Exception:
        return _hold([], "classifier_unavailable")
    return _finish(rows, event, env=env, researcher=researcher)


def _classify_route_batch(path, *, env, transport):
    """Classify up to the stream fetch bound in MAX_COMMENTS-sized JEV chunks.

    The entire batch is still held unless every row has a valid evidence
    decision. Chunking is only a provider request boundary; it does not ack or
    omit the unclassified tail from the final routing decision.
    """
    lines = jev.heuristic.read_comment_lines(Path(path))
    if not 1 <= len(lines) <= MAX_ROUTING_BATCH_COMMENTS:
        raise ValueError("input_limit")
    expected_comments = [jev.heuristic.split_line(line)[1] for line in lines]
    if (len(lines) <= jev.MAX_COMMENTS
            or env.get("COMMENT_CLASSIFIER_BACKEND") != "jev"):
        rows, event = classify_comments_file(path, env=env, transport=transport)
        return _require_complete_classification(rows, event, expected_comments)

    started = time.monotonic()
    baseline = jev.heuristic.baseline(lines)
    baseline_ms = (time.monotonic() - started) * 1000
    outputs, details = [], []
    for offset in range(0, len(baseline), jev.MAX_COMMENTS):
        originals = baseline[offset:offset + jev.MAX_COMMENTS]
        chunk = [dict(row, index=index) for index, row in enumerate(originals, 1)]
        chunk_started = time.monotonic()
        output, event = jev.run_jev(
            chunk, env=env, heuristic_ms=baseline_ms, started=chunk_started,
            transport=transport or jev.docich_transport)
        chunk_details = event.get("rows") if isinstance(event, dict) else None
        if (not isinstance(output, list) or len(output) != len(chunk)
                or not isinstance(chunk_details, list) or len(chunk_details) != len(chunk)):
            raise ValueError("invalid_result")
        for local_index, (out_row, detail) in enumerate(zip(output, chunk_details), 1):
            out_row["index"] = offset + local_index
            outputs.append(out_row)
            details.append(detail)
    return _require_complete_classification(outputs, {"rows": details}, expected_comments)


def _require_complete_classification(rows, event, expected_comments):
    """Reject classifier output that omits, duplicates, or rewrites a source row."""
    details = event.get("rows") if isinstance(event, dict) else None
    expected_count = len(expected_comments)
    if (not isinstance(rows, list) or len(rows) != expected_count
            or not isinstance(details, list) or len(details) != expected_count):
        raise ValueError("invalid_result")
    for index, (row, comment) in enumerate(zip(rows, expected_comments), 1):
        if (not isinstance(row, dict) or row.get("index") != index
                or row.get("comment") != comment):
            raise ValueError("invalid_result")
    return rows, event


def _hold(rows: list[dict], reason: str) -> dict:
    return {"schema_version": 1, "rows": rows,
            "routing": {"status": "hold", "scope": "unknown", "reason": reason,
                        "confidence": None, "research_status": "not_requested",
                        "notes": "", "sources": []}}


def _finish(rows, event, *, env, researcher):
    if env.get(ENABLE_ENV, "0") != "1":
        return _hold(rows, "routing_disabled")
    if event is None:
        return _hold(rows, "classifier_unavailable")
    details = event.get("rows") if isinstance(event, dict) else None
    decision = aggregate(rows, details)
    if decision.status != "ready":
        result = _hold(rows, decision.reason)
        result["routing"]["scope"] = decision.scope
        return result

    routing = {"status": "ready", "scope": decision.scope, "reason": decision.reason,
               "confidence": decision.confidence, "research_status": "not_requested",
               "notes": "", "sources": []}
    if decision.scope == "api_only":
        return {"schema_version": 1, "rows": rows, "routing": routing}
    if decision.scope == "runtime":
        return _hold(rows, "runtime_evidence_unavailable")
    try:
        if researcher is None:
            from ..reply_research import research as researcher
        evidence = researcher(
            [{"role": "user", "content": text} for text in decision.research_comments],
            decision.scope, env=env, timeout_sec=45.0)
    except Exception:
        evidence = None
    if evidence is None or not getattr(evidence, "ok", False):
        result = _hold(rows, "research_unavailable")
        result["routing"].update(scope=decision.scope, research_status="unavailable")
        return result
    notes = getattr(evidence, "notes", "")
    sources = getattr(evidence, "sources", ())
    if (not isinstance(notes, str) or not notes.strip() or len(notes.encode("utf-8")) > 8192
            or not isinstance(sources, (tuple, list)) or not 1 <= len(sources) <= 4
            or any(not isinstance(source, str) or len(source) > 512 for source in sources)):
        result = _hold(rows, "research_unavailable")
        result["routing"].update(scope=decision.scope, research_status="unavailable")
        return result
    routing.update(research_status="ok", notes=notes, sources=list(sources))
    return {"schema_version": 1, "rows": rows, "routing": routing}


def main(argv=None) -> int:
    import sys
    from ..semantic_decision.validator import dumps

    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: docich-comment-reply-route <comments_file>", file=sys.stderr)
        return 2
    try:
        result = classify_file(argv[0])
        print(dumps(result))
        return 0
    except Exception:
        # No raw text, environment values, paths, or provider errors.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

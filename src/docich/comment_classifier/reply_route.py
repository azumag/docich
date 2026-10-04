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
from typing import Callable, Mapping

from . import classify_file as classify_comments_file
from . import jev
from ..reply_routing import CRITERIA, ENABLE_ENV

RESEARCH_SCOPES = frozenset({"web", "code", "web_and_code"})


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
        if status == "local_notification" and row.get("user", "").casefold() in jev.SYSTEM_USERS:
            scopes.append("api_only")
            continue
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
        return BatchDecision("hold", "unknown", "empty_result")
    if "unknown" in scopes:
        return BatchDecision("hold", "unknown", "scope_unknown")
    need_web = "web" in scopes or "web_and_code" in scopes
    need_code = "code" in scopes or "web_and_code" in scopes
    scope = "web_and_code" if need_web and need_code else "web" if need_web else "code" if need_code else "api_only"
    reason = "jev" if confidences else "local_notification"
    return BatchDecision("ready", scope, reason, min(confidences) if confidences else None,
                         tuple(research_comments))


def classify_file(path, *, env: Mapping[str, str] | None = None, transport=None,
                  researcher: Callable | None = None) -> dict:
    """Classify and, only after a valid non-runtime decision, request evidence."""
    env = os.environ if env is None else env
    if env.get(ENABLE_ENV, "0") != "1" or env.get("DOCICH_ALLOW_REAL_AI") != "1":
        return _hold([], "routing_disabled")
    try:
        rows, event = classify_comments_file(path, env=env, transport=transport)
    except Exception:
        return _hold([], "classifier_unavailable")
    return _finish(rows, event, env=env, researcher=researcher)


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

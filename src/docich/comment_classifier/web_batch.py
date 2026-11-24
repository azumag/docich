"""Verified-Web direct research for the broadcast comment batch.

This is an opt-in sibling of the single-conversation direct Web path.  It never
starts OpenCode: search results only authorize URLs, fetched bodies come from
``WebBroker`` receipts, and every question must have verified Web evidence
before the batch is released.

The shared broker permits four fetch attempts, so this first rollout handles at
most four research-requiring comments.  Larger Web batches fail closed instead
of silently falling back after the direct path has been selected.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
import sys
import tempfile
import time
from typing import Mapping

from ..reply_research import Evidence, verify_quotes
from ..reply_research_web import Receipt, WebBroker, canonical_url, search_public
from ..reply_routing import project_research_batch

ENABLE_ENV = "DOCICH_REPLY_WEB_DIRECT_BATCH_ENABLED"
MAX_QUESTIONS = 4
MAX_QUERY_CHARS = 256


def research(turns, scope: str, *, env: Mapping[str, str], timeout_sec: float = 45.0,
             comment_scopes=None, diagnostic=None) -> Evidence:
    """Collect one verified Web reference per broadcast question, without a model."""
    if (env.get(ENABLE_ENV, "0") != "1"
            or env.get("DOCICH_REPLY_WEB_DIRECT_ENABLED") != "1"
            or env.get("DOCICH_REPLY_WEB_SEARCH_ENABLED") != "1"
            or env.get("DOCICH_REPLY_RESEARCH_ENABLED") != "1"
            or env.get("DOCICH_ALLOW_REAL_AI") != "1"
            or sys.platform != "linux"
            or scope != "web"
            or type(timeout_sec) not in (int, float)
            or not math.isfinite(timeout_sec)
            or not 0 < timeout_sec <= 45):
        return Evidence("research_disabled")
    if (not isinstance(comment_scopes, (tuple, list))
            or not 1 <= len(comment_scopes) <= MAX_QUESTIONS
            or any(value != "web" for value in comment_scopes)
            or not isinstance(turns, list)
            or len(turns) != len(comment_scopes)):
        return Evidence("input_limit")

    try:
        projected = project_research_batch(turns)
    except (ValueError, UnicodeError, TypeError, AttributeError):
        return Evidence("private_or_invalid_input")
    if len(projected) != len(comment_scopes):
        return Evidence("research_unavailable")

    queries: list[str] = []
    for turn in projected:
        query = " ".join(turn["text"].split())
        if (not 1 <= len(query) <= MAX_QUERY_CHARS
                or any(ord(char) < 32 for char in query)):
            return Evidence("input_limit")
        queries.append(query)

    deadline = time.monotonic() + timeout_sec
    try:
        with tempfile.TemporaryDirectory(prefix="docich-web-batch-") as directory:
            options = {"diagnostic": diagnostic} if diagnostic is not None else {}
            broker = WebBroker(Path(directory) / "web-unused.sock", deadline, **options)
            receipts: dict[str, Receipt] = {}
            refs: list[dict] = []
            attempts_left = MAX_QUESTIONS

            for offset, query in enumerate(queries):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return Evidence("research_unavailable")
                candidates = search_public(query, min(8.0, remaining), env=env)
                urls: list[str] = []
                for value in candidates[:8]:
                    url = canonical_url(value)
                    if url and url not in urls:
                        urls.append(url)
                if not urls:
                    return Evidence("research_unavailable")
                broker.authorize(urls)

                questions_left = len(queries) - offset
                allowed_attempts = max(1, attempts_left - (questions_left - 1))
                receipt = None
                for url in urls[:allowed_attempts]:
                    attempts_left -= 1
                    candidate = broker.fetch(url)
                    if not isinstance(candidate, Receipt):
                        continue
                    if hashlib.sha256(candidate.text.encode()).hexdigest() != candidate.text_sha256:
                        continue
                    if not candidate.text:
                        continue
                    receipt = candidate
                    break
                if receipt is None:
                    return Evidence("research_unavailable")

                receipts[receipt.receipt] = receipt
                refs.append({
                    "kind": "web",
                    "ref": receipt.url,
                    "receipt": receipt.receipt,
                    "sha256": receipt.sha256,
                    "quote": receipt.text[:1024],
                    "question": offset + 1,
                })

            result = verify_quotes(refs, receipts, Path("."), None, {}, tuple(comment_scopes))
            expected = tuple(range(1, len(comment_scopes) + 1))
            return result if result.covered_questions == expected else Evidence("research_unavailable")
    except Exception:
        return Evidence("research_unavailable")

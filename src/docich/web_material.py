"""Verified public-Web material collection shared by non-code consumers.

This adapter deliberately stops at evidence collection. It never chooses a
model, starts OpenCode, executes tools, modifies code, publishes content, or
performs runtime actions. Search output only authorizes candidate URLs; every
material item is derived from a WebBroker receipt whose fetched text hash is
rechecked here.

Consumers such as RADIO/news/education corners can feed the returned typed
material into an ordinary bounded `RADIO:*` generation request.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import tempfile
import time
from typing import Callable, Mapping, Sequence

from .reply_research_web import WebBroker, Receipt, canonical_url, search_public
from .reply_routing import _has_private_route_input

MAX_QUERIES = 3
MAX_SOURCES = 4
MAX_QUERY_CHARS = 256
MAX_EXCERPT_BYTES = 8192


@dataclass(frozen=True)
class VerifiedWebMaterial:
    url: str
    body_sha256: str
    text_sha256: str
    excerpt_sha256: str
    excerpt: str
    query_indexes: tuple[int, ...] = ()

    def wire(self) -> dict[str, str]:
        return {
            "url": self.url,
            "body_sha256": self.body_sha256,
            "text_sha256": self.text_sha256,
            "excerpt_sha256": self.excerpt_sha256,
            "excerpt": self.excerpt,
            "query_indexes": list(self.query_indexes),
        }


@dataclass(frozen=True)
class VerifiedWebBundle:
    status: str
    items: tuple[VerifiedWebMaterial, ...] = ()
    queries_used: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status in {"ok", "partial"} and bool(self.items)

    def wire(self) -> dict[str, object]:
        return {
            "status": self.status,
            "items": [item.wire() for item in self.items],
            "queries_used": list(self.queries_used),
        }


def _query(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid_query")
    text = " ".join(value.split())
    if (not 1 <= len(text) <= MAX_QUERY_CHARS
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in text)
            or _has_private_route_input(text)):
        raise ValueError("invalid_query")
    return text


def _queries(values: Sequence[str]) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= MAX_QUERIES:
        raise ValueError("invalid_query")
    result: list[str] = []
    for value in values:
        text = _query(value)
        if text not in result:
            result.append(text)
    if not result:
        raise ValueError("invalid_query")
    return tuple(result)


def _excerpt(text: str) -> str:
    if not isinstance(text, str) or not text:
        raise ValueError("invalid_receipt")
    raw = text.encode("utf-8")
    if len(raw) <= MAX_EXCERPT_BYTES:
        return text
    # UTF-8-safe bounded prefix. A truncated excerpt is explicitly re-hashed
    # below; the full verified-text hash remains separate.
    return raw[:MAX_EXCERPT_BYTES].decode("utf-8", errors="ignore").rstrip()


def _material(receipt: object, query_indexes: tuple[int, ...] = ()) -> VerifiedWebMaterial | None:
    if not isinstance(receipt, Receipt):
        return None
    url = canonical_url(receipt.url)
    if url is None or url != receipt.url:
        return None
    text = receipt.text
    if (not isinstance(text, str) or not text
            or hashlib.sha256(text.encode("utf-8")).hexdigest() != receipt.text_sha256):
        return None
    body_sha = receipt.sha256
    text_sha = receipt.text_sha256
    if (not isinstance(body_sha, str) or len(body_sha) != 64
            or not isinstance(text_sha, str) or len(text_sha) != 64):
        return None
    excerpt = _excerpt(text)
    if not excerpt:
        return None
    return VerifiedWebMaterial(
        url=url,
        body_sha256=body_sha,
        text_sha256=text_sha,
        excerpt_sha256=hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
        excerpt=excerpt,
        query_indexes=query_indexes,
    )


def collect_verified_web_material(
    queries: Sequence[str],
    *,
    env: Mapping[str, str],
    timeout_sec: float = 20.0,
    max_sources: int = MAX_SOURCES,
    searcher: Callable[[str, float], Sequence[str]] | None = None,
    broker=None,
    clock: Callable[[], float] = time.monotonic,
) -> VerifiedWebBundle:
    """Collect verified public material with finite search/fetch budgets.

    Caller owns the feature gate. This function performs at most three search
    calls and at most four public-body fetch attempts. Provider/search failure
    never escalates to OpenCode or a different backend.
    """
    planned = _queries(queries)
    if (type(timeout_sec) not in (int, float) or not math.isfinite(timeout_sec)
            or not 0 < float(timeout_sec) <= 45):
        raise ValueError("invalid_timeout")
    if type(max_sources) is not int or not 1 <= max_sources <= MAX_SOURCES:
        raise ValueError("invalid_limit")
    if not isinstance(env, Mapping):
        raise ValueError("invalid_env")

    deadline = clock() + float(timeout_sec)
    search = searcher or (lambda query, timeout: search_public(query, timeout, env=env))

    def run(active_broker) -> VerifiedWebBundle:
        candidates: list[str] = []
        candidate_queries: dict[str, set[int]] = {}
        used: list[str] = []
        for query_index, query in enumerate(planned):
            remaining = deadline - clock()
            if remaining <= 0:
                break
            used.append(query)
            try:
                found = search(query, min(8.0, remaining))
            except Exception:
                found = ()
            if not isinstance(found, (list, tuple)):
                continue
            for value in found[:8]:
                url = canonical_url(value)
                if not url:
                    continue
                if url not in candidate_queries and len(candidates) < 16:
                    candidates.append(url)
                    candidate_queries[url] = set()
                if url in candidate_queries:
                    candidate_queries[url].add(query_index)
        if not candidates:
            return VerifiedWebBundle("unavailable", (), tuple(used))

        active_broker.authorize(candidates)
        items: list[VerifiedWebMaterial] = []
        fetch_attempts = 0
        for url in candidates:
            if (len(items) >= max_sources or fetch_attempts >= MAX_SOURCES
                    or clock() >= deadline):
                break
            fetch_attempts += 1
            try:
                item = _material(
                    active_broker.fetch(url),
                    tuple(sorted(candidate_queries.get(url, ()))),
                )
            except Exception:
                item = None
            if item is not None and all(existing.url != item.url for existing in items):
                items.append(item)
        if not items:
            return VerifiedWebBundle("unavailable", (), tuple(used))
        status = "ok" if len(items) >= min(max_sources, len(candidates)) else "partial"
        return VerifiedWebBundle(status, tuple(items), tuple(used))

    if broker is not None:
        return run(broker)

    with tempfile.TemporaryDirectory(prefix="docich-web-material-") as directory:
        active = WebBroker(Path(directory) / "unused.sock", deadline)
        return run(active)

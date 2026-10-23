"""Bounded URL-only paper discovery for the cross-disciplinary paper corner.

Web Search is used only to discover candidate primary-source URLs. Search
snippets, titles and descriptions are deliberately discarded and can never
become paper facts. Metadata/body extraction belongs to a later primary-source
adapter and must independently verify the selected URL.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import time
from collections.abc import Mapping, Sequence
from typing import Callable
from urllib.parse import urlsplit

from .reply_research_web import canonical_url, search_public
from .reply_routing import _has_private_route_input

ENABLE_ENV = "DOCICH_RESEARCH_PAPER_DISCOVERY_ENABLED"
MAX_QUERIES = 3
MAX_QUERY_CHARS = 256
MAX_CANDIDATES = 12
MAX_SEARCH_RESULTS = 8
MAX_TIMEOUT_SEC = 20.0

# This is a discovery allowlist, not a statement about peer-review status or
# factual trust. The later metadata/body adapter must verify the actual record.
PRIMARY_HOSTS = {
    "arxiv.org": ("preprint", 10),
    "openreview.net": ("review-platform", 10),
    "biorxiv.org": ("preprint", 10),
    "medrxiv.org": ("preprint", 10),
    "proceedings.mlr.press": ("proceedings", 20),
    "dl.acm.org": ("publisher", 20),
    "ieeexplore.ieee.org": ("publisher", 20),
    "nature.com": ("publisher", 20),
    "science.org": ("publisher", 20),
    "journals.plos.org": ("publisher", 20),
    "link.springer.com": ("publisher", 20),
}


@dataclass(frozen=True)
class PaperCandidate:
    url: str
    source_kind: str
    query_indexes: tuple[int, ...]
    rank: int

    def wire(self) -> dict[str, object]:
        return {
            "url": self.url,
            "source_kind": self.source_kind,
            "query_indexes": list(self.query_indexes),
            "rank": self.rank,
        }


@dataclass(frozen=True)
class DiscoveryResult:
    status: str
    candidates: tuple[PaperCandidate, ...] = field(default=(), repr=False)
    queries_used: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "ok" and bool(self.candidates)

    def wire(self) -> dict[str, object]:
        return {
            "status": self.status,
            "candidates": [candidate.wire() for candidate in self.candidates],
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
        query = _query(value)
        if query not in result:
            result.append(query)
    if not result:
        raise ValueError("invalid_query")
    return tuple(result)


def _source(url: str) -> tuple[str, int] | None:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    value = PRIMARY_HOSTS.get(host)
    if value is None:
        return None
    path = parsed.path.lower()
    if path.endswith((".pdf", ".ps", ".gz", ".zip", ".tar", ".tgz")):
        return None
    # arXiv HTML abstract pages are suitable for later text fetch; PDF URLs are
    # not, and WebBroker's MIME boundary must not be widened for this corner.
    if host == "arxiv.org" and not path.startswith("/abs/"):
        return None
    # OpenReview public paper landing pages use /forum?id=... . Avoid API URLs
    # or arbitrary profile/group pages at discovery time.
    if host == "openreview.net" and not (path == "/forum" and parsed.query.startswith("id=")):
        return None
    return value


def discover_candidates(
    queries: Sequence[str],
    *,
    env: Mapping[str, str],
    max_candidates: int = MAX_CANDIDATES,
    timeout_sec: float = MAX_TIMEOUT_SEC,
    searcher: Callable[[str, float], Sequence[str]] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> DiscoveryResult:
    """Discover primary-source URLs without importing search snippets as facts."""
    if not isinstance(env, Mapping):
        return DiscoveryResult("invalid_config")
    flag = env.get(ENABLE_ENV, "0")
    if flag == "0":
        return DiscoveryResult("disabled")
    if (flag != "1" or env.get("DOCICH_REPLY_WEB_SEARCH_ENABLED") != "1"
            or type(max_candidates) is not int or not 1 <= max_candidates <= MAX_CANDIDATES
            or type(timeout_sec) not in (int, float) or not math.isfinite(timeout_sec)
            or not 0 < float(timeout_sec) <= MAX_TIMEOUT_SEC):
        return DiscoveryResult("invalid_config")
    try:
        planned = _queries(queries)
    except (TypeError, UnicodeError, ValueError):
        return DiscoveryResult("invalid_input")

    deadline = clock() + float(timeout_sec)
    search = searcher or (lambda query, timeout: search_public(query, timeout, env=env))
    seen: dict[str, dict[str, object]] = {}
    used: list[str] = []
    sequence = 0
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
        for value in found[:MAX_SEARCH_RESULTS]:
            url = canonical_url(value)
            if url is None:
                continue
            source = _source(url)
            if source is None:
                continue
            kind, priority = source
            entry = seen.get(url)
            if entry is None:
                entry = {
                    "kind": kind,
                    "priority": priority,
                    "queries": set(),
                    "sequence": sequence,
                }
                seen[url] = entry
                sequence += 1
            entry["queries"].add(query_index)

    if not seen:
        status = "timeout" if clock() >= deadline and not used else "unavailable"
        return DiscoveryResult(status, (), tuple(used))

    ordered = sorted(
        seen.items(),
        key=lambda pair: (
            -len(pair[1]["queries"]),
            pair[1]["priority"],
            pair[1]["sequence"],
            pair[0],
        ),
    )[:max_candidates]
    candidates = tuple(
        PaperCandidate(
            url=url,
            source_kind=str(entry["kind"]),
            query_indexes=tuple(sorted(entry["queries"])),
            rank=index + 1,
        )
        for index, (url, entry) in enumerate(ordered)
    )
    return DiscoveryResult("ok", candidates, tuple(used))

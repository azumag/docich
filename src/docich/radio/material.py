"""Shared public-Web material provider for native radio consumers."""
from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable, Mapping, Sequence

from ..reply_research_web import Receipt
from .contracts import MaterialQuery, WebMaterial


MAX_MATERIAL_QUERIES = 4
MAX_MATERIAL_ITEMS = 8
MAX_EXCERPT_CHARS = 1200


def _excerpt(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()[:MAX_EXCERPT_CHARS]


def collect_public_web_material(
    queries: Sequence[MaterialQuery],
    *,
    env: Mapping[str, str],
    timeout_sec: float = 20.0,
    max_items: int = 4,
    per_query: int = 2,
    collector: Callable | None = None,
) -> tuple[WebMaterial, ...]:
    """Collect a finite manifest from broker-verified public page bodies.

    Search snippets never enter the manifest. The underlying collector returns
    only WebBroker receipts. This layer rechecks text hashes, deduplicates URLs
    across queries and converts receipts into a stable radio MaterialProvider
    DTO. It has no command/path/provider-selection authority.
    """
    if (not isinstance(queries, Sequence) or isinstance(queries, (str, bytes))
            or not 1 <= len(queries) <= MAX_MATERIAL_QUERIES
            or type(timeout_sec) not in (int, float) or not 0 < timeout_sec <= 30
            or type(max_items) is not int or not 1 <= max_items <= MAX_MATERIAL_ITEMS
            or type(per_query) is not int or not 1 <= per_query <= 4):
        raise ValueError("invalid radio material request")
    if not isinstance(env, Mapping):
        raise ValueError("invalid radio material environment")
    if any(not isinstance(item, MaterialQuery) for item in queries):
        raise ValueError("invalid radio material query")

    if collector is None:
        from ..reply_research_web import collect_verified_public
        fetch = collect_verified_public
    else:
        fetch = collector
    deadline = time.monotonic() + float(timeout_sec)
    rows: list[WebMaterial] = []
    seen_urls: set[str] = set()
    for item in queries:
        if len(rows) >= max_items:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        receipts = fetch(
            item.query,
            env=env,
            timeout_sec=min(remaining, 12.0),
            limit=min(per_query, max_items - len(rows)),
        )
        for receipt in receipts:
            if len(rows) >= max_items:
                break
            if not isinstance(receipt, Receipt) or receipt.url in seen_urls:
                continue
            if hashlib.sha256(receipt.text.encode()).hexdigest() != receipt.text_sha256:
                continue
            text = _excerpt(receipt.text)
            if not text:
                continue
            seen_urls.add(receipt.url)
            rows.append(WebMaterial(item.kind, receipt.url, receipt.sha256, text))
    return tuple(rows)

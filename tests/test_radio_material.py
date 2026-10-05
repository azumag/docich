from __future__ import annotations

import hashlib
import time

import pytest

from docich.radio.contracts import MaterialQuery, WebMaterial
from docich.radio.material import collect_public_web_material
from docich.reply_research_web import Receipt


def _receipt(url: str, text: str, key: str = "a") -> Receipt:
    digest = hashlib.sha256(text.encode()).hexdigest()
    return Receipt(url, key * 32, digest, digest, text)


def test_material_contracts_reject_unsafe_shape():
    with pytest.raises(ValueError):
        MaterialQuery("Bad Kind", "topic")
    with pytest.raises(ValueError):
        MaterialQuery("news", "x" * 257)
    with pytest.raises(ValueError):
        WebMaterial("news", "http://example.test", "a" * 64, "text")


def test_collect_public_web_material_is_bounded_deduped_and_typed():
    first = _receipt("https://example.org/one", "one   body")
    duplicate = _receipt("https://example.org/one", "duplicate body", "b")
    second = _receipt("https://example.org/two", "two body", "c")
    calls = []

    def collect(query, *, env, timeout_sec, limit):
        calls.append((query, timeout_sec, limit, dict(env)))
        return (first, duplicate) if query == "market query" else (second,)

    rows = collect_public_web_material(
        (MaterialQuery("market", "market query"), MaterialQuery("asset", "asset query")),
        env={"SAFE": "1"},
        timeout_sec=5,
        max_items=2,
        per_query=2,
        collector=collect,
    )
    assert [item.kind for item in rows] == ["market", "asset"]
    assert [item.url for item in rows] == ["https://example.org/one", "https://example.org/two"]
    assert rows[0].excerpt == "one body"
    assert all(isinstance(item, WebMaterial) for item in rows)
    assert len(calls) == 2
    assert all(0 < call[1] <= 5 for call in calls)


def test_material_provider_rechecks_text_hash():
    body = "trusted body"
    good = _receipt("https://example.org/good", body)
    bad = Receipt(
        "https://example.org/bad",
        "z" * 32,
        hashlib.sha256(body.encode()).hexdigest(),
        "0" * 64,
        body,
    )
    rows = collect_public_web_material(
        (MaterialQuery("news", "topic"),),
        env={},
        collector=lambda *args, **kwargs: (bad, good),
    )
    assert rows == (WebMaterial("news", good.url, good.sha256, body),)


def test_material_provider_global_deadline_stops_later_queries(monkeypatch):
    clock = [100.0]
    calls = []

    def now():
        return clock[0]

    def collect(query, **kwargs):
        calls.append(query)
        clock[0] += 2.0
        return (_receipt(f"https://example.org/{len(calls)}", query),)

    monkeypatch.setattr(time, "monotonic", now)
    rows = collect_public_web_material(
        (MaterialQuery("a", "first"), MaterialQuery("b", "second")),
        env={},
        timeout_sec=1,
        collector=collect,
    )
    assert len(rows) == 1
    assert calls == ["first"]

from __future__ import annotations

import hashlib
import json
import time

import pytest

from docich import web_material as m
from docich.reply_research_web import Receipt

URL1 = "https://example.com/a"
URL2 = "https://example.org/b"


def _receipt(url=URL1, text="取得済みの公開本文です。"):
    body_sha = hashlib.sha256(("body:" + text).encode()).hexdigest()
    text_sha = hashlib.sha256(text.encode()).hexdigest()
    return Receipt(url, "a" * 32, body_sha, text_sha, text)


class Broker:
    def __init__(self, values):
        self.values = dict(values)
        self.authorized = []
        self.fetched = []

    def authorize(self, urls):
        self.authorized.extend(urls)

    def fetch(self, url):
        self.fetched.append(url)
        value = self.values.get(url)
        if isinstance(value, BaseException):
            raise value
        return value


def test_collects_only_verified_body_receipts_and_dedupes_candidates():
    broker = Broker({
        URL1: _receipt(URL1, "本文A。"),
        URL2: _receipt(URL2, "本文B。"),
    })
    searches = []

    def search(query, timeout):
        searches.append((query, timeout))
        return [URL1, URL1, URL2]

    result = m.collect_verified_web_material(
        ["暗号資産 最新", "Bitcoin technology"],
        env={},
        searcher=search,
        broker=broker,
        timeout_sec=10,
    )
    assert result.ok
    assert result.status == "ok"
    assert [item.url for item in result.items] == [URL1, URL2]
    assert result.items[0].query_indexes == (0, 1)
    assert result.items[1].query_indexes == (0, 1)
    assert broker.authorized == [URL1, URL2]
    assert broker.fetched == [URL1, URL2]
    assert len(searches) == 2
    wire = result.wire()
    assert wire["queries_used"] == ["暗号資産 最新", "Bitcoin technology"]
    assert "本文A" in wire["items"][0]["excerpt"]
    assert "snippet" not in json.dumps(wire)


def test_broker_authorization_failure_returns_unavailable_without_fetch():
    class BrokenBroker(Broker):
        def authorize(self, urls):
            raise RuntimeError("synthetic broker failure")

    broker = BrokenBroker({URL1: _receipt()})
    result = m.collect_verified_web_material(
        ["公開仕様"],
        env={},
        searcher=lambda *args: [URL1],
        broker=broker,
    )
    assert result.status == "unavailable"
    assert result.items == ()
    assert broker.fetched == []


def test_optional_host_allowlist_accepts_exact_and_subdomain_only():
    sub = "https://sub.example.com/c"
    blocked = "https://example.org/no"
    broker = Broker({
        URL1: _receipt(URL1, "exact"),
        sub: _receipt(sub, "subdomain"),
        blocked: _receipt(blocked, "blocked"),
    })
    result = m.collect_verified_web_material(
        ["公式資料"],
        env={},
        allowed_hosts=["example.com"],
        searcher=lambda *args: [URL1, sub, blocked],
        broker=broker,
    )
    assert result.ok
    assert [item.url for item in result.items] == [URL1, sub]
    assert blocked not in broker.authorized
    assert blocked not in broker.fetched


@pytest.mark.parametrize("hosts", [
    ["localhost"],
    ["-bad.example.com"],
    ["bad-.example.com"],
    ["bad..example.com"],
    ["example.com."] ,
    ["x.example"] * 17,
    [123],
])
def test_invalid_host_allowlist_holds_before_search(hosts):
    called = []
    with pytest.raises(ValueError, match="invalid_hosts"):
        m.collect_verified_web_material(
            ["query"],
            env={},
            allowed_hosts=hosts,
            searcher=lambda *args: called.append(True) or [],
            broker=Broker({}),
        )
    assert called == []


def test_forged_text_hash_is_not_material():
    rec = _receipt()
    forged = Receipt(rec.url, rec.receipt, rec.sha256, "0" * 64, rec.text)
    broker = Broker({URL1: forged})
    result = m.collect_verified_web_material(
        ["公開仕様"],
        env={},
        searcher=lambda *args: [URL1],
        broker=broker,
    )
    assert result.status == "unavailable"
    assert result.items == ()


def test_private_or_invalid_query_holds_before_search():
    calls = []
    for queries in (
        ["secret: abcdefghijkl"],
        ["x" * 257],
        [],
        ["ok", "two", "three", "four"],
    ):
        with pytest.raises(ValueError, match="invalid_query"):
            m.collect_verified_web_material(
                queries,
                env={},
                searcher=lambda *args: calls.append(args) or [],
                broker=Broker({}),
            )
    assert calls == []


def test_search_failure_does_not_escalate_and_later_query_can_succeed():
    broker = Broker({URL1: _receipt()})
    calls = []

    def search(query, timeout):
        calls.append(query)
        if len(calls) == 1:
            raise RuntimeError("synthetic search failure")
        return [URL1]

    result = m.collect_verified_web_material(
        ["first query", "second query"],
        env={},
        searcher=search,
        broker=broker,
    )
    assert result.ok
    assert calls == ["first query", "second query"]
    assert broker.fetched == [URL1]


def test_body_fetch_attempts_never_exceed_four_even_when_all_fail():
    urls = [f"https://example.com/{i}" for i in range(8)]
    broker = Broker({url: None for url in urls})
    result = m.collect_verified_web_material(
        ["bounded"],
        env={},
        searcher=lambda *args: urls,
        broker=broker,
    )
    assert result.status == "unavailable"
    assert len(broker.fetched) == m.MAX_SOURCES == 4


def test_excerpt_is_utf8_safe_bounded_and_separately_hashed():
    text = "あ" * 5000
    broker = Broker({URL1: _receipt(URL1, text)})
    result = m.collect_verified_web_material(
        ["長文"],
        env={},
        searcher=lambda *args: [URL1],
        broker=broker,
    )
    item = result.items[0]
    assert len(item.excerpt.encode("utf-8")) <= m.MAX_EXCERPT_BYTES
    assert item.text_sha256 == hashlib.sha256(text.encode()).hexdigest()
    assert item.excerpt_sha256 == hashlib.sha256(item.excerpt.encode()).hexdigest()
    assert item.excerpt != text


@pytest.mark.parametrize("timeout", [0, -1, 46, float("inf"), float("nan")])
def test_invalid_timeout_holds_before_search(timeout):
    called = []
    with pytest.raises(ValueError, match="invalid_timeout"):
        m.collect_verified_web_material(
            ["query"],
            env={},
            timeout_sec=timeout,
            searcher=lambda *args: called.append(True) or [],
            broker=Broker({}),
        )
    assert called == []


@pytest.mark.parametrize("limit", [0, 5, True, 1.5])
def test_invalid_source_limit_holds(limit):
    with pytest.raises(ValueError, match="invalid_limit"):
        m.collect_verified_web_material(
            ["query"],
            env={},
            max_sources=limit,
            searcher=lambda *args: pytest.fail("search"),
            broker=Broker({}),
        )


def test_expired_deadline_returns_without_fetch():
    clock = iter([0.0, 30.0])
    broker = Broker({URL1: _receipt()})
    result = m.collect_verified_web_material(
        ["query"],
        env={},
        timeout_sec=20,
        searcher=lambda *args: [URL1],
        broker=broker,
        clock=lambda: next(clock),
    )
    assert result.status == "unavailable"
    assert broker.fetched == []

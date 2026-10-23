import hashlib

from docich.comment_classifier import reply_route
from docich.comment_classifier import web_batch
from docich.reply_research import Evidence
from docich.reply_research_web import Receipt


ENV = {
    web_batch.ENABLE_ENV: "1",
    "DOCICH_REPLY_WEB_DIRECT_ENABLED": "1",
    "DOCICH_REPLY_WEB_SEARCH_ENABLED": "1",
    "DOCICH_REPLY_RESEARCH_ENABLED": "1",
    "DOCICH_ALLOW_REAL_AI": "1",
}


class Broker:
    receipts_by_url = {}
    authorized = []
    fetched = []

    def __init__(self, path, deadline, **kwargs):
        self.path = path
        self.deadline = deadline

    def authorize(self, urls):
        type(self).authorized.extend(urls)

    def fetch(self, url):
        type(self).fetched.append(url)
        return type(self).receipts_by_url.get(url)


def receipt(url, text):
    return Receipt(
        url,
        "receipt-" + url.rsplit("/", 1)[-1],
        hashlib.sha256(("body:" + text).encode()).hexdigest(),
        hashlib.sha256(text.encode()).hexdigest(),
        text,
    )


def reset_broker():
    Broker.receipts_by_url = {}
    Broker.authorized = []
    Broker.fetched = []


def test_direct_batch_collects_verified_evidence_for_every_question(monkeypatch):
    reset_broker()
    first = "https://example.com/first"
    second = "https://example.com/second"
    Broker.receipts_by_url = {
        first: receipt(first, "first verified source body"),
        second: receipt(second, "second verified source body"),
    }
    searches = []

    def search(query, timeout, env=None):
        searches.append(query)
        return [first] if query == "first question" else [second]

    monkeypatch.setattr(web_batch.sys, "platform", "linux")
    monkeypatch.setattr(web_batch, "WebBroker", Broker)
    monkeypatch.setattr(web_batch, "search_public", search)

    result = web_batch.research(
        [
            {"role": "user", "content": "first question"},
            {"role": "user", "content": "second question"},
        ],
        "web",
        env=ENV,
        comment_scopes=("web", "web"),
    )

    assert result.ok
    assert result.covered_questions == (1, 2)
    assert result.sources == (first, second)
    assert searches == ["first question", "second question"]
    assert Broker.fetched == [first, second]
    assert "質問1:" in result.notes
    assert "質問2:" in result.notes


def test_direct_batch_disabled_makes_zero_network_calls(monkeypatch):
    called = []
    monkeypatch.setattr(web_batch.sys, "platform", "linux")
    monkeypatch.setattr(web_batch, "search_public", lambda *a, **k: called.append(1))

    env = {**ENV, web_batch.ENABLE_ENV: "0"}
    result = web_batch.research(
        [{"role": "user", "content": "question"}],
        "web",
        env=env,
        comment_scopes=("web",),
    )

    assert result.status == "research_disabled"
    assert called == []


def test_direct_batch_rejects_more_than_shared_broker_fetch_budget(monkeypatch):
    called = []
    monkeypatch.setattr(web_batch.sys, "platform", "linux")
    monkeypatch.setattr(web_batch, "search_public", lambda *a, **k: called.append(1))
    turns = [{"role": "user", "content": f"question {index}"} for index in range(5)]

    result = web_batch.research(
        turns,
        "web",
        env=ENV,
        comment_scopes=("web",) * 5,
    )

    assert result.status == "input_limit"
    assert called == []


def test_reply_route_selects_direct_batch_without_legacy_fallback(monkeypatch):
    calls = []

    def direct(*args, **kwargs):
        calls.append("direct")
        return Evidence("ok", "verified", ("https://example.com/source",), (1,))

    def legacy(*args, **kwargs):
        raise AssertionError("legacy research must not run after direct batch selection")

    monkeypatch.setattr(web_batch, "research", direct)
    import docich.reply_research as legacy_module
    monkeypatch.setattr(legacy_module, "research", legacy)

    rows = [{"index": 1, "comment": "current public fact?"}]
    event = {"rows": [{
        "evidence_status": "jev",
        "evidence_scope": "web",
        "evidence_confidence": 0.95,
    }]}
    result = reply_route._finish(
        rows,
        event,
        env={web_batch.ENABLE_ENV: "1"},
        researcher=None,
    )

    assert calls == ["direct"]
    assert result["routing"]["status"] == "ready"
    assert result["routing"]["research_status"] == "ok"


def test_reply_route_keeps_code_and_mixed_scopes_on_legacy_research(monkeypatch):
    calls = []

    def legacy(*args, **kwargs):
        calls.append("legacy")
        return Evidence(
            "ok",
            "verified",
            ("https://example.com/web", "https://github.com/example/repo/blob/a/file#L1"),
            (1,),
        )

    import docich.reply_research as legacy_module
    monkeypatch.setattr(legacy_module, "research", legacy)
    monkeypatch.setattr(web_batch, "research", lambda *a, **k: (_ for _ in ()).throw(AssertionError("direct batch")))

    rows = [{"index": 1, "comment": "compare public docs and implementation"}]
    event = {"rows": [{
        "evidence_status": "jev",
        "evidence_scope": "web_and_code",
        "evidence_confidence": 0.95,
    }]}
    result = reply_route._finish(
        rows,
        event,
        env={web_batch.ENABLE_ENV: "1"},
        researcher=None,
    )

    assert calls == ["legacy"]
    assert result["routing"]["status"] == "ready"

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from docich.radio import research
from docich.web_material import VerifiedWebBundle, VerifiedWebMaterial


def _material(url="https://example.org/chip", excerpt="verified excerpt", indexes=(0,)):
    digest = hashlib.sha256(excerpt.encode()).hexdigest()
    return VerifiedWebMaterial(url, "a" * 64, digest, digest, excerpt, indexes)


ENV = {
    research.ENABLE_ENV: "1",
    "DOCICH_ALLOW_REAL_AI": "1",
    "DOCICH_JEV_ROUTE": "direct",
    "TYPESAFE_API_KEY": "SYNTHETIC_KEY",
}


def _answer(scope="web", confidence=.95):
    return {
        "status": "ok",
        "data": {
            "answers": {
                "radio_evidence": {"choice": scope, "confidence": confidence}
            }
        },
    }


def test_disabled_radio_planner_never_calls_transport():
    result = research.decide(
        "半導体の仕組み",
        env={},
        transport=lambda *a, **k: pytest.fail("transport"),
    )
    assert result.status == "disabled"


def test_radio_request_projects_only_topic_and_fixed_rubric():
    seen = {}
    def transport(request, **kwargs):
        seen["request"] = request
        seen["kwargs"] = kwargs
        return _answer("web", .93)

    result = research.decide("最近の橋梁工学について", env=ENV, transport=transport)
    assert result.accepted and result.scope == "web"
    request = seen["request"]
    assert request["state"] == {"topic": "最近の橋梁工学について"}
    assert set(request["questions"]) == {"radio_evidence"}
    assert set(request["questions"]["radio_evidence"]["criteria"]) == {"api_only", "web"}
    raw = json.dumps(request, ensure_ascii=False)
    assert "persona" not in request["state"]
    assert "SYNTHETIC_KEY" not in raw
    assert "command" not in request["state"]
    assert seen["kwargs"]["route"] == "direct"
    assert seen["kwargs"]["timeout_ms"] == 1500


@pytest.mark.parametrize("scope", ["api_only", "web"])
def test_radio_planner_accepts_only_high_confidence_choices(scope):
    result = research.decide("topic", env=ENV, transport=lambda *a, **k: _answer(scope, .8))
    assert result.accepted and result.scope == scope


def test_low_confidence_holds_instead_of_guessing_api_only():
    result = research.decide("topic", env=ENV, transport=lambda *a, **k: _answer("web", .79))
    assert result.status == "low_confidence"
    assert not result.accepted


@pytest.mark.parametrize("status", ["missing_key", "timeout", "rate_limited", "network_error"])
def test_provider_failure_never_becomes_api_only(status):
    result = research.decide(
        "topic", env=ENV,
        transport=lambda *a, **k: {"status": status},
    )
    assert result.status == status
    assert result.scope == "unknown"
    assert not result.accepted


def test_private_or_oversize_topic_holds_before_transport():
    for topic in ("secret: abcdefghijkl", "x" * 5000):
        result = research.decide(
            topic, env=ENV,
            transport=lambda *a, **k: pytest.fail("transport"),
        )
        assert result.status in {"private_or_invalid_input", "input_limit"}
        assert not result.accepted


def test_model_cannot_invent_label_or_provider():
    bad = {
        "status": "ok",
        "data": {"answers": {"radio_evidence": {"choice": "opencode", "confidence": .99}}},
    }
    result = research.decide("topic", env=ENV, transport=lambda *a, **k: bad)
    assert result.status == "invalid_response"


def test_configured_route_chain_does_not_trigger_hidden_second_call():
    calls = []
    env = {**ENV, "DOCICH_JEV_ROUTE": "direct,vercel"}
    result = research.decide(
        "topic", env=env,
        transport=lambda request, **kwargs: calls.append(kwargs["route"]) or _answer("web", .9),
    )
    assert result.accepted
    assert calls == ["direct"]


def test_plan_and_collect_api_only_never_searches():
    result = research.plan_and_collect(
        "場面転換のつなぎトーク",
        ["should not run"],
        env=ENV,
        transport=lambda *a, **k: _answer("api_only", .95),
        material_collector=lambda *a, **k: pytest.fail("web collection"),
    )
    assert result.ok
    assert result.scope == "api_only"
    assert result.materials == ()


def test_plan_and_collect_web_uses_only_trusted_material_queries():
    queries = ["半導体 最新 技術"]
    seen = {}

    def collect(received, *, env, timeout_sec, clock):
        seen["queries"] = received
        seen["env"] = env
        assert 0 < timeout_sec <= 45
        return VerifiedWebBundle("ok", (_material(),), received)

    result = research.plan_and_collect(
        "最近の半導体技術を解説",
        queries,
        env=ENV,
        transport=lambda *a, **k: _answer("web", .94),
        material_collector=collect,
    )
    assert result.ok and result.scope == "web"
    assert seen["queries"] == tuple(queries)
    assert seen["env"] is ENV
    assert result.materials[0].url == "https://example.org/chip"


def test_plan_and_collect_holds_without_web_or_opencode_fallback():
    called = []
    result = research.plan_and_collect(
        "最近のニュース",
        ["latest"],
        env=ENV,
        transport=lambda *a, **k: _answer("web", .4),
        material_collector=lambda *a, **k: called.append(1),
    )
    assert not result.ok
    assert result.status == "low_confidence"
    assert called == []


def test_plan_and_collect_rejects_untrusted_query_shape_after_web_decision():
    result = research.plan_and_collect(
        "最近のニュース",
        [{"query": "untrusted dict"}],
        env=ENV,
        transport=lambda *a, **k: _answer("web", .95),
        material_collector=lambda *a, **k: pytest.fail("collector"),
    )
    assert result.status == "invalid_material_plan"
    assert result.materials == ()



@pytest.mark.parametrize("timeout", [0, -1, 46, float("inf"), float("nan"), True, "45"])
def test_invalid_deadline_never_calls_jev_or_collector(timeout):
    result = research.plan_and_collect("topic", ["query"], env=ENV, timeout_sec=timeout,
        transport=lambda *a, **k: pytest.fail("JEV"),
        material_collector=lambda *a, **k: pytest.fail("collector"))
    assert result.status == "invalid_config" and not result.ok


@pytest.mark.parametrize("confidence", [True, -1, 1.01, float("inf"), float("nan"), None, "0.9"])
def test_invalid_confidence_cannot_authorize_collection(confidence):
    result = research.plan_and_collect("topic", ["query"], env=ENV,
        transport=lambda *a, **k: _answer("web", confidence),
        material_collector=lambda *a, **k: pytest.fail("collector"))
    assert result.status == "invalid_response" and not result.ok
    assert not research.Decision("web", "jev", confidence).accepted


@pytest.mark.parametrize("queries", [[], ["one", "two", "three", "four"],
    ["secret: abcdefghijk"], ["x" * 257], [123], "query"])
def test_shared_query_limits_hold_before_search(queries):
    result = research.plan_and_collect("topic", queries, env=ENV,
        transport=lambda *a, **k: _answer(),
        material_collector=lambda *a, **k: pytest.fail("collector"))
    assert result.status == "invalid_material_plan" and not result.ok


def test_query_plan_is_not_derived_from_model_output():
    model = _answer()
    model["data"]["answers"]["radio_evidence"].update(query="do not use", provider="opencode", url="https://evil.example")
    seen = []
    def collect(queries, **kwargs):
        seen.append(queries)
        return VerifiedWebBundle("ok", (_material(indexes=(0,)),), queries)
    result = research.plan_and_collect("topic", [" caller   query ", "caller query"], env=ENV,
        transport=lambda *a, **k: model, material_collector=collect)
    assert result.ok and seen == [("caller query",)]


def test_classification_and_collection_share_one_deadline():
    now, seen = [100.], []
    def transport(request, **kwargs):
        assert kwargs["timeout_ms"] <= 1500
        now[0] = 143.
        return _answer()
    def collect(queries, **kwargs):
        seen.append(kwargs["timeout_sec"])
        return VerifiedWebBundle("ok", (_material(),), queries)
    result = research.plan_and_collect("topic", ["query"], env=ENV,
        transport=transport, material_collector=collect, clock=lambda: now[0])
    assert result.ok and seen == [2.]


def test_exhausted_classifier_deadline_holds_before_collection():
    now = [100.]
    def transport(*a, **k):
        now[0] = 146.
        return _answer()
    result = research.plan_and_collect("topic", ["query"], env=ENV, transport=transport,
        material_collector=lambda *a, **k: pytest.fail("collector"), clock=lambda: now[0])
    assert result.status == "timeout" and not result.ok


def test_submillisecond_budget_never_starts_jev():
    result = research.plan_and_collect("topic", ["query"], env=ENV, timeout_sec=.0001,
        transport=lambda *a, **k: pytest.fail("JEV"), clock=lambda: 0.)
    assert result.status == "timeout"


def test_late_collection_is_not_reported_as_success():
    now = [100.]
    def collect(queries, **kwargs):
        now[0] = 146.
        return VerifiedWebBundle("ok", (_material(),), queries)
    result = research.plan_and_collect("topic", ["query"], env=ENV,
        transport=lambda *a, **k: _answer(), material_collector=collect, clock=lambda: now[0])
    assert result.status == "timeout" and result.materials == ()


@pytest.mark.parametrize("material", [
    replace(_material(), url=None), replace(_material(), url="http://example.org/chip"),
    replace(_material(), body_sha256="z" * 64), replace(_material(), text_sha256="0"),
    replace(_material(), excerpt_sha256="0" * 64), replace(_material(), excerpt=""),
    replace(_material(), excerpt="x" * 8193), replace(_material(), query_indexes=()),
    replace(_material(), query_indexes=(True,)), replace(_material(), query_indexes=(1,)),
    replace(_material(), query_indexes=(0, 0)), replace(_material(), query_indexes=[0]),
])
def test_malformed_shared_material_is_not_evidence(material):
    result = research.plan_and_collect("topic", ["query"], env=ENV,
        transport=lambda *a, **k: _answer(),
        material_collector=lambda *a, **k: VerifiedWebBundle("ok", (material,), ("query",)))
    assert result.status == "material_unavailable" and result.materials == ()


@pytest.mark.parametrize("bundle", [
    {"items": [{"url": "https://example.org", "description": "search snippet"}]},
    VerifiedWebBundle("unavailable"), VerifiedWebBundle("ok", (_material(),), ("other query",)),
    VerifiedWebBundle("ok", (_material(), _material()), ("query",)),
])
def test_search_snippets_and_bad_bundles_are_not_material(bundle):
    result = research.plan_and_collect("topic", ["query"], env=ENV,
        transport=lambda *a, **k: _answer(), material_collector=lambda *a, **k: bundle)
    assert result.status == "material_unavailable"


def test_missing_query_coverage_remains_partial_with_verified_items():
    result = research.plan_and_collect("topic", ["first", "second"], env=ENV,
        transport=lambda *a, **k: _answer(),
        material_collector=lambda *a, **k: VerifiedWebBundle("ok", (_material(indexes=(0,)),), ("first", "second")))
    assert result.status == "partial" and result.ok and len(result.materials) == 1


def test_real_shared_collector_only_search_fetch_io_is_synthetic(monkeypatch):
    from docich import web_material
    from docich.reply_research_web import Receipt
    calls, fetched, decisions = [], [], []
    text = "取得した公式本文です。"
    receipt = Receipt("https://example.org/source", "a" * 32, "b" * 64,
                      hashlib.sha256(text.encode()).hexdigest(), text)
    def search(query, timeout, *, env):
        calls.append((query, timeout, env.get("DOCICH_REPLY_WEB_SEARCH_BACKEND")))
        return [receipt.url]
    def fetch(self, url):
        assert url in self._candidates
        fetched.append(url); return receipt
    monkeypatch.setattr(web_material, "search_public", search)
    monkeypatch.setattr(web_material.WebBroker, "fetch", fetch)
    def transport(request, **kwargs):
        decisions.append(request); return _answer()
    env = {**ENV, "DOCICH_REPLY_WEB_SEARCH_BACKEND": "cloudflare"}
    result = research.plan_and_collect("公開仕様の解説", ["公式公開仕様"], env=env, transport=transport)
    assert result.ok and len(decisions) == len(calls) == len(fetched) == 1
    assert calls[0][0] == "公式公開仕様" and calls[0][2] == "cloudflare"
    item = result.materials[0]
    assert item.excerpt == text and item.body_sha256 == receipt.sha256
    assert item.text_sha256 == receipt.text_sha256 and item.query_indexes == (0,)


def test_shared_search_failure_does_not_start_cli_or_generation(monkeypatch):
    from docich import web_material
    calls = []
    def search(*a, **k):
        calls.append(True); raise RuntimeError("SYNTHETIC_PROVIDER_SECRET")
    monkeypatch.setattr(web_material, "search_public", search)
    result = research.plan_and_collect("topic", ["query"], env=ENV,
        transport=lambda *a, **k: _answer())
    assert calls == [True] and result.status == "material_unavailable"
    assert "SECRET" not in repr(result) and result.materials == ()


@pytest.mark.parametrize("budget", [.0001, .01, .049])
def test_below_transport_minimum_budget_is_timeout_without_post(budget):
    result = research.plan_and_collect("topic", ["query"], env=ENV, timeout_sec=budget,
        transport=lambda *a, **k: pytest.fail("JEV"), clock=lambda: 0.)
    assert result.status == "timeout"


@pytest.mark.parametrize("timeout", [0, 1, 49, 1501, True])
def test_decide_timeout_matches_default_transport_bounds(timeout):
    decision = research.decide("topic", env=ENV, timeout_ms=timeout,
        transport=lambda *a, **k: pytest.fail("JEV"))
    assert decision.status == "invalid_config"


def test_default_transport_strict_schema_and_selected_key_only(monkeypatch):
    from docich.semantic_decision import transport
    from docich.semantic_decision.routes import resolve_route
    calls = []
    def worker(argv, *, data, timeout, env):
        request = json.loads(data)
        calls.append((request, argv))
        assert request["state"] == {"topic": "半導体の公開仕様"}
        assert set(request["questions"]) == {"radio_evidence"}
        assert set(env) == {"TYPESAFE_API_KEY", "LANG"}
        assert env["TYPESAFE_API_KEY"] == "SYNTHETIC_KEY" and 0 < timeout <= 1.5
        return json.dumps({"status": "ok", "data": {
            "model": resolve_route("direct").resolved_models[0],
            "usage": {"input_tokens": 10, "output_tokens": 0},
            "answers": {"radio_evidence": {"type": "choice", "choice": "web", "confidence": .95,
                "probabilities": {"api_only": .05, "web": .95}}}}}).encode()
    monkeypatch.setattr(transport, "_bounded_process", worker)
    env = {**ENV, "DOCICH_JEV_ROUTE": "direct,vercel", "DOCICH_DISCORD_TOKEN": "SYNTHETIC_BOT",
           "DOCICH_REPLY_WEB_SEARCH_CLOUDFLARE_API_TOKEN": "SYNTHETIC_SEARCH"}
    decision = research.decide("半導体の公開仕様", env=env)
    assert decision.accepted and decision.scope == "web" and len(calls) == 1

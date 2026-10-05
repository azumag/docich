from __future__ import annotations

import json

import pytest

from docich.radio import research
from docich.radio.contracts import MaterialQuery, WebMaterial


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
    assert "persona" not in raw.lower()
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
        [MaterialQuery("topic", "should not run")],
        env=ENV,
        transport=lambda *a, **k: _answer("api_only", .95),
        material_collector=lambda *a, **k: pytest.fail("web collection"),
    )
    assert result.ok
    assert result.scope == "api_only"
    assert result.materials == ()


def test_plan_and_collect_web_uses_only_trusted_material_queries():
    queries = [MaterialQuery("news", "半導体 最新 技術")]
    seen = {}

    def collect(received, *, env):
        seen["queries"] = received
        seen["env"] = env
        return (
            WebMaterial("news", "https://example.org/chip", "a" * 64, "verified excerpt"),
        )

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
        [MaterialQuery("news", "latest")],
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
        ["raw string is not a MaterialQuery"],
        env=ENV,
        transport=lambda *a, **k: _answer("web", .95),
        material_collector=lambda *a, **k: pytest.fail("collector"),
    )
    assert result.status == "invalid_material_plan"
    assert result.materials == ()

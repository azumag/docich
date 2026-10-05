from __future__ import annotations

import json

import pytest

from docich.radio import research


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

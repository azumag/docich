import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from docich.semantic_decision.client import SemanticDecisionClient
from docich.semantic_decision.contracts import (
    ChoiceQuestion,
    DecisionRequest,
    RouteProfile,
    SemanticDecisionError,
    TransportResult,
)
from docich.semantic_decision.metrics import append_event, build_event
from docich.semantic_decision.jev import CRITERIA, build_request as build_jev_request
from docich.semantic_decision.routes import resolve_route, resolve_timeout_ms
from docich.semantic_decision.transport import (
    NoRedirect,
    bounded_process,
    request_once,
)
from docich.semantic_decision.validator import strict_loads, validate_response


def _request(model="jev-1.13.0"):
    return DecisionRequest(
        purpose="comment-classification",
        model=model,
        payload={
            "model": model,
            "state": {"comments": [{"index": 1, "text": "hello"}]},
            "questions": {"intent": {"type": "choice", "choices": ["yes", "no"]}},
        },
        questions=(ChoiceQuestion("intent", ("yes", "no")),),
    )


def _response(model="jev-1.13.0"):
    return {
        "model": model,
        "answers": {
            "intent": {
                "type": "choice",
                "choice": "yes",
                "confidence": 0.9,
                "probabilities": {"yes": 0.9, "no": 0.1},
            }
        },
        "usage": {"input_tokens": 10, "output_tokens": 4},
    }


def test_route_profiles_are_fixed_and_credentials_are_route_specific():
    direct = resolve_route({"DOCICH_JEV_ROUTE": "direct"})
    assert direct.endpoint == "https://api.typesafe.ai/v1/systemone"
    assert direct.requested_model == "jev-1.13.0"
    assert direct.credential_env == "TYPESAFE_API_KEY"

    vercel = resolve_route({"DOCICH_JEV_ROUTE": "vercel"})
    assert vercel.endpoint == "https://ai-gateway.vercel.sh/typesafe/v1/systemone"
    assert vercel.requested_model == "typesafe-ai/jev"
    assert vercel.credential_env == "DOCICH_JEV_VERCEL_API_KEY"

    overridden = resolve_route(
        {
            "DOCICH_JEV_ROUTE": "direct",
            "DOCICH_JEV_ENDPOINT": "https://attacker.invalid",
            "DOCICH_JEV_MODEL": "attacker-model",
        }
    )
    assert overridden == direct


def test_invalid_route_backend_and_timeout_fail_closed():
    with pytest.raises(SemanticDecisionError):
        resolve_route({"DOCICH_JEV_ROUTE": "arbitrary"})
    with pytest.raises(SemanticDecisionError):
        resolve_route({"DOCICH_SEMANTIC_BACKEND": "other"})
    with pytest.raises(SemanticDecisionError):
        resolve_timeout_ms({"DOCICH_JEV_TIMEOUT_MS": "49"})
    with pytest.raises(SemanticDecisionError):
        resolve_timeout_ms({"DOCICH_JEV_TIMEOUT_MS": "5001"})


def test_request_projection_is_strict_json_and_bounded():
    with pytest.raises(SemanticDecisionError):
        DecisionRequest(
            purpose="comment-classification",
            model="jev-1.13.0",
            payload={"bad": float("nan")},
            questions=(ChoiceQuestion("intent", ("yes", "no")),),
        )


def test_jev_projection_sends_only_indexed_text_and_fixed_rubric():
    request = build_jev_request(
        [{"comment": "hello", "user": "not-for-provider", "category": "secret"}],
        "jev-1.13.0",
    )
    assert request.purpose == "jev-category"
    assert request.payload["state"] == {"comments": [{"index": 1, "text": "hello"}]}
    assert request.payload["questions"]["c1"]["criteria"] == CRITERIA
    assert "not-for-provider" not in json.dumps(request.payload)
    assert "secret" not in json.dumps(request.payload)
    with pytest.raises(SemanticDecisionError):
        DecisionRequest(
            purpose="comment-classification",
            model="jev-1.13.0",
            payload={"body": "x" * 40000},
            questions=(ChoiceQuestion("intent", ("yes", "no")),),
        )


def test_strict_json_rejects_duplicate_and_nonfinite_values():
    with pytest.raises(SemanticDecisionError, match="duplicate"):
        strict_loads('{"a": 1, "a": 2}')
    with pytest.raises(SemanticDecisionError, match="non-finite"):
        strict_loads('{"a": NaN}')


def test_validator_accepts_only_expected_answer_schema():
    result = validate_response(_response(), _request())
    assert result["answers"]["intent"]["choice"] == "yes"
    assert result["usage"] == {"input_tokens": 10, "output_tokens": 4}

    invalid = _response()
    invalid["unexpected"] = "no"
    with pytest.raises(SemanticDecisionError):
        validate_response(invalid, _request())

    invalid = _response()
    invalid["answers"]["intent"]["choice"] = "maybe"
    with pytest.raises(SemanticDecisionError):
        validate_response(invalid, _request())

    invalid = _response()
    invalid["answers"]["intent"]["confidence"] = True
    with pytest.raises(SemanticDecisionError):
        validate_response(invalid, _request())

    invalid = _response()
    invalid["answers"]["intent"]["probabilities"]["no"] = 0.2
    with pytest.raises(SemanticDecisionError):
        validate_response(invalid, _request())

    invalid = _response()
    invalid["usage"]["input_tokens"] = True
    with pytest.raises(SemanticDecisionError):
        validate_response(invalid, _request())


def test_validator_rejects_model_and_answer_id_mismatch():
    with pytest.raises(SemanticDecisionError):
        validate_response(_response("jev-1.12.0"), _request())
    invalid = _response()
    invalid["answers"]["other"] = invalid["answers"].pop("intent")
    with pytest.raises(SemanticDecisionError):
        validate_response(invalid, _request())


def test_client_uses_only_selected_route_credential_and_redacts_metrics():
    calls = []

    def transport(payload, profile, credential, timeout_s):
        calls.append((payload, profile, credential, timeout_s))
        return TransportResult("ok", attempted=True, data=_response(profile.requested_model))

    client = SemanticDecisionClient(
        env={
            "DOCICH_JEV_ROUTE": "vercel",
            "TYPESAFE_API_KEY": "direct-secret",
            "DOCICH_JEV_VERCEL_API_KEY": "vercel-secret",
        },
        transport=transport,
        clock=iter((10.0, 10.125)).__next__,
    )
    result = client.decide(_request("typesafe-ai/jev"))

    assert result.status == "ok"
    assert calls[0][1].name == "vercel"
    assert calls[0][2] == "vercel-secret"
    assert calls[0][3] == 1.5
    assert "vercel-secret" not in json.dumps(result.metric)
    assert result.metric["cost_usd"] is None


def test_client_does_not_call_transport_without_key_or_with_wrong_model():
    calls = []

    def transport(*args):
        calls.append(args)
        raise AssertionError("transport must not be called")

    client = SemanticDecisionClient(env={}, transport=transport)
    assert client.decide(_request()).status == "missing_key"
    assert client.decide(_request("wrong-model")).status == "invalid_config"
    assert calls == []


def test_client_converts_invalid_provider_response_to_fail_closed_status():
    def transport(*_args):
        return TransportResult("ok", attempted=True, data={"model": "bad"})

    result = SemanticDecisionClient(
        env={"TYPESAFE_API_KEY": "key"}, transport=transport
    ).decide(_request())
    assert result.status == "invalid_response"
    assert result.data is None


def test_transport_makes_one_attempt_and_rejects_untrusted_profile():
    profile = resolve_route({})
    calls = []

    def runner(*args, **kwargs):
        calls.append((args, kwargs))
        return json.dumps({"status": "rate_limited", "retry_after": 42}).encode()

    result = request_once({"model": profile.requested_model}, profile, "key", 1.5, runner=runner)
    assert result.status == "rate_limited"
    assert result.attempted is True
    assert result.retry_after == 42
    assert len(calls) == 1

    untrusted = RouteProfile("direct", "https://attacker.invalid", "bad", "KEY")
    assert request_once({}, untrusted, "key", 1.5).status == "invalid_config"


def test_transport_handles_child_timeout_and_request_limit():
    profile = resolve_route({})
    calls = []

    def runner(*args, **kwargs):
        calls.append(1)
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout_s"])

    result = request_once({"model": profile.requested_model}, profile, "key", 1.5, runner=runner)
    assert result.status == "timeout"
    assert len(calls) == 1
    assert request_once({"body": "x" * 40000}, profile, "key", 1.5, runner=runner).status == "input_limit"
    assert len(calls) == 1


def test_bounded_process_kills_and_reaps_stalled_child():
    with pytest.raises(subprocess.TimeoutExpired):
        bounded_process(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            data=b"",
            timeout_s=0.05,
            env={"LANG": "C.UTF-8"},
        )


def test_no_redirect_handler_never_follows_location():
    assert NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://evil") is None


def test_metrics_are_bounded_and_secret_free(tmp_path):
    request = _request()
    profile = resolve_route({})
    event = build_event(
        request,
        profile,
        status="ok",
        latency_ms=12.3456,
        resolved_model=profile.requested_model,
        usage={"input_tokens": 1, "output_tokens": 2},
    )
    assert event["latency_ms"] == 12.346
    assert event["cost_usd"] is None
    assert "secret" not in json.dumps(event)
    path = tmp_path / "metrics.jsonl"
    assert append_event(path, event) is True
    assert path.stat().st_mode & 0o077 == 0

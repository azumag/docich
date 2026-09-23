"""Owner-only Jev route canary contracts (#882). No network: the core is mocked,
except where the real core must refuse before any child/HTTP (missing key)."""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docich.comment_classifier import canary, jev  # noqa: E402
from docich.semantic_decision.routes import resolve_route  # noqa: E402

DIRECT_KEY = "SYNTHETIC_DIRECT_KEY_0123456789"
VERCEL_KEY = "SYNTHETIC_VERCEL_KEY_0123456789"
ENV = {"TYPESAFE_API_KEY": DIRECT_KEY, "DOCICH_JEV_VERCEL_API_KEY": VERCEL_KEY}


def ok_core(seen, *, choice_for=None, model_for=None):
    def request_once(request, *, route, env, timeout_ms):
        seen.append((route, request, timeout_ms))
        answers = {}
        for index, (_, expected) in enumerate(canary.FIXTURE, 1):
            choice = (choice_for or {}).get(index, expected)
            labels = request["questions"][f"c{index}"]["criteria"]
            answers[f"c{index}"] = {"type": "choice", "choice": choice, "confidence": 0.9,
                                    "probabilities": {k: (1.0 if k == choice else 0.0) for k in labels}}
        model = (model_for or {}).get(route, resolve_route(route).requested_model)
        data = {"model": model, "answers": answers, "usage": {"input_tokens": 10, "output_tokens": 4}}
        return {"status": "ok", "data": data,
                "meta": {"route": route, "resolved_model": model, "latency_ms": 12.5,
                         "usage": data["usage"], "cost_usd": None}}
    return request_once


def run(argv, core, env=ENV):
    out = io.StringIO()
    with redirect_stdout(out):
        code = canary.main(argv, env=env, request_once=core)
    return code, out.getvalue()


def test_same_fixture_over_both_routes_with_each_routes_own_model():
    seen = []
    code, out = run(["--route", "direct", "--route", "vercel"], ok_core(seen))
    assert code == 0
    report = json.loads(out)
    assert [r["route"] for r in report["routes"]] == ["direct", "vercel"]
    assert [r["requested_model"] for r in report["routes"]] == ["jev-1.13.0", "typesafe-ai/jev"]
    assert [r["agreement"] for r in report["routes"]] == ["4/4", "4/4"]
    assert all(r["usage"] == {"input_tokens": 10, "output_tokens": 4} for r in report["routes"])
    assert all(r["cost_usd_available"] is False for r in report["routes"])
    # Identical synthetic state, each route's own requested model, max bounded timeout.
    (_, direct_request, t1), (_, vercel_request, t2) = seen
    assert direct_request["state"] == vercel_request["state"]
    assert direct_request["model"] == "jev-1.13.0" and vercel_request["model"] == "typesafe-ai/jev"
    assert direct_request == jev.build_request([{"comment": t} for t, _ in canary.FIXTURE], "jev-1.13.0")
    assert t1 == t2 == canary.CANARY_TIMEOUT_MS == 5000


def test_report_carries_no_credential_or_comment_text():
    code, out = run(["--route", "direct", "--route", "vercel"], ok_core([]))
    assert code == 0
    assert DIRECT_KEY not in out and VERCEL_KEY not in out
    for text, _ in canary.FIXTURE:
        assert text not in out


def test_disagreement_is_reported_not_a_failure():
    code, out = run(["--route", "vercel"], ok_core([], choice_for={4: "other"}))
    assert code == 0
    route = json.loads(out)["routes"][0]
    assert route["agreement"] == "3/4"
    assert route["answers"][3] == {"index": 4, "expected": "chitchat", "choice": "other",
                                   "confidence": 0.9, "agrees": False}


def test_any_non_ok_route_fails_the_canary_without_answers():
    def core(request, *, route, env, timeout_ms):
        if route == "vercel":
            return {"status": "invalid_response",
                    "meta": {"route": route, "resolved_model": None, "latency_ms": 80.0,
                             "usage": None, "cost_usd": None}}
        return ok_core([])(request, route=route, env=env, timeout_ms=timeout_ms)
    code, out = run(["--route", "direct", "--route", "vercel"], core)
    assert code == 1
    direct, vercel = json.loads(out)["routes"]
    assert direct["status"] == "ok" and "answers" in direct
    assert vercel["status"] == "invalid_response" and "answers" not in vercel


def test_missing_key_is_refused_by_the_real_core_before_any_request():
    code, out = run(["--route", "vercel"], canary.transport.request_once,
                    env={"TYPESAFE_API_KEY": DIRECT_KEY})
    assert code == 1
    route = json.loads(out)["routes"][0]
    assert route["status"] == "missing_key"
    assert route["credential_env"] == "DOCICH_JEV_VERCEL_API_KEY"
    assert route["credential_present"] is False


def test_a_credential_echoed_into_the_report_is_refused():
    code, out = run(["--route", "vercel"], ok_core([], model_for={"vercel": VERCEL_KEY}))
    assert code == 1
    assert VERCEL_KEY not in out
    assert json.loads(out) == {"status": "refused_credential_in_report"}

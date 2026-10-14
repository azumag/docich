"""Owner-only real-API canary for the Jev routes (#882 "実API canary").

    python -m docich.comment_classifier.canary --route direct --route vercel

Sends the same fixed synthetic Japanese batch over each selected route through
the classifier's own request builder and the shared core's only HTTP path, and
prints one sanitised JSON report. Nothing here reads or writes production
state, live comments, metrics or the cooldown gate. A route passes only when
the core returned ``ok``, meaning HTTP 200 and the strict answer schema
(choice/confidence/probabilities/usage and the route's reviewed resolved
model) all validated. A schema difference fails closed as ``invalid_response``
and must be reviewed as a route contract, never patched over here.

The report never contains a credential, a response body, headers or error
text: only status enums, model names, latency, usage counts and, per synthetic
comment, the expected label, the chosen label and its confidence.
"""
from __future__ import annotations

import argparse
import os
import sys

from docich.semantic_decision import transport
from docich.semantic_decision.routes import resolve_route
from docich.semantic_decision.validator import dumps

from . import jev

# Fixed synthetic inputs; never live chat. Expectations are the rubric's
# unambiguous reading, reported as agreement, not as a pass/fail gate.
FIXTURE = (
    ("音声が聞こえません", "stream_bug_report"),
    ("歌ってください！", "sing_request"),
    ("このゲームのルールを教えて", "game_question"),
    ("こんばんは〜", "chitchat"),
)
CANARY_TIMEOUT_MS = 5000


def run_route(route, env, *, request_once=transport.request_once):
    profile = resolve_route(route)
    rows = [{"comment": text} for text, _ in FIXTURE]
    request = jev.build_request(rows, profile.requested_model)
    result = request_once(request, route=route, env=env, timeout_ms=CANARY_TIMEOUT_MS)
    meta = result.get("meta") or {}
    report = {
        "route": route,
        "status": result.get("status"),
        "requested_model": profile.requested_model,
        "resolved_model": meta.get("resolved_model"),
        "latency_ms": meta.get("latency_ms"),
        "usage": meta.get("usage"),
        "cost_usd_available": meta.get("cost_usd") is not None,
        "credential_env": profile.credential_env,
        "credential_present": transport._valid_key(env.get(profile.credential_env, "")),
    }
    if "retry_after" in result:
        report["retry_after"] = result["retry_after"]
    data = result.get("data")
    if result.get("status") == "ok" and isinstance(data, dict):
        answers = []
        for index, (_, expected) in enumerate(FIXTURE, 1):
            answer = data["answers"][f"c{index}"]
            answers.append({"index": index, "expected": expected, "choice": answer["choice"],
                            "confidence": answer["confidence"],
                            "agrees": answer["choice"] == expected})
        report["answers"] = answers
        report["agreement"] = f"{sum(a['agrees'] for a in answers)}/{len(answers)}"
    return report


def _secrets(env):
    names = {resolve_route(name).credential_env for name in ("direct", "vercel")}
    # Very short values would match unrelated report text; real keys are long.
    return [env[name] for name in names if len(env.get(name) or "") >= 8]


def main(argv=None, *, env=None, request_once=transport.request_once) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--route", action="append", choices=("direct", "vercel"), required=True)
    args = parser.parse_args(argv)
    env = dict(os.environ if env is None else env)
    reports = [run_route(route, env, request_once=request_once) for route in dict.fromkeys(args.route)]
    output = dumps({"schema_version": 1, "fixture_size": len(FIXTURE), "routes": reports})
    if any(secret in output for secret in _secrets(env)):
        print('{"status":"refused_credential_in_report"}')
        return 1
    print(output)
    return 0 if all(r["status"] == "ok" for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())

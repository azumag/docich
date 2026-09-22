"""Keyless, mock-only transport contracts, including real process cancellation."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import signal
import ssl
import subprocess
import sys
import time
import urllib.error
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from docich.semantic_decision import transport as t
from docich.semantic_decision.routes import resolve_route
from docich.semantic_decision.validator import (
    MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, dumps, encode_request, strict_json,
    validate_response,
)

FIXTURE = json.loads((ROOT / "tests/fixtures/semantic_choice_direct.json").read_text())
KEYS = {"TYPESAFE_API_KEY": "DIRECT_SECRET_SENTINEL",
        "DOCICH_JEV_VERCEL_API_KEY": "VERCEL_SECRET_SENTINEL"}


def example(route="direct"):
    request, response = deepcopy(FIXTURE["request"]), deepcopy(FIXTURE["response"])
    request["model"] = response["model"] = resolve_route(route).requested_model
    return request, response


def worker_reply(response):
    return dumps({"status": "ok", "data": response}).encode()


def test_fixed_direct_golden_and_idempotent_validation():
    req, response = example()
    profile = resolve_route()
    assert profile.endpoint == "https://api.typesafe.ai/v1/systemone"
    assert profile.requested_model == "jev-1.13.0"
    assert profile.credential_env == "TYPESAFE_API_KEY"
    clean = validate_response(response, req, profile)
    assert clean == response
    assert validate_response(clean, req, profile) == clean
    assert json.loads(encode_request(req, profile)) == FIXTURE["request"]


def test_criteria_are_per_question_not_comment_global():
    req = {"model": "jev-1.13.0", "state": "synthetic", "questions": {
        "gate": {"type": "choice", "instructions": "gate", "criteria": {"yes": "yes", "no": "no"}},
        "route": {"type": "choice", "instructions": "route", "criteria": {"a": "A", "b": "B"}},
    }}
    response = {"model": "jev-1.13.0", "answers": {
        "gate": {"type": "choice", "choice": "yes", "confidence": .9, "probabilities": {"yes": .9, "no": .1}},
        "route": {"type": "choice", "choice": "a", "confidence": .8, "probabilities": {"a": .8, "b": .2}},
    }, "usage": {"input_tokens": 2, "output_tokens": 1}}
    assert validate_response(response, req, resolve_route()) == response


@pytest.mark.parametrize("raw", [
    '{"model":"x","model":"y"}', '{"a":{"b":1,"b":2}}',
    '{"p":NaN}', '{"p":Infinity}', '{"p":-Infinity}', '{',
])
def test_strict_json_rejects_invalid_raw(raw):
    with pytest.raises(ValueError):
        strict_json(raw)


@pytest.mark.parametrize("path,value", [
    (("model",), "jev-latest"), (("model",), None),
    (("answers",), {}), (("answers", "c1", "type"), "noul"),
    (("answers", "c1", "choice"), "UNKNOWN"), (("answers", "c1", "choice"), []),
    (("answers", "c1", "confidence"), True), (("answers", "c1", "confidence"), "0.9"),
    (("answers", "c1", "confidence"), -0.1), (("answers", "c1", "confidence"), 1.1),
    (("answers", "c1", "confidence"), float("inf")),
    (("answers", "c1", "confidence"), float("nan")),
    (("answers", "c1", "probabilities"), {}),
    (("answers", "c1", "probabilities", "other"), True),
    (("answers", "c1", "probabilities", "other"), .8),
    (("answers", "c1", "probabilities", "other"), -1),
    (("usage",), None), (("usage", "input_tokens"), True),
    (("usage", "input_tokens"), 1.0), (("usage", "output_tokens"), -1),
    (("usage", "output_tokens"), 10**9 + 1),
])
def test_response_schema_is_fail_closed(path, value):
    req, response = example()
    target = response
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises((ValueError, TypeError)):
        validate_response(response, req, resolve_route())


@pytest.mark.parametrize("field", ["type", "choice", "confidence", "probabilities"])
def test_missing_answer_field_is_not_synthesised(field):
    req, response = example()
    del response["answers"]["c1"][field]
    with pytest.raises(ValueError):
        validate_response(response, req, resolve_route())


def test_unknown_ids_labels_and_nonwinning_choice_are_rejected():
    req, response = example()
    for mutation in (lambda r: r["answers"].update(c3=r["answers"]["c1"]),
                     lambda r: r["answers"]["c1"]["probabilities"].update(unknown=0),
                     lambda r: r["answers"]["c1"].update(choice="chitchat")):
        candidate = deepcopy(response)
        mutation(candidate)
        with pytest.raises(ValueError):
            validate_response(candidate, req, resolve_route())


@pytest.mark.parametrize("route", ["direct", "vercel"])
def test_one_transport_only_selected_secret_and_no_input_projection_change(route):
    req, response = example(route)
    profile = resolve_route(route)
    env = {**KEYS, "HTTPS_PROXY": "PRIVATE_PROXY", "VERCEL_API_KEY": "GENERATION_KEY",
           "PYTHONPATH": "UNTRUSTED", "DOCICH_JEV_ENDPOINT": "https://invalid.example",
           "COMMENT_CLASSIFIER_JEV_MODEL": "unreviewed"}
    with patch.object(t, "_bounded_process", return_value=worker_reply(response)) as spawn:
        result = t.request_once(req, route=route, env=env)
    assert result["status"] == "ok"
    assert spawn.call_count == 1
    args, kwargs = spawn.call_args
    assert kwargs["env"] == {profile.credential_env: KEYS[profile.credential_env], "LANG": "C.UTF-8"}
    assert args[0][1] == "-I"
    assert args[0][-2] == route
    assert 0 < kwargs["timeout"] <= 1.5
    assert json.loads(kwargs["data"]) == req
    for secret in (*KEYS.values(), "PRIVATE_PROXY", "GENERATION_KEY", "UNTRUSTED"):
        assert secret not in repr(args)
        assert secret not in kwargs["data"].decode()
        assert secret not in dumps(result)
    assert result["meta"]["cost_usd"] is None
    assert result["meta"]["retry_count"] == 0


def test_route_unset_and_rollback_to_direct_are_explicit():
    for route in (None, "vercel", "direct"):
        req, response = example(route or "direct")
        with patch.object(t, "_bounded_process", return_value=worker_reply(response)) as spawn:
            assert t.request_once(req, route=route, env=KEYS)["status"] == "ok"
        assert spawn.call_args.args[0][-2] == (route or "direct")
    req, response = example("vercel")
    with patch.object(t, "_bounded_process", return_value=worker_reply(response)):
        assert t.request_once(req, env={**KEYS, "DOCICH_JEV_ROUTE": "vercel"})["meta"]["route"] == "vercel"


@pytest.mark.parametrize("route", ["", "DIRECT", "auto", "https://evil.example", [], True])
def test_unknown_route_has_no_child(route):
    with patch.object(t, "_bounded_process") as spawn:
        assert t.request_once(example()[0], route=route, env=KEYS)["status"] == "invalid_config"
    spawn.assert_not_called()


@pytest.mark.parametrize("timeout", [True, 0, 49, 5001, "1500", float("inf")])
def test_invalid_timeout_has_no_child(timeout):
    with patch.object(t, "_bounded_process") as spawn:
        assert t.request_once(example()[0], env=KEYS, timeout_ms=timeout)["status"] == "invalid_config"
    spawn.assert_not_called()


@pytest.mark.parametrize("route,env", [
    ("direct", {"DOCICH_JEV_VERCEL_API_KEY": "OTHER_KEY"}),
    ("vercel", {"TYPESAFE_API_KEY": "OTHER_KEY", "VERCEL_API_KEY": "GENERATION_KEY"}),
    ("direct", {"TYPESAFE_API_KEY": "bad\nkey"}),
    ("direct", {"TYPESAFE_API_KEY": "é"}),
    ("direct", {"TYPESAFE_API_KEY": "x" * 4097}),
])
def test_missing_or_invalid_selected_key_never_uses_another_key(route, env):
    with patch.object(t, "_bounded_process") as spawn:
        assert t.request_once(example(route)[0], route=route, env=env)["status"] == "missing_key"
    spawn.assert_not_called()


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(model="jev-latest"), lambda r: r.update(endpoint="https://evil.example"),
    lambda r: r.update(state="x" * MAX_REQUEST_BYTES),
    lambda r: r["questions"]["c1"].update(type="score"),
    lambda r: r["questions"]["c1"].update(criteria={}),
])
def test_invalid_request_never_spawns(mutation):
    req, _ = example()
    mutation(req)
    with patch.object(t, "_bounded_process") as spawn:
        assert t.request_once(req, env=KEYS)["status"] == "input_limit"
    spawn.assert_not_called()


class Response:
    status = 200

    def __init__(self, raw):
        self.raw = raw

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        assert limit == MAX_RESPONSE_BYTES + 1
        return self.raw[:limit]


@pytest.mark.parametrize("route", ["direct", "vercel"])
def test_http_fixed_origin_tls_redirect_and_proxy_policy(route):
    req, response = example(route)
    with patch.object(t.urllib.request, "build_opener") as build:
        build.return_value.open.return_value = Response(dumps(response).encode())
        assert t._http_worker(req, route, 1.5, KEYS)["status"] == "ok"
    handlers = build.call_args.args
    assert handlers[0].proxies == {}
    assert isinstance(handlers[1], t._NoRedirect)
    assert handlers[1].redirect_request(None, None, 302, "", {}, "https://evil.example") is None
    assert handlers[2]._context.check_hostname
    assert handlers[2]._context.verify_mode == ssl.CERT_REQUIRED
    call = build.return_value.open.call_args
    assert call.args[0].full_url == resolve_route(route).endpoint
    assert call.args[0].get_header("Authorization") == "Bearer " + KEYS[resolve_route(route).credential_env]
    assert call.args[0].method == "POST"
    assert call.kwargs["timeout"] == 1.5
    assert build.return_value.open.call_count == 1


@pytest.mark.parametrize("code,status", [(301, "http_error"), (302, "http_error"),
    (401, "auth_error"), (403, "auth_error"), (429, "rate_limited"),
    (529, "overloaded"), (500, "server_error"), (503, "server_error")])
def test_http_errors_are_sanitised_without_retry(code, status):
    error = urllib.error.HTTPError("https://private.invalid", code, "RAW_SECRET_SENTINEL",
                                   {"Retry-After": "900"}, io.BytesIO(b"BODY_SECRET_SENTINEL"))
    with patch.object(t.urllib.request, "build_opener") as build:
        build.return_value.open.side_effect = error
        result = t._http_worker(example()[0], "direct", 1.5, KEYS)
    assert result == {"status": status, "retry_after": 300}
    assert build.return_value.open.call_count == 1


@pytest.mark.parametrize("error,status", [(TimeoutError("secret"), "timeout"),
    (urllib.error.URLError(TimeoutError("secret")), "timeout"),
    (urllib.error.URLError("secret"), "network_error"), (RuntimeError("secret"), "network_error")])
def test_network_errors_never_include_raw_detail(error, status):
    with patch.object(t.urllib.request, "build_opener", side_effect=error):
        assert t._http_worker(example()[0], "direct", 1.5, KEYS) == {"status": status}


@pytest.mark.parametrize("raw", [b"not json", b'{"model":1,"model":2}', b"x" * (MAX_RESPONSE_BYTES + 1)])
def test_bad_http_bodies_fail_closed(raw):
    with patch.object(t.urllib.request, "build_opener") as build:
        build.return_value.open.return_value = Response(raw)
        assert t._http_worker(example()[0], "direct", 1.5, KEYS)["status"] == "invalid_response"


@pytest.mark.parametrize("status", sorted(t._ERRORS))
def test_failed_route_never_fails_over_or_exposes_worker_extras(status):
    raw = dumps({"status": status, "detail": "PRIVATE_ERROR", "headers": KEYS}).encode()
    with patch.object(t, "_bounded_process", return_value=raw) as spawn:
        result = t.request_once(example("vercel")[0], route="vercel", env=KEYS)
    assert result["status"] == status
    assert result["meta"]["usage"] is None
    assert "PRIVATE_ERROR" not in dumps(result)
    assert spawn.call_count == 1
    assert spawn.call_args.args[0][-2] == "vercel"


def test_raw_echoes_are_removed_at_parent_boundary():
    req, response = example()
    response["source"] = response["answers"]["c1"]["source"] = "RAW_BODY_SENTINEL"
    response["usage"]["headers"] = KEYS
    with patch.object(t, "_bounded_process", return_value=worker_reply(response)):
        result = t.request_once(req, env=KEYS)
    assert result["status"] == "ok"
    assert "SENTINEL" not in dumps(result)


def test_vercel_resolved_alias_requires_review_not_synthesis():
    req, response = example("vercel")
    response["model"] = "jev-1.13.0"
    with patch.object(t, "_bounded_process", return_value=worker_reply(response)):
        assert t.request_once(req, route="vercel", env=KEYS)["status"] == "invalid_response"


def test_main_thread_requirement_is_fail_closed():
    with patch.object(t.subprocess, "Popen") as spawn, ThreadPoolExecutor(1) as executor:
        result = executor.submit(t.request_once, example()[0], env=KEYS).result()
    assert result["status"] != "ok"
    spawn.assert_not_called()


def test_isolated_worker_bootstrap_without_game_or_key():
    result = subprocess.run([sys.executable, "-I", t.__file__, "--http-worker", "direct", "1.5"],
                            input=dumps(example()[0]).encode(), capture_output=True,
                            env={"LANG": "C.UTF-8"}, timeout=3, check=True)
    assert json.loads(result.stdout) == {"status": "auth_error"}
    assert result.stderr == b""


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group contract")
@pytest.mark.parametrize("phase", ["dns", "connect", "body"])
def test_stalled_network_phase_is_bounded_and_child_reaped(phase, tmp_path):
    marker = tmp_path / "entered"
    code = f'''
import sys, socket, time
from pathlib import Path
sys.path.insert(0, {str(ROOT / 'src')!r})
from docich.semantic_decision import transport as t
request = {example()[0]!r}
def stall(*a, **k):
    Path({str(marker)!r}).write_text({phase!r})
    time.sleep(10)
class R:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def read(self, n): return stall()
class O:
    def open(self, *a, **k):
        if {phase!r} == 'dns':
            socket.getaddrinfo = stall
            socket.getaddrinfo('synthetic.invalid', 443)
        if {phase!r} == 'connect':
            socket.create_connection = stall
            socket.create_connection(('synthetic.invalid', 443))
        return R()
t.urllib.request.build_opener = lambda *a: O()
t._http_worker(request, 'direct', 1.5, {{'TYPESAFE_API_KEY': 'synthetic'}})
'''
    processes = []
    popen = subprocess.Popen

    def capture(*a, **k):
        p = popen(*a, **k)
        processes.append(p)
        return p

    before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    started = time.monotonic()
    with patch.object(t.subprocess, "Popen", side_effect=capture), pytest.raises(subprocess.TimeoutExpired):
        t._bounded_process([sys.executable, "-I", "-c", code], data=b"", timeout=.6, env={"LANG": "C.UTF-8"})
    assert marker.read_text() == phase
    assert time.monotonic() - started < 2
    assert processes[0].poll() is not None
    assert {s: signal.getsignal(s) for s in before} == before


def _running(pid):
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
        return text.rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux live-descendant check")
@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_parent_cancel_kills_group_and_reaps_child(signum, tmp_path):
    ids = tmp_path / "ids"
    child = ("import os,subprocess,sys,time; from pathlib import Path; "
             "p=subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(20)']); "
             f"Path({str(ids)!r}).write_text(str(os.getpid())+' '+str(p.pid)); time.sleep(20)")
    manager = (f"import sys; sys.path.insert(0,{str(ROOT / 'src')!r}); "
               "from docich.semantic_decision.transport import _bounded_process; "
               f"_bounded_process([sys.executable,'-I','-c',{child!r}], data=b'', timeout=5, env={{'LANG':'C.UTF-8'}})")
    p = subprocess.Popen([sys.executable, "-I", "-c", manager], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, env={"LANG": "C.UTF-8"})
    children = []
    try:
        deadline = time.monotonic() + 3
        while not ids.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        children = [int(x) for x in ids.read_text().split()]
        p.send_signal(signum)
        out, err = p.communicate(timeout=2)
        assert p.returncode == 128 + signum
        assert out == err == b""
        assert not any(_running(pid) for pid in children)
    finally:
        if p.poll() is None:
            p.kill()
            p.communicate()
        for pid in children:
            if _running(pid):
                os.kill(pid, signal.SIGKILL)


def test_canonical_timeout_is_centralised_not_legacy_env():
    req, response = example()
    with patch.object(t, "_bounded_process", return_value=worker_reply(response)) as spawn:
        result = t.request_once(req, env={**KEYS, "DOCICH_JEV_TIMEOUT_MS": "250",
                                         "COMMENT_CLASSIFIER_JEV_TIMEOUT_MS": "5000"})
    assert result["status"] == "ok"
    assert 0 < spawn.call_args.kwargs["timeout"] <= .25
    with patch.object(t, "_bounded_process") as spawn:
        result = t.request_once(req, env={**KEYS, "DOCICH_JEV_TIMEOUT_MS": "NaN"})
    assert result["status"] == "invalid_config"
    spawn.assert_not_called()


def test_platform_guard_prevents_unreapable_process():
    with patch.object(t.os, "name", "nt"), patch.object(t, "_bounded_process") as spawn:
        result = t.request_once(example()[0], env=KEYS)
    assert result["status"] == "invalid_config"
    spawn.assert_not_called()


def test_process_creation_consumes_absolute_deadline():
    processes = []
    popen = subprocess.Popen

    def delayed(*a, **k):
        time.sleep(.06)
        p = popen(*a, **k)
        processes.append(p)
        return p

    with patch.object(t.subprocess, "Popen", side_effect=delayed), pytest.raises(subprocess.TimeoutExpired):
        t._bounded_process([sys.executable, "-I", "-c", "import time; time.sleep(5)"],
                           data=b"", timeout=.04, env={"LANG": "C.UTF-8"})
    assert processes[0].poll() is not None

"""Bounded stdin/stdout bridge to the native RADIO script core.

This process prepares a script; it never acknowledges or writes a delivery queue.
The caller still owns persona, quality/fact-check, voice and delivery acceptance.
"""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
import re
import sys

REQUEST_SCHEMA = "radio-script-request-v1"
RESPONSE_SCHEMA = "radio-script-response-v1"
MAX_INPUT_BYTES = 16384
MAX_OUTPUT_BYTES = 131072
_FIELDS = {"schema", "request_id", "topic", "queries"}
_ID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_FAILURES = frozenset({
    "disabled", "invalid_config", "invalid_input", "input_limit", "timeout",
    "missing_key", "rate_limited", "network_error", "server_error", "auth_error",
    "invalid_response", "overloaded", "http_error", "unavailable",
    "generation_failed", "invalid_script", "low_confidence", "material_unavailable",
})


def _gate(env):
    flag = env.get("DOCICH_RADIO_SCRIPT_DIRECT_ENABLED", "0")
    if flag == "0":
        return "disabled"
    if (flag != "1" or env.get("DOCICH_ALLOW_REAL_AI") != "1"
            or env.get("DOCICH_RADIO_RESEARCH_ROUTING_ENABLED") != "1"):
        return "invalid_config"
    return ""


def _response(status, request_id="", scope="unknown"):
    return {
        "schema": RESPONSE_SCHEMA, "request_id": request_id, "status": status,
        "scope": scope, "script": None, "materials": [], "delivery": "not_requested",
    }


def _object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate_key")
        obj[key] = value
    return obj


def _constant(_value):
    raise ValueError("non_json_constant")


def _request(raw):
    if type(raw) is not bytes or len(raw) > MAX_INPUT_BYTES:
        raise ValueError("input_limit")
    obj = json.loads(raw.decode("utf-8"), object_pairs_hook=_object,
                     parse_constant=_constant)
    if (type(obj) is not dict or set(obj) != _FIELDS
            or obj["schema"] != REQUEST_SCHEMA
            or type(obj["request_id"]) is not str or not _ID.fullmatch(obj["request_id"])
            or type(obj["topic"]) is not str or not obj["topic"].strip()
            or type(obj["queries"]) is not list
            or any(type(query) is not str for query in obj["queries"])):
        raise ValueError("invalid_input")
    # Public-topic projection, query limits and direct-agent allowlists stay in
    # #1848/#1852, not in a second planner or search/provider implementation.
    obj["topic"].encode("utf-8")
    for query in obj["queries"]:
        query.encode("utf-8")
    return obj


def _prepare(topic, queries, *, agents, env):
    # Lazy import: the disabled launcher does not load provider infrastructure.
    from .script import generate_script

    result = generate_script(topic, queries, agents=agents, env=env, timeout_sec=45.0)
    scope = result.scope if result.scope in {"api_only", "web"} else "unknown"
    status = result.status if result.status in _FAILURES | {"ok", "partial"} else "unavailable"
    if status in {"ok", "partial"} and (not result.ok or scope == "unknown"):
        return _response("invalid_response")
    response = _response(status, scope=scope)
    if result.ok:
        response["script"] = {
            "body": result.script.body, "summary": result.script.summary,
            "selected_news": result.script.selected_news,
        }
        response["materials"] = [item.wire() for item in result.materials]
    return response


def execute(raw: bytes, *, agents: str, env: Mapping[str, str]) -> dict:
    """Prepare exactly one caller-owned request, without delivery or fallback."""
    gated = _gate(env)
    if gated:
        return _response(gated)
    request_id = ""
    try:
        obj = _request(raw)
        request_id = obj["request_id"]
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return _response("invalid_input")
    try:
        response = _prepare(obj["topic"], obj["queries"], agents=agents, env=dict(env))
        response["request_id"] = request_id
        # Bound the actual serialized reply, including verified material and JSON
        # escaping. Never silently truncate evidence, provenance or spoken text.
        if len(_encode(response)) > MAX_OUTPUT_BYTES:
            return _response("input_limit", request_id)
        return response
    except Exception:
        # No provider message, credentials, topic or generated text in errors.
        return _response("generation_failed", request_id)


def _encode(response):
    return (json.dumps(response, ensure_ascii=False, allow_nan=False,
                       separators=(",", ":")) + "\n").encode("utf-8")


class _Quiet(io.TextIOBase):
    """Discard incidental Python diagnostics without unbounded buffering."""
    def write(self, text):
        return len(text)


def main(argv=None, *, stdin=None, stdout=None, env=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    stdout = sys.stdout.buffer if stdout is None else stdout
    gated = _gate(env)
    if gated:
        response = _response(gated)
    elif len(argv) != 2 or argv[0] != "--agents" or not argv[1]:
        response = _response("invalid_input")
    else:
        stdin = sys.stdin.buffer if stdin is None else stdin
        try:
            # The worker must close stdin and impose its own process deadline.
            raw = stdin.read(MAX_INPUT_BYTES + 1)
            with redirect_stdout(_Quiet()), redirect_stderr(_Quiet()):
                response = execute(raw, agents=argv[1], env=env)
        except Exception:
            response = _response("invalid_input")
    try:
        stdout.write(_encode(response))
        stdout.flush()
    except (OSError, ValueError):
        return 2
    return 0 if response["status"] in {"ok", "partial"} else 3


if __name__ == "__main__":
    raise SystemExit(main())

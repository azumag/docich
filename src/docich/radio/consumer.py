"""Stable process bridge for the opt-in native RADIO script core.

The bridge owns no delivery, queue, credentials, provider selection or runtime
state.  It accepts one bounded JSON request on stdin and returns one bounded
JSON result on stdout so an existing RADIO consumer can call the reviewed
Python core without importing docich internals from shell.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from contextlib import redirect_stderr, redirect_stdout
from typing import TextIO

from .script import ScriptResult, generate_script

MAX_REQUEST_BYTES = 8192
MAX_RESPONSE_BYTES = 131072
_REQUEST_KEYS = frozenset({"topic", "queries", "agents"})
_STATUS = re.compile(r"[a-z_]{1,40}\Z")
_SCOPES = frozenset({"unknown", "api_only", "web"})


class ConsumerRequestError(ValueError):
    """Fixed, non-content-bearing request failure."""


def _object(pairs):
    payload = {}
    for key, value in pairs:
        if key in payload:
            raise ConsumerRequestError("invalid_request")
        payload[key] = value
    return payload


def _constant(_value):
    raise ConsumerRequestError("invalid_request")


def _parse_request(raw: str) -> tuple[str, tuple[str, ...], str]:
    if not isinstance(raw, str):
        raise ConsumerRequestError("invalid_request")
    try:
        if not raw or len(raw.encode("utf-8")) > MAX_REQUEST_BYTES:
            raise ConsumerRequestError("invalid_request")
        payload = json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
    except (json.JSONDecodeError, UnicodeError, RecursionError) as exc:
        raise ConsumerRequestError("invalid_request") from exc
    if type(payload) is not dict or set(payload) != _REQUEST_KEYS:
        raise ConsumerRequestError("invalid_request")

    topic = payload["topic"]
    queries = payload["queries"]
    agents = payload["agents"]
    if not isinstance(topic, str) or not isinstance(agents, str):
        raise ConsumerRequestError("invalid_request")
    if not isinstance(queries, list) or any(not isinstance(item, str) for item in queries):
        raise ConsumerRequestError("invalid_request")
    try:
        # JSON escapes can introduce lone surrogates even in valid UTF-8 input.
        for value in (topic, agents, *queries):
            value.encode("utf-8")
    except UnicodeError as exc:
        raise ConsumerRequestError("invalid_request") from exc
    return topic, tuple(queries), agents


def _wire(result: ScriptResult) -> dict[str, object]:
    if (not isinstance(result, ScriptResult)
            or not isinstance(result.status, str) or not _STATUS.fullmatch(result.status)
            or not isinstance(result.scope, str) or result.scope not in _SCOPES):
        raise ValueError("invalid_response")
    payload: dict[str, object] = {
        "status": result.status,
        "scope": result.scope,
    }
    if result.status in {"ok", "partial"}:
        script = result.script
        if (script is None or result.scope == "unknown"
                or not isinstance(script.body, str) or not script.body
                or not isinstance(script.summary, str) or not script.summary
                or not isinstance(script.selected_news, str)):
            raise ValueError("invalid_response")
        payload.update({
            "body": script.body,
            "summary": script.summary,
            "selected_news": script.selected_news,
        })
    return payload


def run_request(
    raw: str,
    *,
    env: Mapping[str, str],
) -> dict[str, object]:
    topic, queries, agents = _parse_request(raw)
    result = generate_script(topic, queries, agents=agents, env=dict(env))
    return _wire(result)


def _read_request(stdin: TextIO) -> str:
    buffer = getattr(stdin, "buffer", None)
    try:
        if buffer is not None:
            # Read bytes rather than locale-decoded characters on real stdin.
            return buffer.read(MAX_REQUEST_BYTES + 1).decode("utf-8", errors="strict")
        return stdin.read(MAX_REQUEST_BYTES + 1)
    except UnicodeError as exc:
        raise ConsumerRequestError("invalid_request") from exc


def _encode(payload: dict[str, object]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                            separators=(",", ":")) + "\n"
    # Include JSON escaping and the newline. Reject the whole response rather
    # than truncating spoken text or writing a partial success envelope.
    if len(serialized.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise ValueError("response_limit")
    return serialized


class _Quiet(io.TextIOBase):
    """Discard incidental Python prints, without buffering diagnostics."""

    def write(self, text):
        return len(text)


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    env: Mapping[str, str] | None = None,
) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv:
        return 2
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    env = os.environ if env is None else env

    try:
        # This is a one-shot process entry point, not a threaded library API.
        # Do not let incidental Python prints corrupt its JSON protocol.
        with redirect_stdout(_Quiet()), redirect_stderr(_Quiet()):
            payload = run_request(_read_request(stdin), env=env)
            serialized = _encode(payload)
    except ConsumerRequestError:
        serialized = _encode({"status": "invalid_request", "scope": "unknown"})
        rc = 2
    except Exception:
        # Never expose raw exceptions, credentials, request content or provider
        # details across the process boundary.
        serialized = _encode({"status": "bridge_error", "scope": "unknown"})
        rc = 1
    else:
        # Typed failures stay in the protocol; the opted-in caller must not
        # silently fall back to legacy generation or mark delivery complete.
        rc = 0

    try:
        buffer = getattr(stdout, "buffer", None)
        if buffer is not None:
            buffer.write(serialized.encode("utf-8"))
            buffer.flush()
        else:
            stdout.write(serialized)
            stdout.flush()
    except (OSError, UnicodeError, ValueError):
        return 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

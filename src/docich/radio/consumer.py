"""Stable process bridge for the opt-in native RADIO script core.

The bridge owns no delivery, queue, credentials, provider selection or runtime
state.  It accepts one bounded JSON request on stdin and returns one bounded
JSON result on stdout so an existing RADIO consumer can call the reviewed
Python core without importing docich internals from shell.
"""
from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping, Sequence
from typing import TextIO

from .script import ScriptResult, generate_script

MAX_REQUEST_BYTES = 8192
_REQUEST_KEYS = frozenset({"topic", "queries", "agents"})


class ConsumerRequestError(ValueError):
    """Fixed, non-content-bearing request failure."""


def _parse_request(raw: str) -> tuple[str, tuple[str, ...], str]:
    if not isinstance(raw, str):
        raise ConsumerRequestError("invalid_request")
    try:
        if not raw or len(raw.encode("utf-8")) > MAX_REQUEST_BYTES:
            raise ConsumerRequestError("invalid_request")
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeError) as exc:
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
    return topic, tuple(queries), agents


def _wire(result: ScriptResult) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": result.status,
        "scope": result.scope,
    }
    if result.ok and result.script is not None:
        payload.update({
            "body": result.script.body,
            "summary": result.script.summary,
            "selected_news": result.script.selected_news,
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

    raw = stdin.read(MAX_REQUEST_BYTES + 1)
    try:
        payload = run_request(raw, env=env)
    except ConsumerRequestError:
        payload = {"status": "invalid_request", "scope": "unknown"}
        rc = 2
    except Exception:
        # Never expose raw exceptions, credentials, request content or provider
        # details across the process boundary.
        payload = {"status": "bridge_error", "scope": "unknown"}
        rc = 1
    else:
        # ScriptResult failures are part of the typed protocol.  The caller
        # decides whether to hold, retry later or remain on the legacy path.
        rc = 0

    stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

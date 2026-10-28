"""Loopback OpenAI-compatible adapter for the caption translation client.

Serves ``POST /v1/chat/completions`` on 127.0.0.1 and forwards the single user
prompt to one configured direct-chat upstream through the LLM dispatch layer.
``TranslationRuntimeClient`` in ``captions.py`` is unchanged; only the process
answering its loopback endpoint is replaced. Prompts and secrets are never
logged; failures are logged as a short kind only.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Mapping

from .llm.contracts import AgentSpec, DispatchRequest
from .llm.policy import DIRECT_CHAT_PROVIDERS

LOGGER = logging.getLogger("docich.cc_translation_adapter")
LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 4100
MAX_REQUEST_BYTES = 256 * 1024
MAX_PROMPT_CHARS = 64 * 1024
DEFAULT_TIMEOUT_SEC = 25.0
MAX_TIMEOUT_SEC = 45.0
CHAT_PATH = "/v1/chat/completions"
HEALTH_PATH = "/healthz"

# (prompt, timeout) -> (output, failure_kind); failure_kind is "" on success.
Translator = Callable[[str, float], tuple[str, str]]


def probe_health(port: int = DEFAULT_PORT, timeout: float = 1.0) -> dict:
    """Read-only loopback GET of ``/healthz``; never trusts a proxy.

    Returns ``{"state": "ok", "upstream": ...}`` or ``{"state": ...}`` with state
    in {"unreachable", "invalid"}. The body is reduced to the upstream name only.
    """

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    url = f"http://{LOOPBACK_HOST}:{port}{HEALTH_PATH}"
    try:
        with opener.open(url, timeout=timeout) as response:
            body = json.loads(response.read(4096).decode("utf-8"))
    except Exception:
        return {"state": "unreachable"}
    if not isinstance(body, dict) or body.get("status") != "ok" or not isinstance(
        body.get("upstream"), str
    ):
        return {"state": "invalid"}
    return {"state": "ok", "upstream": body["upstream"]}


def describe_health(health: dict) -> str:
    if health.get("state") == "ok":
        return f"ok (upstream={health['upstream']})"
    return {"unreachable": "unreachable", "invalid": "invalid response"}[health["state"]]


def parse_upstream(raw: str) -> AgentSpec:
    """Accept only ``<direct-chat provider>:<model>`` as the adapter upstream."""

    provider, sep, model = raw.partition(":")
    if provider not in DIRECT_CHAT_PROVIDERS or not sep or not model.strip():
        raise ValueError("adapter upstream must be <direct-chat provider>:<model>")
    return AgentSpec(raw=raw, provider=provider, model=model.strip())


def dispatch_translator(spec: AgentSpec, env: Mapping[str, str]) -> Translator:
    from .llm.providers import call_agent

    snapshot = dict(env)

    def translate(prompt: str, timeout: float) -> tuple[str, str]:
        request = DispatchRequest(label="cc-translation", prompt=prompt, agents=(spec,))
        result = call_agent(spec, request, timeout=timeout, env=snapshot)
        if result.returncode == 0:
            return result.output, ""
        return "", result.failure_kind or "provider_failed"

    return translate


def _make_handler(
    translate: Translator, timeout: float, upstream: str
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "docich-cc-adapter"
        sys_version = ""

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            if self.path != HEALTH_PATH:
                self._send_error(404, "not_found")
                return
            # Only the configured upstream name is exposed; never the key or prompt.
            self._send_json(200, {"status": "ok", "upstream": upstream})

        def do_POST(self) -> None:  # noqa: N802 - http.server API
            if self.path != CHAT_PATH:
                self._send_error(404, "not_found")
                return
            prompt = self._read_prompt()
            if prompt is None:
                return
            output, kind = translate(prompt, timeout)
            if kind:
                LOGGER.warning("[CC_TRANSLATION_ADAPTER] failed kind=%s status=502", kind)
                self._send_error(502, kind)
                return
            self._send_json(
                200, {"choices": [{"message": {"role": "assistant", "content": output}}]}
            )

        def _read_prompt(self) -> str | None:
            raw_length = self.headers.get("Content-Length", "")
            if not raw_length.isdigit() or int(raw_length) > MAX_REQUEST_BYTES:
                self._send_error(400, "invalid_request")
                return None
            try:
                body = json.loads(self.rfile.read(int(raw_length)).decode("utf-8"))
                messages = body["messages"]
                if (
                    not isinstance(messages, list)
                    or len(messages) != 1
                    or not isinstance(messages[0], dict)
                    or messages[0].get("role") != "user"
                    or not isinstance(messages[0].get("content"), str)
                    or len(messages[0]["content"]) > MAX_PROMPT_CHARS
                ):
                    raise ValueError("unsupported messages")
            except (ValueError, KeyError, TypeError):
                self._send_error(400, "invalid_request")
                return None
            return messages[0]["content"]

        def _send_error(self, status: int, kind: str) -> None:
            self._send_json(status, {"error": {"type": kind}})

        def _send_json(self, status: int, payload: dict) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

    return Handler


def make_server(
    translate: Translator,
    *,
    port: int = DEFAULT_PORT,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    upstream: str = "",
) -> ThreadingHTTPServer:
    if not 0.1 <= timeout <= MAX_TIMEOUT_SEC:
        raise ValueError("adapter timeout must be between 0.1 and 45 seconds")
    handler = _make_handler(translate, timeout, upstream)
    return ThreadingHTTPServer((LOOPBACK_HOST, port), handler)


def main(env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    upstream = parse_upstream(env.get("DOCICH_CC_ADAPTER_UPSTREAM", ""))
    try:
        port = int(env.get("DOCICH_CC_ADAPTER_PORT", str(DEFAULT_PORT)))
        timeout = float(env.get("DOCICH_CC_ADAPTER_TIMEOUT_SEC", str(DEFAULT_TIMEOUT_SEC)))
    except ValueError as exc:
        raise ValueError("adapter port and timeout must be numeric") from exc
    if not 1 <= port <= 65535:
        raise ValueError("adapter port must be between 1 and 65535")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = make_server(
        dispatch_translator(upstream, env),
        port=port,
        timeout=timeout,
        upstream=upstream.raw,
    )
    LOGGER.info("[CC_TRANSLATION_ADAPTER] listening on %s:%s", LOOPBACK_HOST, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

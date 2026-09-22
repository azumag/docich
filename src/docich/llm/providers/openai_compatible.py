"""Generic OpenAI-compatible chat-completions POST helper (#829 PR-1).

Used by the local-LLM adapter.  Pure stdlib (no curl dependency): the
request/response semantics — JSON body, ``choices[0].message.content``
extraction, wall timeout — match the legacy curl call.
"""

from __future__ import annotations

import json
import urllib.request

from .base import ProviderResult, clean_text, provider_error_detected
from ..contracts import RC_FAILED


def post_chat_completions(
    base_url: str,
    model: str,
    prompt_text: str,
    timeout: int,
    extra_body: dict | None = None,
) -> ProviderResult:
    """POST one chat-completions request; return cleaned content or rc 1."""
    body: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt_text}],
        "stream": False,
    }
    if extra_body:
        body.update(extra_body)
    payload = json.dumps(body).encode("utf-8")
    url = base_url.rstrip("/") + "/v1/chat/completions"
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=max(1, timeout)) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
    except Exception as exc:
        return ProviderResult(rc=RC_FAILED, stderr=f"http_error: {type(exc).__name__}")
    try:
        data = json.loads(raw)
        content = data["choices"][0]["message"].get("content") or ""
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        return ProviderResult(rc=RC_FAILED, stderr="invalid_response")
    text = clean_text(content)
    if not text or provider_error_detected(text):
        return ProviderResult(rc=RC_FAILED, stderr="invalid_output")
    return ProviderResult(rc=0, stdout=text, resolved_model=model)

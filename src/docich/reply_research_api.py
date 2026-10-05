"""Native, retry-free Go proposals; the parent still retrieves/verifies evidence.

The research worker runs only inside the existing mandatory bwrap boundary.
The answer worker supervises the existing persona API with the remaining wall
deadline. Neither worker exposes tools or selects configuration from model text.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import secrets
import sys
import time

MODEL = "deepseek-v4.1-flash"
REGISTERED = "opencode-go:" + MODEL
BASE_URL = "https://opencode.ai/zen/go/v1"
PUBLIC_MODULES = ("__init__.py", "discord_memory.py", "discord_chat.py", "reply_research_api.py")
MAX_CALLS = 8


def registered(env) -> bool:
    """Only an explicit literal in the existing operator chain is eligible."""
    chain = env.get("AI_COMMON_AGENTS", "")
    return (isinstance(chain, str) and not any(marker in chain for marker in ("$", "`", "\n", "\r"))
            and REGISTERED in [part.strip() for part in chain.split(",")]
            and env.get("DOCICH_REPLY_OPENCODE_MODEL") == "opencode-go/" + MODEL)


def model_call(argv, env, *, deadline, runner, diagnostic=None):
    """Reserve each attempt before spawn/POST; retries/fallbacks are absent."""
    calls = 0
    session = secrets.token_hex(16)

    def call(prompt, remaining):
        nonlocal calls
        budget = min(remaining, deadline - time.monotonic())
        if calls >= MAX_CALLS or not math.isfinite(budget) or budget <= 0:
            raise ValueError("budget_exhausted")
        calls += 1
        payload = json.dumps({"prompt": prompt, "timeout": min(45., budget),
                              "session": session}, ensure_ascii=False).encode()
        options = {"diagnostic": diagnostic} if diagnostic is not None else {}
        return runner(argv, payload, env, budget, **options)
    return call


def answer_once(settings, messages, remaining, *, raw_reply=False):
    """One unchanged persona API request, forcibly bounded by its caller."""
    from .discord_chat import ChatError, ChatRateLimit, strict_json
    from .reply_research import _run
    if type(remaining) not in (int, float) or not math.isfinite(remaining) or not 0 < remaining <= 45:
        raise ChatError("LLM request failed")
    started = time.monotonic()
    payload = json.dumps({"base": settings.base_url, "model": settings.model,
                          "messages": messages, "timeout": remaining,
                          "provider": settings.provider, "upstream": settings.upstream,
                          "billing_mode": settings.billing_mode, "raw_reply": raw_reply}, ensure_ascii=False).encode()
    # No Discord token, memory path, ambient credentials or proxy environment.
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
           "DOCICH_ANSWER_API_KEY": settings.api_key}
    try:
        raw = _run([sys.executable, "-I", "-B", str(Path(__file__).resolve()), "answer"],
                   payload, env, remaining - (time.monotonic() - started))
        value = strict_json(raw)
        if value == {"error": "rate_limit"}:
            raise ChatRateLimit("LLM rate limited")
        if type(value) is not dict or set(value) != {"reply"} or type(value["reply"]) is not str:
            raise ValueError("invalid_response")
        return value["reply"]
    except ChatRateLimit:
        raise
    except Exception:
        raise ChatError("LLM request failed") from None


def worker(mode, payload):
    from .discord_chat import ChatBackend, ChatRateLimit, Settings
    timeout = payload["timeout"]
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 45:
        raise ValueError("timeout")
    if mode == "proposal":
        if set(payload) != {"prompt", "timeout", "session"} or type(payload["prompt"]) is not str:
            raise ValueError("invalid_request")
        # Fixed key/model/endpoint; the bridge creates this explicit proxy.
        settings = Settings(base_url=BASE_URL, model=MODEL, token="",
                            api_key=os.environ["OPENCODE_GO_API_KEY"])
        text = ChatBackend(settings)._complete_api(
            [{"role": "user", "content": payload["prompt"]}], max_tokens=256,
            timeout_sec=timeout, proxy_url=os.environ["DOCICH_RESEARCH_PROXY"],
            strict_proposal=True, session_id=payload["session"])
        return b"\n".join(json.dumps(event, ensure_ascii=False).encode() for event in (
            {"type": "text", "part": {"type": "text", "text": text}},
            {"type": "step_finish", "part": {"type": "step-finish", "reason": "stop"}}))
    if mode == "answer":
        if (set(payload) != {"base", "model", "messages", "timeout", "provider", "upstream",
                            "billing_mode", "raw_reply"} or type(payload["raw_reply"]) is not bool):
            raise ValueError("invalid_request")
        settings = Settings(base_url=payload["base"], model=payload["model"], token="",
                            api_key=os.environ["DOCICH_ANSWER_API_KEY"], provider=payload["provider"],
                            upstream=payload["upstream"], billing_mode=payload["billing_mode"])
        try:
            text = ChatBackend(settings)._complete_api(payload["messages"], timeout_sec=timeout,
                                                      raw_reply=payload["raw_reply"])
        except ChatRateLimit:
            return b'{"error":"rate_limit"}'
        return json.dumps({"reply": text}, ensure_ascii=False).encode()
    raise ValueError("invalid_mode")


def main():
    from .discord_chat import strict_json
    try:
        raw = sys.stdin.buffer.read(262145)
        if len(raw) > 262144 or len(sys.argv) != 2:
            return 2
        result = worker(sys.argv[1], strict_json(raw))
        sys.stdout.buffer.write(result)
        return 0
    except Exception:
        return 1  # Never emit keys, request/response text or raw exceptions.


if __name__ == "__main__":
    # Fixed public module directory, even under Python -I in the namespace.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "docich"
    raise SystemExit(main())

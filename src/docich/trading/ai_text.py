"""Native helper for free-text AI generation (Issue #198, Stage 3).

Thin wrapper over ``docich.ai_generate``'s native dispatch. It exists so the
PAPER corner narration and the end-of-corner strategy improvement can request
plain text through one audited code path.

A real invocation only happens when ``DOCICH_ALLOW_REAL_AI=1``; otherwise a
typed error is raised so callers can fall back deterministically. Output is
returned to the caller and never logged here.
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Mapping

from ..config import GlobalConfig


class AiTextError(RuntimeError):
    """User-facing failure when requesting generated text.

    ``kind`` is a short, non-secret failure category (gate state / rc bucket /
    upstream failure_kind such as ``rate_limit``) safe to persist in state or
    logs for diagnosis. It never contains model output, prompt text, or
    anything beyond what ``_safe_detail``/the upstream failure_kind file
    already bound to a fixed small vocabulary.
    """

    def __init__(self, message: str, *, kind: str = "unknown") -> None:
        super().__init__(message)
        self.kind = kind


AI_FAILURE_REASON_CODES = frozenset({
    "gate-disabled", "invalid-timeout", "invocation-error", "empty-output",
    "timeout", "rate-limit", "queue-giveup", "gate-giveup", "provider-failed",
    "invalid-output", "unknown",
})
_DISPATCH_FAILURE_REASONS = {
    "timeout": "timeout",
    "rate_limit": "rate-limit",
    "queue_giveup": "queue-giveup",
    "gate_giveup": "gate-giveup",
    "empty_output": "empty-output",
    "invalid_output": "invalid-output",
    "invalid_response": "invalid-output",
    "output_too_large": "invalid-output",
    "validator_failed": "invalid-output",
    "adapter_error": "provider-failed",
    "disabled": "provider-failed",
    "failed": "provider-failed",
    "http_error": "provider-failed",
    "invalid_provider": "provider-failed",
    "provider_error": "provider-failed",
    "provider_failed": "provider-failed",
    "transport_error": "provider-failed",
    "unclassified": "provider-failed",
}


def ai_failure_reason_code(kind: object) -> str:
    """Project the compatible error kind onto fixed, public diagnostic values.

    The detailed ``rc-N:failure_kind`` spelling remains available to existing
    callers. Unknown values, including a forged exception body, never become
    a persisted reason code.
    """
    if not isinstance(kind, str):
        return "unknown"
    if kind in AI_FAILURE_REASON_CODES:
        return kind
    match = re.fullmatch(r"rc-(-?[0-9]{1,3}):([a-z][a-z0-9_]{0,63})", kind)
    if match is None or int(match.group(1)) == 0:
        return "unknown"
    return _DISPATCH_FAILURE_REASONS.get(match.group(2), "unknown")


def _safe_detail(value: BaseException | str) -> str:
    return str(value).replace("\n", " ")[:240]


def _try_parse_json(candidate: str) -> dict | None:
    """Parse one balanced candidate, tolerating raw control characters.

    Models often pretty-print JSON with literal newlines/tabs inside string
    values, which strict ``json.loads`` rejects (9/17 corner: every AI script
    died this way with CornerScriptError). Raw C0 controls can never be
    meaningful JSON content or structure, so a sanitized retry is safe.
    """
    try:
        data = json.loads(candidate)
    except ValueError:
        data = None
    if isinstance(data, dict):
        return data
    try:
        data = json.loads(re.sub(r"[\x00-\x1f]", " ", candidate))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def extract_json_object(text: str) -> dict | None:
    """Best-effort extraction of one JSON object from model output.

    Tolerates prose around the object and ```json fences, and scans the first
    balanced ``{...}`` that parses. Returns ``None`` when no object is found.
    """
    raw = str(text or "")
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S)
    candidate = fenced.group(1) if fenced else raw
    start = candidate.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(candidate)):
            char = candidate[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    data = _try_parse_json(candidate[start:index + 1])
                    if data is not None:
                        return data
                    break
        start = candidate.find("{", start + 1)
    return None


def generate_text(
    g: GlobalConfig,
    *,
    label: str,
    agents: str,
    prompt_text: str,
    timeout: int = 600,
    overall_timeout_s: float | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    """Run one AI generation and return its stdout.

    Mirrors ``corner_improve._default_llm``: real execution is gated on
    ``DOCICH_ALLOW_REAL_AI=1`` and every failure (non-zero rc, empty output,
    transport error) raises ``AiTextError``. The prompt remains in memory and
    only the typed request is forwarded to the dispatch layer.

    ``env`` (when given) is the dispatch environment. Callers that run
    concurrently, such as the PAPER narration prefetch, grant the gate in a
    private copy instead of mutating the process-wide ``os.environ``, which
    cannot be saved/restored safely across threads.

    ``timeout`` limits each provider attempt. ``overall_timeout_s`` may give
    the ordered fallback chain a separate total budget; omitted, it retains
    the existing ``timeout + 60`` seconds, including lock and gate waits.
    """
    effective_env = os.environ if env is None else env
    if effective_env.get("DOCICH_ALLOW_REAL_AI") != "1":
        raise AiTextError("AI生成の実実行には DOCICH_ALLOW_REAL_AI=1 が必要です", kind="gate-disabled")
    if type(timeout) is not int or timeout < 1:
        raise AiTextError("timeout は1以上である必要があります", kind="invalid-timeout")
    if overall_timeout_s is not None and type(overall_timeout_s) not in (int, float):
        raise AiTextError("overall_timeout_s は有限の正数である必要があります", kind="invalid-timeout")
    try:
        overall_timeout = float(timeout + 60 if overall_timeout_s is None else overall_timeout_s)
    except (OverflowError, ValueError, TypeError) as exc:
        raise AiTextError("overall_timeout_s は有限の正数である必要があります", kind="invalid-timeout") from exc
    if not math.isfinite(overall_timeout) or overall_timeout <= 0:
        raise AiTextError("overall_timeout_s は有限の正数である必要があります", kind="invalid-timeout")

    from ..ai_generate import AiError, run_prompt

    try:
        result = run_prompt(
            g,
            label=label,
            agents=agents,
            prompt_text=prompt_text,
            timeout=timeout,
            timeout_sec=overall_timeout,
            env=None if env is None else dict(env),
        )
    except (AiError, OSError) as exc:
        raise AiTextError(f"AI呼び出しに失敗しました: {_safe_detail(exc)}", kind="invocation-error") from exc
    if result.returncode != 0:
        raise AiTextError(
            f"AI生成が失敗しました (rc={result.returncode})",
            kind=f"rc-{result.returncode}:{result.failure_kind or 'unclassified'}",
        )
    output = result.output.strip()
    if not output:
        raise AiTextError("AI生成の出力が空でした", kind="empty-output")
    return output

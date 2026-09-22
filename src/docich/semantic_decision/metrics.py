"""Secret-free semantic route metrics."""

from __future__ import annotations

import json
import fcntl
import math
import os
from pathlib import Path
from typing import Any, Mapping

from .contracts import DecisionRequest, RouteProfile, SemanticDecisionError


MAX_METRIC_BYTES = 1_048_576
_STATUSES = frozenset(
    {
        "ok",
        "missing_key",
        "invalid_config",
        "input_limit",
        "busy",
        "cooldown",
        "timeout",
        "network_error",
        "invalid_response",
        "auth_error",
        "rate_limited",
        "overloaded",
        "server_error",
        "http_error",
        "redirect_forbidden",
        "state_unavailable",
    }
)


def build_event(
    request: DecisionRequest,
    profile: RouteProfile,
    *,
    status: str,
    latency_ms: float | None,
    resolved_model: str | None = None,
    usage: Mapping[str, int] | None = None,
    fallback_reason: str | None = None,
) -> dict[str, Any]:
    """Build an allowlisted event without accepting raw provider data."""

    if status not in _STATUSES:
        raise SemanticDecisionError("invalid metric status")
    if latency_ms is not None and (
        type(latency_ms) not in (int, float)
        or not math.isfinite(latency_ms)
        or latency_ms < 0
    ):
        raise SemanticDecisionError("invalid metric latency")
    if resolved_model is not None and (
        not isinstance(resolved_model, str) or not 1 <= len(resolved_model) <= 128
    ):
        raise SemanticDecisionError("invalid metric model")
    if fallback_reason is not None and fallback_reason not in _STATUSES:
        raise SemanticDecisionError("invalid metric fallback reason")
    clean_usage = None
    if usage is not None:
        if set(usage) != {"input_tokens", "output_tokens"} or any(
            type(usage.get(key)) is not int or usage[key] < 0
            for key in ("input_tokens", "output_tokens")
        ):
            raise SemanticDecisionError("invalid metric usage")
        clean_usage = {key: usage[key] for key in ("input_tokens", "output_tokens")}
    return {
        "schema_version": 1,
        "purpose": request.purpose,
        "route": profile.name,
        "requested_model": request.model,
        "resolved_model": resolved_model,
        "latency_ms": round(float(latency_ms), 3) if latency_ms is not None else None,
        "status": status,
        "fallback_reason": fallback_reason,
        "usage": clean_usage,
        # Unknown cost is represented as null, never as a false zero.
        "cost_usd": None,
    }


def append_event(path: Path, event: Mapping[str, Any]) -> bool:
    """Best-effort bounded append of already-redacted metrics."""

    try:
        raw = (
            json.dumps(event, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            + "\n"
        ).encode("utf-8")
        if len(raw) > MAX_METRIC_BYTES:
            return False
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(
            path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
        )
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            if os.fstat(fd).st_size + len(raw) > MAX_METRIC_BYTES:
                return False
            os.write(fd, raw)
            return True
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(fd)
    except (OSError, TypeError, ValueError, UnicodeError):
        return False

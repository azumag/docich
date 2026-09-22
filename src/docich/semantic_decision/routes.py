"""Fixed semantic provider route profiles and safe configuration parsing."""

from __future__ import annotations

import os
from typing import Mapping

if __package__:
    from .contracts import (
        BACKEND_NAMES,
        DEFAULT_ROUTE,
        DEFAULT_TIMEOUT_MS,
        MAX_TIMEOUT_MS,
        MIN_TIMEOUT_MS,
        ROUTE_NAMES,
        RouteProfile,
        SemanticDecisionError,
    )
else:  # pragma: no cover - used by the isolated transport child.
    from contracts import (  # type: ignore[no-redef]
        BACKEND_NAMES,
        DEFAULT_ROUTE,
        DEFAULT_TIMEOUT_MS,
        MAX_TIMEOUT_MS,
        MIN_TIMEOUT_MS,
        ROUTE_NAMES,
        RouteProfile,
        SemanticDecisionError,
    )


_PROFILES = {
    "direct": RouteProfile(
        name="direct",
        endpoint="https://api.typesafe.ai/v1/systemone",
        requested_model="jev-1.13.0",
        credential_env="TYPESAFE_API_KEY",
    ),
    "vercel": RouteProfile(
        name="vercel",
        endpoint="https://ai-gateway.vercel.sh/typesafe/v1/systemone",
        requested_model="typesafe-ai/jev",
        credential_env="DOCICH_JEV_VERCEL_API_KEY",
    ),
}


def resolve_route(env: Mapping[str, str] | None = None) -> RouteProfile:
    """Resolve a fixed route without accepting endpoint/model overrides."""

    values = os.environ if env is None else env
    backend = str(values.get("DOCICH_SEMANTIC_BACKEND", "jev")).strip().lower()
    if backend not in BACKEND_NAMES:
        raise SemanticDecisionError("invalid semantic backend")
    route = str(values.get("DOCICH_JEV_ROUTE", DEFAULT_ROUTE)).strip().lower()
    if route not in ROUTE_NAMES or route not in _PROFILES:
        raise SemanticDecisionError("invalid semantic route")
    return _PROFILES[route]


def resolve_timeout_ms(
    env: Mapping[str, str] | None = None, profile: RouteProfile | None = None
) -> int:
    """Read only the bounded, non-secret timeout setting."""

    values = os.environ if env is None else env
    default = profile.timeout_ms if profile is not None else DEFAULT_TIMEOUT_MS
    raw = values.get("DOCICH_JEV_TIMEOUT_MS", str(default))
    try:
        timeout_ms = int(raw)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SemanticDecisionError("invalid semantic timeout") from exc
    if not MIN_TIMEOUT_MS <= timeout_ms <= MAX_TIMEOUT_MS:
        raise SemanticDecisionError("invalid semantic timeout")
    return timeout_ms


def credential_present(profile: RouteProfile, env: Mapping[str, str] | None = None) -> bool:
    """Check presence without exposing or persisting the credential value."""

    values = os.environ if env is None else env
    value = values.get(profile.credential_env, "")
    return isinstance(value, str) and bool(value)

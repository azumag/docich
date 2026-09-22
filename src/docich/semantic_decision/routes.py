"""Reviewed, immutable routes. No endpoint/model overrides or automatic failover."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class RouteProfile:
    name: str
    endpoint: str
    requested_model: str
    credential_env: str
    resolved_models: tuple[str, ...]


_PROFILES = MappingProxyType({
    "direct": RouteProfile(
        "direct", "https://api.typesafe.ai/v1/systemone", "jev-1.13.0",
        "TYPESAFE_API_KEY", ("jev-1.13.0",),
    ),
    # Prototype contract, not a claim of live compatibility. A different resolved
    # model/schema must be reviewed after an owner-authorised synthetic canary.
    "vercel": RouteProfile(
        "vercel", "https://ai-gateway.vercel.sh/typesafe/v1/systemone",
        "typesafe-ai/jev", "DOCICH_JEV_VERCEL_API_KEY", ("typesafe-ai/jev",),
    ),
})


def resolve_route(name: str = "direct") -> RouteProfile:
    if type(name) is not str or name not in _PROFILES:
        raise ValueError("invalid_config")
    return _PROFILES[name]

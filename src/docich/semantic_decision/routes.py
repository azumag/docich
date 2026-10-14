"""Reviewed, immutable routes. No endpoint/model overrides.

Failover is never implicit: only an owner-configured ordered chain of reviewed
routes (``parse_route_chain``, e.g. ``direct,vercel``) lets a consumer try the
next route, and the consumer decides which outcomes may do so.
"""
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


def parse_route_chain(value="direct") -> tuple[str, ...]:
    """``"direct"`` or an ordered, comma-separated chain of distinct routes.

    At most one fallback; whitespace, empty items, unknown or repeated routes
    are rejected rather than guessed.
    """
    if type(value) is not str:
        raise ValueError("invalid_config")
    names = tuple(value.split(","))
    if not 1 <= len(names) <= 2 or len(set(names)) != len(names):
        raise ValueError("invalid_config")
    for name in names:
        resolve_route(name)
    return names

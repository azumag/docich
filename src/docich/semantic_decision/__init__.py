"""Common, provider-independent semantic decision contracts.

The package owns route selection, strict response validation, and the bounded
transport boundary.  Purpose-specific classifiers build the typed request;
this layer never infers a rubric from arbitrary runtime context.
"""

from .client import DecisionResult, SemanticDecisionClient
from .contracts import (
    ChoiceQuestion,
    DecisionRequest,
    RouteProfile,
    SemanticDecisionError,
    TransportResult,
)
from .routes import resolve_route, resolve_timeout_ms
from .jev import build_request as build_jev_request

__all__ = [
    "ChoiceQuestion",
    "DecisionRequest",
    "DecisionResult",
    "RouteProfile",
    "SemanticDecisionClient",
    "SemanticDecisionError",
    "TransportResult",
    "resolve_route",
    "resolve_timeout_ms",
    "build_jev_request",
]

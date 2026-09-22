"""Native, game-independent LLM dispatch for comment/radio migrations."""

from .contracts import AgentSpec, DispatchRequest, DispatchResult, ProviderResult
from .dispatch import Dispatcher

__all__ = [
    "AgentSpec",
    "DispatchRequest",
    "DispatchResult",
    "Dispatcher",
    "ProviderResult",
]

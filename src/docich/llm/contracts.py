"""Small, typed contracts shared by the native LLM dispatcher.

The contracts deliberately contain no prompt persistence or provider-specific
credential fields. Prompts are carried in memory and provider adapters own
their short-lived transport details.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


class LlmError(RuntimeError):
    """A deterministic input or dispatch-policy error."""


@dataclass(frozen=True)
class AgentSpec:
    """One allowlisted provider/model entry from a fallback chain."""

    raw: str
    provider: str
    model: str | None = None

    @property
    def resolved_model(self) -> str:
        """Return the model identifier exposed to telemetry and the adapter."""

        if self.provider == "codex":
            return self.model or "amd-token-factory-deepseek-v4-flash"
        if self.provider == "opencode":
            return f"opencode/{self.model or ''}"
        if self.provider == "opencode-go":
            return f"opencode-go/{self.model or ''}"
        if self.provider == "openrouter":
            return f"openrouter/{self.model or ''}"
        if self.provider == "vercel":
            return f"vercel/{self.model or ''}"
        if self.provider == "amd":
            return f"amd-token-factory/{self.model or ''}"
        if self.provider == "local":
            return self.model or "gemma4:12b"
        return self.raw


Validator = Callable[[str], bool]


@dataclass(frozen=True)
class DispatchRequest:
    label: str
    prompt: str
    agents: tuple[AgentSpec, ...]
    timeout_sec: int | None = None
    validator: Validator | None = None


@dataclass(frozen=True)
class ProviderResult:
    """Sanitized result from one provider attempt."""

    returncode: int
    output: str = ""
    failure_kind: str = ""
    detail: str = ""


@dataclass(frozen=True)
class DispatchResult:
    """Final result of a fallback-chain dispatch."""

    returncode: int
    output: str = ""
    last_agent: str = ""
    failure_kind: str = ""
    attempted: int = 0
    skipped: int = 0
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and bool(self.output)

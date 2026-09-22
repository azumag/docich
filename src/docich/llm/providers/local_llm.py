"""Local-LLM provider adapter (#829 PR-1).

Mirrors ``_ai_call_local_llm``: POSTs ``{model, messages, stream:false,
temperature:0.7, num_predict:1600}`` to ``$base/v1/chat/completions``.
"""

from __future__ import annotations

from . import openai_compatible
from .base import ProviderResult


def model_from_agent(agent: str, settings) -> str:
    if agent.startswith("local:") and len(agent) > len("local:"):
        return agent.split(":", 1)[1]
    return settings.local_model


def run(
    agent: str,
    prompt_text: str,
    timeout: int,
    settings,
    label: str = "",
) -> ProviderResult:
    _ = label
    return openai_compatible.post_chat_completions(
        settings.local_base_url,
        model_from_agent(agent, settings),
        prompt_text,
        timeout,
        extra_body={
            "temperature": settings.local_temperature,
            "num_predict": settings.local_num_predict,
        },
    )

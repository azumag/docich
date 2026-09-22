"""Codex CLI provider adapter (#829 PR-1).

Mirrors ``_ai_call_codex_unqueued``: ``codex exec --skip-git-repo-check -m
<model> -o <out> <prompt>`` with stdin on ``/dev/null`` and the prompt as a
single argv positional.
"""

from __future__ import annotations

import tempfile

from .base import (
    ProviderResult,
    clean_text,
    provider_error_detected,
    rate_limit_detected,
    resolve_binary,
    run_fixed,
)
from ..contracts import RC_FAILED


def model_from_agent(agent: str, settings) -> str:
    if agent.startswith("codex:") and len(agent) > len("codex:"):
        return agent.split(":", 1)[1]
    return settings.codex_model or "amd-token-factory-deepseek-v4-flash"


def run(
    agent: str,
    prompt_text: str,
    timeout: int,
    settings,
    label: str = "",
) -> ProviderResult:
    _ = label
    binary = resolve_binary(settings.codex_bin, "codex")
    if binary is None:
        return ProviderResult(rc=RC_FAILED, stderr="codex_not_found")
    model = model_from_agent(agent, settings)
    raw = ""
    with tempfile.NamedTemporaryFile(
        prefix="docich-codex-", suffix=".txt", delete=True
    ) as out_file:
        result = run_fixed(
            [
                binary,
                "exec",
                "--skip-git-repo-check",
                "-m",
                model,
                "-o",
                out_file.name,
                prompt_text,
            ],
            timeout,
        )
        if result.rc != 0:
            if rate_limit_detected(
                result.stdout + "\n" + result.stderr
            ):
                return ProviderResult(
                    rc=79, stdout="", stderr="rate_limited",
                    resolved_model=model,
                )
            return result
        try:
            with open(out_file.name, encoding="utf-8") as stream:
                raw = stream.read()
        except OSError:
            return ProviderResult(rc=RC_FAILED, stderr="output_unreadable")
    text = clean_text(raw)
    # Rate-limit text wins over the generic provider-error bucket: the
    # legacy provider-error regex also matches "rate limit exceeded", so
    # checking it first would misclassify rate limits as generic failures
    # (legacy shell checks stderr only; the native file body has no stderr
    # to fall back on, so the body check must map 79 explicitly).
    if rate_limit_detected(text):
        return ProviderResult(rc=79, stderr="rate_limited", resolved_model=model)
    if not text or provider_error_detected(text):
        return ProviderResult(rc=RC_FAILED, stderr="invalid_output")
    return ProviderResult(rc=0, stdout=text, resolved_model=model)

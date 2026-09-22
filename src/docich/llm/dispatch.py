"""Single-candidate dispatch for the native LLM dispatch (#829 PR-1).

Mirrors ``_ai_dispatch``: agent-spec validation (rc 2), the MiniMax
deny-list (rc 1, no model call, no stats), label-based timeout resolution,
provider routing, and attempt/ok/fail telemetry.  Queue, improve-gate,
chain budgets, and list-level fallback live in :mod:`policy`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import budget as budget_mod
from .contracts import (
    RC_FAILED,
    RC_INVALID_SPEC,
    RC_OK,
    RC_RATE_LIMIT,
    RC_TIMEOUT,
    is_minimax_denied,
    record_stats,
    timeout_for,
    validate_agent_spec,
)
from .providers import base as base_prov
from .providers import codex as codex_prov
from .providers import local_llm as local_prov
from .providers import opencode as opencode_prov


def resolved_model_for(agent: str, settings) -> str:
    """Best-effort resolved model name for telemetry (no process spawn)."""
    if agent.startswith("codex"):
        return codex_prov.model_from_agent(agent, settings)
    if agent.startswith("local"):
        return local_prov.model_from_agent(agent, settings)
    if agent.startswith(("opencode", "openrouter:", "vercel:", "amd:")):
        return opencode_prov.model_from_agent(agent)
    return ""


def dispatch_one(
    settings,
    state_dir: Path,
    label: str,
    agent: str,
    prompt_text: str,
    timeout_override: int | None = None,
    validator: Callable[[str], bool] | None = None,
    chain_budget: budget_mod.ChainBudget | None = None,
    abort_retry_allowed: bool = True,
) -> tuple[int, str, str, bool]:
    """Run one candidate.  Returns (rc, text, resolved_model, backoff).

    rc is 0 (validator passed), 1 (generic failure, incl. timeout and the
    MiniMax deny-list), 2 (invalid spec), or 79 (explicit rate-limit).
    ``backoff`` is False only for the legacy "no model backoff" case
    (empty/invalid output with no provider error); all other failures take
    the scoped generic-failure backoff in the list runner.
    """
    if not validate_agent_spec(agent, settings.vercel_free_agents):
        return RC_INVALID_SPEC, "", "", False
    if is_minimax_denied(agent):
        return RC_FAILED, "", "", True
    record_stats(state_dir, "attempt", label, agent, "")
    timeout = timeout_for(label, agent, settings, timeout_override)
    if chain_budget is not None:
        timeout = chain_budget.clamp_timeout(timeout)
    resolved = resolved_model_for(agent, settings)
    if agent.startswith("codex"):
        result = codex_prov.run(agent, prompt_text, timeout, settings, label)
    elif agent.startswith("local"):
        result = local_prov.run(agent, prompt_text, timeout, settings, label)
    else:
        result = opencode_prov.run(
            agent,
            prompt_text,
            timeout,
            settings,
            label,
            state_dir=state_dir,
            abort_retry_allowed=abort_retry_allowed,
        )
    if result.rc == 79 or base_prov.rate_limit_detected(
        result.stdout + "\n" + result.stderr
    ):
        record_stats(state_dir, "fail", label, agent, 79, resolved)
        return RC_RATE_LIMIT, "", resolved, True
    if result.rc == RC_TIMEOUT:
        record_stats(state_dir, "fail", label, agent, result.rc, resolved)
        return RC_FAILED, "", resolved, True
    if result.rc != 0:
        record_stats(state_dir, "fail", label, agent, result.rc, resolved)
        return RC_FAILED, "", resolved, True
    text = base_prov.clean_text(result.stdout)
    if not text or base_prov.provider_error_detected(text):
        record_stats(state_dir, "fail", label, agent, "empty", resolved)
        return RC_FAILED, "", resolved, False
    if validator is not None and not validator(text):
        record_stats(state_dir, "fail", label, agent, "invalid", resolved)
        return RC_FAILED, "", resolved, False
    record_stats(state_dir, "ok", label, agent, 0, resolved or result.resolved_model)
    return RC_OK, text, resolved or result.resolved_model, True

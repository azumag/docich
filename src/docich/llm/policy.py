"""List-level chain runner for the native LLM dispatch (#829 PR-1).

Mirrors ``ai_generate_list`` (policy overlay): per-agent backoff skips,
lane queue acquisition, the radio-family improve-gate, chain budgets,
fallback across the agent chain, winner/loser telemetry, and the
``last_agent`` / ``failure_kind`` file contract.

Return codes: 0 winner, 1 all failed, 79/91/92 are produced the same way
the legacy list maps them (rate_limit kind, gate_giveup, queue_giveup).
Invalid specs are skipped silently; the MiniMax deny-list counts as a
generic failure without any stats or model call.
"""

from __future__ import annotations

from pathlib import Path
import time
from typing import Callable

from . import budget as budget_mod
from . import dispatch as dispatch_mod
from .backoff import ExplicitBackoff, FailureBackoff, FamilyBreaker
from .contracts import (
    RC_FAILED,
    RC_GATE_GIVEUP,
    RC_OK,
    RC_QUEUE_GIVEUP,
    RC_RATE_LIMIT,
    is_minimax_denied,
    record_stats,
    validate_agent_spec,
)
from .queue import LaneQueue, improve_gate


def _write(path: Path | None, text: str) -> None:
    if path is None:
        return
    try:
        Path(path).write_text(text, encoding="utf-8")
    except OSError:
        pass


def _chain_summary(
    state_dir: Path,
    label: str,
    vercel_count: int,
    vercel_agents: set[str],
    non_vercel_success: bool,
    terminal: str,
) -> None:
    if terminal not in ("winner", "all_failed", "queue_giveup", "gate_giveup"):
        terminal = "all_failed"
    record_stats(
        state_dir,
        "chain_summary",
        label,
        "",
        vercel_count,
        "",
        f"vrl={vercel_count};vda={len(vercel_agents)};"
        f"nfs={1 if non_vercel_success else 0};term={terminal}",
    )


def generate_list(
    settings,
    state_dir: Path,
    label: str,
    prompt_text: str,
    agents: list[str] | tuple[str, ...],
    timeout: int | None = None,
    validator: Callable[[str], bool] | None = None,
    chain_budget: budget_mod.ChainBudget | None = None,
    last_agent_file: Path | None = None,
    failure_kind_file: Path | None = None,
    max_queue_wait: int | None = None,
    improve_state_path: Path | None = None,
    radio_gen_started_at: float | None = None,
    abort_retry_allowed: bool = True,
) -> tuple[int, str, str, str]:
    """Run the agent chain.  Returns (rc, text, winner_agent, failure_kind).

    ``failure_kind`` is one of gate_giveup / queue_giveup / rate_limit /
    failed (empty on success), matching the legacy file enum.
    """
    state = Path(state_dir)
    explicit = ExplicitBackoff(state, settings)
    failure = FailureBackoff(state, settings)
    family = FamilyBreaker(state, settings)
    lanes = LaneQueue(state, settings)
    _write(last_agent_file, "")
    _write(failure_kind_file, "")

    if chain_budget is not None and chain_budget.exhausted():
        record_stats(state, "budget_exhausted", label, "", 0)
        return RC_OK, "", "", ""

    vercel_count = 0
    vercel_agents: set[str] = set()
    saw_rate_limit = False
    attempted = 0
    skipped_rate = 0
    skipped_failure = 0
    resolved_all: list[str] = []

    for agent in agents:
        if not validate_agent_spec(agent, settings.vercel_free_agents):
            continue
        resolved_all.append(dispatch_mod.resolved_model_for(agent, settings))
        if is_minimax_denied(agent):
            failure.record_failure(label, agent)
            continue
        if explicit.check(agent) > 0:
            skipped_rate += 1
            continue
        if agent.startswith("vercel:") and family.check() > 0:
            skipped_rate += 1
            continue
        if failure.check(label, agent) > 0:
            skipped_failure += 1
            continue

        acquired, holder = lanes.acquire(
            label, max_wait_override=max_queue_wait
        )
        if not acquired:
            _write(failure_kind_file, "queue_giveup\n")
            record_stats(state, "queue_giveup", label, agent, RC_QUEUE_GIVEUP)
            _chain_summary(
                state, label, vercel_count, vercel_agents, False, "queue_giveup"
            )
            return RC_QUEUE_GIVEUP, "", "", "queue_giveup"
        if not improve_gate(
            label,
            state,
            settings,
            improve_state_path=improve_state_path,
            radio_gen_started_at=radio_gen_started_at,
        ):
            lanes.release(label)
            _write(failure_kind_file, "gate_giveup\n")
            record_stats(state, "gate_giveup", label, agent, RC_GATE_GIVEUP)
            _chain_summary(
                state, label, vercel_count, vercel_agents, False, "gate_giveup"
            )
            return RC_GATE_GIVEUP, "", "", "gate_giveup"
        try:
            if chain_budget is not None and chain_budget.exhausted():
                record_stats(state, "budget_exhausted", label, agent, 0)
                return RC_OK, "", "", ""
            attempted += 1
            rc, text, resolved, count_failure = dispatch_mod.dispatch_one(
                settings,
                state,
                label,
                agent,
                prompt_text,
                timeout_override=timeout,
                validator=validator,
                chain_budget=chain_budget,
                abort_retry_allowed=abort_retry_allowed,
            )
        finally:
            lanes.release(label)

        if rc == RC_OK and text:
            _write(last_agent_file, agent + "\n")
            failure.record_success(label, agent)
            record_stats(state, "winner", label, agent, 0, resolved)
            non_vercel = vercel_count > 0 and not agent.startswith("vercel:")
            _chain_summary(
                state, label, vercel_count, vercel_agents, non_vercel, "winner"
            )
            return RC_OK, text, agent, ""
        if rc == RC_RATE_LIMIT:
            saw_rate_limit = True
            if agent.startswith("vercel:"):
                vercel_count += 1
                vercel_agents.add(agent)
                if len(vercel_agents) >= 2:
                    family.trip()
            explicit.set(agent, label)
            continue
        if rc != RC_OK:
            # Generic provider failure takes the scoped short backoff,
            # except the legacy "no model backoff" case (empty output or
            # validator rejection with no provider error).
            if count_failure:
                failure.record_failure(label, agent)
            continue

    if attempted == 0 and skipped_rate > 0:
        saw_rate_limit = True
    kind = "rate_limit" if saw_rate_limit else "failed"
    _write(failure_kind_file, kind + "\n")
    record_stats(
        state, "all_failed", label, "", 1, ",".join(resolved_all)
    )
    _chain_summary(state, label, vercel_count, vercel_agents, False, "all_failed")
    return RC_FAILED, "", "", kind

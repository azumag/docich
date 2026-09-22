"""Native LLM dispatch package (#829 PR-1).

Golden source: ``games/soviet_now/lib/ai_generate.sh`` (+ policy/budget
siblings).  This package owns provider routing, timeouts, fallback order,
queue lanes, backoff, chain budgets, and ``ai_stats`` telemetry — without
sourcing or executing any soviet_now code.
"""

from . import backoff, budget, contracts, dispatch, policy, queue

__all__ = ["backoff", "budget", "contracts", "dispatch", "policy", "queue"]

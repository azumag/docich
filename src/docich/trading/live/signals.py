"""Pure LIVE signal generation over market frames and the activated policy snapshot.

LIVE re-evaluates the same pure experiment rules as PAPER, but on real market
frames and with the policy snapshot the owner activated.  ``scan_live`` only
reads its arguments: it does not use the network, read a clock (``now`` is a
parameter), write files, or depend on PAPER's process-global state.

``frames`` must already be filtered for freshness by the caller; this module does not judge freshness.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from ..market_data import MarketFrame
from ..models import Opportunity, SkipDecision
from ..strategies import select_diversified_opportunities
from ..strategy_lab import scan_experiment_entries, scan_experiment_exits
from .policy import LivePolicySet
from .views import CostBasis

TIMEFRAME = "5m"
HISTORY_LIMIT = 25
DEFAULT_MAX_PAIR_CORRELATION = Decimal("0.85")


@dataclass(frozen=True)
class LiveSignals:
    entries: tuple[Opportunity, ...]
    exits: tuple[Opportunity, ...]
    skipped: tuple[SkipDecision, ...]
    reason_contexts: dict[str, dict[str, object]]


def scan_live(
    frames: Mapping[str, MarketFrame],
    positions: Mapping[str, CostBasis],
    policy_set: LivePolicySet,
    *,
    now: float,
) -> LiveSignals:
    skipped: list[SkipDecision] = []
    exits: list[Opportunity] = []
    contexts: dict[str, dict[str, object]] = {}
    exit_symbols: set[str] = set()

    # Exits first: a symbol that produces an exit candidate must not also
    # produce a buy candidate in this call (the PAPER worker rule).
    for symbol in sorted(positions):
        basis = positions[symbol]
        if basis.net_base <= 0 or basis.opened_at is None:
            continue
        policy = policy_set.policies.get(symbol)
        if policy is None:
            skipped.append(
                SkipDecision(f"live-no-policy:{symbol}", symbol, "no_policy", side="sell")
            )
            continue
        if symbol not in frames:
            skipped.append(
                SkipDecision(f"live-no-frame:{symbol}", symbol, "no_frame", side="sell")
            )
            continue
        result = scan_experiment_exits(
            frames,
            {symbol: (basis.net_base, basis.avg_cost, basis.opened_at)},
            policy.experiment,
            now=now,
        )
        contexts.update(result.reason_contexts)
        if result.opportunities:
            exits.extend(result.opportunities)
            exit_symbols.add(symbol)

    entry_candidates: list[Opportunity] = []
    for symbol in sorted(policy_set.policies):
        if symbol in exit_symbols:
            continue
        frame = frames.get(symbol)
        if frame is None:
            continue
        result = scan_experiment_entries(
            {symbol: frame}, policy_set.policies[symbol].experiment, now=now
        )
        contexts.update(result.reason_contexts)
        entry_candidates.extend(result.opportunities)

    entries: tuple[Opportunity, ...] = ()
    if entry_candidates:
        selection = select_diversified_opportunities(
            entry_candidates,
            frames,
            max_pair_correlation=DEFAULT_MAX_PAIR_CORRELATION,
            symbol_thresholds={
                symbol: policy_set.policies[symbol].experiment.max_pair_correlation
                for symbol in sorted(policy_set.policies)
            },
        )
        entries = selection.selected
        skipped.extend(selection.rejected)

    return LiveSignals(entries, tuple(exits), tuple(skipped), contexts)

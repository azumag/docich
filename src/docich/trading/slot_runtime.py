"""Worker-side handling of dedicated per-symbol PAPER slots.

The worker keeps its existing common-slot flow. When dedicated slots exist it
additionally assesses each one, scans that symbol with the slot's own entry
and exit rules, and gates that symbol's buys on the slot's own state. With no
dedicated slot nothing here runs (Issue #3623 §2.9, stage 2 PR-2).
"""
from __future__ import annotations

from typing import Mapping

from .experiment_control import ExperimentStep, assess_experiment, experiment_key
from .strategy_lab import scan_experiment_entries, scan_experiment_exits
from .strategy_metrics import read_ledger_rows
from .strategy_runtime import register_reason_contexts
from .symbol_slots import SlotLayout

# The slot's rules are unreadable: nobody may sell this symbol on its behalf.
_UNKNOWN_OWNER_REASONS = frozenset({"invalid-active", "control-invalid", "control-error"})


def assess_slots(trading_dir, layout: SlotLayout, *, capital_jpy, now: float) -> dict[str, ExperimentStep]:
    """Assess every dedicated slot once, sharing one ledger snapshot.

    A slot that cannot be assessed is blocked on its own; it never falls back
    to the common experiment and never stops the other slots.
    """
    rows = read_ledger_rows(trading_dir, now=now)
    steps: dict[str, ExperimentStep] = {}
    for symbol in layout.dedicated:
        try:
            steps[symbol] = assess_experiment(
                trading_dir, capital_jpy=capital_jpy, now=now,
                slot=symbol, layout=layout, ledger_rows=rows,
            )
        except Exception:
            steps[symbol] = ExperimentStep(None, None, False, "blocked", "control-error")
    return steps


def planned_keys(steps: Mapping[str, ExperimentStep]) -> dict[str, str]:
    return {symbol: experiment_key(step.active) for symbol, step in steps.items()}


def owned_symbols(steps: Mapping[str, ExperimentStep]) -> frozenset[str]:
    """Symbols whose holdings are exited by their slot's own rules."""
    return frozenset(symbol for symbol, step in steps.items() if step.active is not None)


def unowned_symbols(steps: Mapping[str, ExperimentStep]) -> frozenset[str]:
    """Symbols nobody may exit until the slot's state is recovered."""
    return frozenset(
        symbol for symbol, step in steps.items()
        if step.active is None and step.reason_code in _UNKNOWN_OWNER_REASONS
    )


def entries_allowed(steps: Mapping[str, ExperimentStep], symbol: str) -> bool:
    step = steps.get(symbol)
    return step is not None and step.entries_allowed


def scan_slot_entries(frames, steps: Mapping[str, ExperimentStep], *, now: float) -> tuple:
    opportunities: list = []
    for symbol in sorted(steps):
        step, frame = steps[symbol], frames.get(symbol)
        if step.active is None or not step.entries_allowed or frame is None:
            continue
        result = scan_experiment_entries({symbol: frame}, step.active, now=now)
        register_reason_contexts(result.reason_contexts)
        opportunities.extend(result.opportunities)
    return tuple(opportunities)


def scan_slot_exits(frames, cost_basis, steps: Mapping[str, ExperimentStep], *, now: float) -> tuple:
    opportunities: list = []
    for symbol in sorted(steps):
        step = steps[symbol]
        if step.active is None or symbol not in cost_basis:
            continue
        result = scan_experiment_exits(frames, {symbol: cost_basis[symbol]}, step.active, now=now)
        register_reason_contexts(result.reason_contexts)
        opportunities.extend(result.opportunities)
    return tuple(opportunities)


def correlation_thresholds(steps: Mapping[str, ExperimentStep]) -> dict[str, object]:
    return {
        symbol: step.active.max_pair_correlation
        for symbol, step in steps.items() if step.active is not None
    }

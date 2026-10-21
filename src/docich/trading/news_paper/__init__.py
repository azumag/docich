"""News-linked PAPER trading (parent #769 / delegation units N00-N09).

This package is the news-paper research lane. It is deliberately separate from
the existing crypto PAPER stack and from ``free_strategy``: separate account,
separate ledger (N05), separate worker (N07). No live-order, credential or
broker API is exposed here. ``mode`` is always ``paper`` and ``live_eligible``
is always false.

N06 implements the next-observation IOC execution model in
:mod:`docich.trading.news_paper.execution`.
"""
from .execution import (
    EXECUTION_MODEL_VERSION,
    LIVE_ELIGIBLE,
    MODE,
    TIME_IN_FORCE,
    ClockQuality,
    DepthLevel,
    ExecutionError,
    ExecutionOutcome,
    Fill,
    FillJournal,
    Fence,
    InMemoryFillJournal,
    InstrumentConstraints,
    LiquidityTracker,
    MarketObservation,
    PendingOrder,
    ReceiptState,
    RiskSnapshot,
    advance_partial,
    build_fills,
    ceil_to_tick,
    execute_pending_order,
    execution_price,
    floor_to_tick,
    round_lot_down,
    select_observation,
    spread_bps,
)

__all__ = [
    "ClockQuality",
    "DepthLevel",
    "EXECUTION_MODEL_VERSION",
    "ExecutionError",
    "ExecutionOutcome",
    "Fill",
    "FillJournal",
    "Fence",
    "InMemoryFillJournal",
    "InstrumentConstraints",
    "LIVE_ELIGIBLE",
    "LiquidityTracker",
    "MODE",
    "MarketObservation",
    "PendingOrder",
    "ReceiptState",
    "RiskSnapshot",
    "TIME_IN_FORCE",
    "advance_partial",
    "build_fills",
    "ceil_to_tick",
    "execute_pending_order",
    "execution_price",
    "floor_to_tick",
    "round_lot_down",
    "select_observation",
    "spread_bps",
]

"""News-linked PAPER trading (parent #769 / delegation units N00-N09).

This package is the news-paper research lane. It is deliberately separate from
the existing crypto PAPER stack and from ``free_strategy``: separate account,
separate ledger (N05), separate worker (N07). No live-order, credential or
broker API is exposed here. ``mode`` is always ``paper`` and ``live_eligible``
is always false.

N06 implements the next-observation IOC execution model in
:mod:`docich.trading.news_paper.execution`; N05 owns the dedicated ledger,
risk gate, reservations and durable decision receipts in
:mod:`docich.trading.news_paper.ledger` / ``risk`` / ``manager``.
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
from .ledger import (
    LEDGER_SCHEMA_VERSION,
    LedgerError,
    NewsLedger,
    recover as recover_ledger,
)
from .manager import (
    FIXTURE_MANAGER_POLICY_V1,
    DecisionRequest,
    PaperManager,
    ReceiptView,
)
from .risk import FIXTURE_RISK_POLICY_V1, RiskDecision, RiskPolicy

__all__ = [
    "ClockQuality",
    "DecisionRequest",
    "DepthLevel",
    "EXECUTION_MODEL_VERSION",
    "ExecutionError",
    "ExecutionOutcome",
    "FIXTURE_MANAGER_POLICY_V1",
    "FIXTURE_RISK_POLICY_V1",
    "Fill",
    "FillJournal",
    "Fence",
    "InMemoryFillJournal",
    "InstrumentConstraints",
    "LEDGER_SCHEMA_VERSION",
    "LIVE_ELIGIBLE",
    "LedgerError",
    "LiquidityTracker",
    "MODE",
    "MarketObservation",
    "NewsLedger",
    "PaperManager",
    "PendingOrder",
    "ReceiptState",
    "ReceiptView",
    "RiskDecision",
    "RiskPolicy",
    "RiskSnapshot",
    "TIME_IN_FORCE",
    "advance_partial",
    "build_fills",
    "ceil_to_tick",
    "execute_pending_order",
    "execution_price",
    "floor_to_tick",
    "recover_ledger",
    "round_lot_down",
    "select_observation",
    "spread_bps",
]

"""Deterministic paper execution; no exchange or private API access."""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from .ledger import PaperLedger
from .models import AllocationDecision, PaperFill, TradingValidationError, as_decimal


D = Decimal
# PAPER uses immediate synthetic fills, so model them as taker-like executions.
# The fixed fallback is deliberately conservative and can be overridden in
# tests/future per-market wiring without changing the ledger schema.
DEFAULT_TAKER_FEE_RATE = D("0.0012")  # 12 bps
DEFAULT_SLIPPAGE_BPS = D("5")         # 5 bps each execution
BPS = D("10000")


class PaperBroker:
    """Persist an allocation decision as an immediate cost-adjusted synthetic fill.

    Execution costs are represented through the recorded fill price so existing
    average-cost and realized/unrealized P/L accounting automatically includes
    them.  Allocation/reference notionals remain the risk-approved gross
    notionals; this avoids silently relaxing or exceeding the existing capital
    deployment ceiling merely because PAPER execution costs were introduced.
    """

    def __init__(
        self,
        ledger: PaperLedger,
        *,
        taker_fee_rate: Decimal | str = DEFAULT_TAKER_FEE_RATE,
        slippage_bps: Decimal | str = DEFAULT_SLIPPAGE_BPS,
    ):
        self.ledger = ledger
        fee = as_decimal(taker_fee_rate, "taker_fee_rate")
        slippage = as_decimal(slippage_bps, "slippage_bps")
        if fee < 0 or fee >= 1:
            raise TradingValidationError("taker_fee_rate must be in [0, 1)")
        if slippage < 0 or slippage >= BPS:
            raise TradingValidationError("slippage_bps must be in [0, 10000)")
        self.taker_fee_rate = fee
        self.slippage_bps = slippage

    def _cost_adjusted(self, decision: AllocationDecision) -> AllocationDecision:
        price = as_decimal(decision.price, "price")
        if price <= 0:
            raise TradingValidationError("price must be positive")
        slip = self.slippage_bps / BPS
        if decision.side == "buy":
            effective = price * (D("1") + slip) * (D("1") + self.taker_fee_rate)
        elif decision.side == "sell":
            effective = price * (D("1") - slip) * (D("1") - self.taker_fee_rate)
        else:
            raise TradingValidationError("paper fill side must be buy or sell")
        if effective <= 0:
            raise TradingValidationError("paper effective price must be positive")
        return replace(decision, price=effective)

    def fill(self, decision: AllocationDecision, *, timestamp: float) -> PaperFill:
        return self.ledger.record_fill(self._cost_adjusted(decision), timestamp=timestamp)

"""Deterministic paper execution; no exchange or private API access."""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Mapping

from .ledger import PaperLedger
from .models import AllocationDecision, MarketInfo, PaperFill, TradingValidationError, as_decimal


D = Decimal
# PAPER uses immediate synthetic fills, so model them as taker-like executions.
# The fixed fallbacks below are deliberately conservative and apply only when
# public market metadata / depth is unavailable; per-market wiring overrides
# them without changing the ledger schema.
DEFAULT_TAKER_FEE_RATE = D("0.0012")  # 12 bps
DEFAULT_SLIPPAGE_BPS = D("5")         # 5 bps each execution
BPS = D("10000")
COST_MODEL_VERSION = "paper-cost-v1"


@dataclass(frozen=True)
class ExecutionCost:
    """Deterministic cost inputs for one synthetic fill."""

    fee_rate: Decimal
    fee_source: str  # "market" | "fallback"
    slippage_bps: Decimal
    slippage_source: str  # "depth" | "fallback"

    def payload(self) -> dict[str, str]:
        return {
            "model": COST_MODEL_VERSION,
            "fee_rate": str(self.fee_rate),
            "fee_source": self.fee_source,
            "slippage_bps": str(self.slippage_bps),
            "slippage_source": self.slippage_source,
        }


def resolve_fee_rate(market: MarketInfo | None, side: str) -> tuple[Decimal, str]:
    """Return (taker fee rate, source) for a market/side pair.

    Side-aware: spot buys settle in base, sells settle in quote, so the
    side-native fee field wins. Any explicit market fee beats the
    conservative fallback; a missing market or missing fee metadata
    fails safe to the fallback.
    """
    normalized = str(side or "").strip().lower()
    if normalized not in {"buy", "sell"}:
        raise TradingValidationError("paper fill side must be buy or sell")
    if market is not None:
        if normalized == "buy":
            candidates = (
                market.taker_fee_rate_base,
                market.taker_fee_rate,
                market.taker_fee_rate_quote,
            )
        else:
            candidates = (
                market.taker_fee_rate_quote,
                market.taker_fee_rate,
                market.taker_fee_rate_base,
            )
        for candidate in candidates:
            if candidate is not None:
                return as_decimal(candidate, "taker_fee_rate"), "market"
    return DEFAULT_TAKER_FEE_RATE, "fallback"


def _book_levels(book: object, side: str) -> list[tuple[Decimal, Decimal]] | None:
    """Extract (price, amount) levels for the execution side, best-first."""
    if book is None:
        return None
    wants_asks = side == "buy"
    levels: list[tuple[Decimal, Decimal]] = []
    raw_levels: object = None
    bids = getattr(book, "bids", None)
    asks = getattr(book, "asks", None)
    if bids is not None and asks is not None:
        raw_levels = asks if wants_asks else bids
    elif getattr(book, "bid", None) is not None and getattr(book, "ask", None) is not None:
        # Top-of-book quote: single level on the execution side.
        if wants_asks:
            raw_levels = [(getattr(book, "ask"), getattr(book, "ask_amount", D("1")))]
        else:
            raw_levels = [(getattr(book, "bid"), getattr(book, "bid_amount", D("1")))]
    else:
        return None
    try:
        items = list(raw_levels)  # type: ignore[arg-type]
    except TypeError:
        return None
    for item in items:
        try:
            item_price = getattr(item, "price", None)
            item_amount = getattr(item, "amount", None)
            if item_price is not None and item_amount is not None:
                price, amount = as_decimal(item_price, "depth price"), as_decimal(item_amount, "depth amount")
            else:
                price, amount = as_decimal(item[0], "depth price"), as_decimal(item[1], "depth amount")
        except (TradingValidationError, IndexError, TypeError, ValueError):
            return None
        if price <= 0 or amount <= 0:
            return None
        levels.append((price, amount))
    return levels or None


def resolve_slippage_bps(
    *,
    amount: Decimal | str,
    side: str,
    book: object = None,
    fallback_bps: Decimal = DEFAULT_SLIPPAGE_BPS,
) -> tuple[Decimal, str]:
    """Return (slippage bps, source) from public depth for a base amount.

    Walks the execution side of the book (asks for buys, bids for sells)
    and measures the volume-weighted average price against the top of
    book. Orders that exceed visible depth, missing books, or malformed
    levels fail safe to the conservative fixed fallback.
    """
    normalized = str(side or "").strip().lower()
    if normalized not in {"buy", "sell"}:
        raise TradingValidationError("paper fill side must be buy or sell")
    quantity = as_decimal(amount, "amount")
    if quantity <= 0:
        raise TradingValidationError("paper fill amount must be positive")
    levels = _book_levels(book, normalized)
    if not levels:
        return as_decimal(fallback_bps, "slippage_bps"), "fallback"
    top = levels[0][0]
    remaining = quantity
    notional = D("0")
    for price, level_amount in levels:
        if remaining <= 0:
            break
        take = min(remaining, level_amount)
        notional += take * price
        remaining -= take
    if remaining > 0:
        # Depth cannot absorb the order; slippage is unknowable from
        # public data, so stay conservative.
        return as_decimal(fallback_bps, "slippage_bps"), "fallback"
    vwap = notional / quantity
    if normalized == "buy":
        slip = (vwap - top) / top
    else:
        slip = (top - vwap) / top
    slip = max(D("0"), slip)
    return slip * BPS, "depth"


class PaperBroker:
    """Persist an allocation decision as an immediate cost-adjusted synthetic fill.

    Execution costs are represented through the recorded fill price so existing
    average-cost and realized/unrealized P/L accounting automatically includes
    them.  Allocation/reference notionals remain the risk-approved gross
    notionals; this avoids silently relaxing or exceeding the existing capital
    deployment ceiling merely because PAPER execution costs were introduced.

    Costs resolve deterministically from public data only: per-market taker
    fees from ``MarketInfo`` and depth-aware slippage from the top of book /
    visible depth.  Anything unavailable fails safe to the conservative
    fixed fallbacks, and the applied breakdown is persisted to the private
    ledger for audit (never to the public status).
    """

    def __init__(
        self,
        ledger: PaperLedger,
        *,
        taker_fee_rate: Decimal | str = DEFAULT_TAKER_FEE_RATE,
        slippage_bps: Decimal | str = DEFAULT_SLIPPAGE_BPS,
        markets: Mapping[str, MarketInfo] | None = None,
        books: Mapping[str, object] | None = None,
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
        self.markets: dict[str, MarketInfo] = dict(markets or {})
        self.books: dict[str, object] = dict(books or {})

    def resolve_cost(
        self,
        decision: AllocationDecision,
        *,
        market: MarketInfo | None = None,
        book: object = None,
    ) -> ExecutionCost:
        resolved_market = market if market is not None else self.markets.get(decision.symbol)
        fee_rate, fee_source = resolve_fee_rate(resolved_market, decision.side)
        if fee_source == "fallback":
            fee_rate = self.taker_fee_rate
        resolved_book = book if book is not None else self.books.get(decision.symbol)
        slip_bps, slip_source = resolve_slippage_bps(
            amount=decision.amount, side=decision.side, book=resolved_book,
            fallback_bps=self.slippage_bps,
        )
        return ExecutionCost(
            fee_rate=fee_rate, fee_source=fee_source,
            slippage_bps=slip_bps, slippage_source=slip_source,
        )

    def _cost_adjusted(
        self, decision: AllocationDecision, cost: ExecutionCost
    ) -> AllocationDecision:
        price = as_decimal(decision.price, "price")
        if price <= 0:
            raise TradingValidationError("price must be positive")
        slip = cost.slippage_bps / BPS
        if decision.side == "buy":
            effective = price * (D("1") + slip) * (D("1") + cost.fee_rate)
        elif decision.side == "sell":
            effective = price * (D("1") - slip) * (D("1") - cost.fee_rate)
        else:
            raise TradingValidationError("paper fill side must be buy or sell")
        if effective <= 0:
            raise TradingValidationError("paper effective price must be positive")
        return replace(decision, price=effective)

    def fill(
        self,
        decision: AllocationDecision,
        *,
        timestamp: float,
        signal_context: Mapping[str, object] | None = None,
        market: MarketInfo | None = None,
        book: object = None,
    ) -> PaperFill:
        cost = self.resolve_cost(decision, market=market, book=book)
        return self.ledger.record_fill(
            self._cost_adjusted(decision, cost),
            timestamp=timestamp,
            signal_context=signal_context,
            cost_context=cost.payload(),
        )

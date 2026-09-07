"""Domain models for the paper-only crypto trading foundation."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import math
from typing import Any


class TradingValidationError(ValueError):
    """Raised when a trading-domain value is unsafe or malformed."""


def as_decimal(value: Any, name: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise TradingValidationError(f"{name} must be a finite decimal") from exc
    if not result.is_finite():
        raise TradingValidationError(f"{name} must be a finite decimal")
    return result


def _nonempty(value: str, name: str) -> str:
    text = str(value).strip()
    if not text:
        raise TradingValidationError(f"{name} must not be empty")
    return text


@dataclass(frozen=True)
class MarketInfo:
    symbol: str
    base: str
    quote: str
    spot: bool
    active: bool
    amount_step: Decimal | None = None
    min_amount: Decimal | None = None
    min_cost: Decimal | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _nonempty(self.symbol, "symbol"))
        object.__setattr__(self, "base", _nonempty(self.base, "base"))
        object.__setattr__(self, "quote", _nonempty(self.quote, "quote"))
        for field_name in ("amount_step", "min_amount", "min_cost"):
            raw = getattr(self, field_name)
            if raw is None:
                continue
            value = as_decimal(raw, field_name)
            if value <= 0:
                raise TradingValidationError(f"{field_name} must be positive")
            object.__setattr__(self, field_name, value)


@dataclass(frozen=True)
class Opportunity:
    opportunity_id: str
    strategy_id: str
    symbol: str
    side: str
    score: Decimal
    expected_edge_bps: Decimal
    max_notional_fraction: Decimal
    expires_at: float
    reason_code: str

    def __post_init__(self) -> None:
        for field_name in ("opportunity_id", "strategy_id", "symbol", "reason_code"):
            object.__setattr__(self, field_name, _nonempty(getattr(self, field_name), field_name))
        side = str(self.side).strip().lower()
        if side not in {"buy", "sell"}:
            raise TradingValidationError("side must be buy or sell")
        object.__setattr__(self, "side", side)
        score = as_decimal(self.score, "score")
        if score < 0 or score > 1:
            raise TradingValidationError("score must be between 0 and 1")
        object.__setattr__(self, "score", score)
        object.__setattr__(self, "expected_edge_bps", as_decimal(self.expected_edge_bps, "expected_edge_bps"))
        fraction = as_decimal(self.max_notional_fraction, "max_notional_fraction")
        if fraction <= 0 or fraction > 1:
            raise TradingValidationError("max_notional_fraction must be in (0, 1]")
        object.__setattr__(self, "max_notional_fraction", fraction)
        if not math.isfinite(float(self.expires_at)):
            raise TradingValidationError("expires_at must be finite")


@dataclass(frozen=True)
class AllocationDecision:
    opportunity_id: str
    strategy_id: str
    symbol: str
    side: str
    quote: str
    amount: Decimal
    price: Decimal
    quote_notional: Decimal
    reference_notional: Decimal
    reason_code: str


@dataclass(frozen=True)
class SkipDecision:
    opportunity_id: str
    symbol: str
    reason_code: str


@dataclass(frozen=True)
class AllocationResult:
    decisions: tuple[AllocationDecision, ...]
    skipped: tuple[SkipDecision, ...]

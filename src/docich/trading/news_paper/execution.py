"""Next-observation IOC execution for the news-linked PAPER account.

This module implements only the pure execution model (N06). It deliberately
does not open a database, read configuration, call a broker, touch the network
or reuse the crypto ``free_strategy`` ledger: the incompatible parts of that
package (JPY spot symbols, LabStore persistence, ``paused`` semantics and its
wall clock) are not valid for news PAPER, as recorded in N00 R18/A2-4.

Contract sources (all approved in #775):

* N00 v2 §2.7 (PaperOrder / PaperFill), §4.1-4.2 (T1/T2 durability).
* N00 v3 §1 (fill fence) and §1.5 (two-clock error budget).
* Parent #769 D7/D8 (IOC, partial fill, cost separation).

The caller owns durability. ``execute_pending_order`` returns an
``ExecutionOutcome`` and never mutates persistent state; N05 must apply the
fills, fee, reservation release and balance change in a single transaction.
Fills are keyed by ``(order_id, quote_ref, level_index)`` so a replay, a restart
or a re-delivered quote cannot create the same fill twice.

Mode is fixed to PAPER and ``live_eligible`` is always false.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from typing import Iterable, Mapping, Protocol, Sequence


D = Decimal
ZERO = D("0")
TEN_THOUSAND = D("10000")

MODE = "paper"
LIVE_ELIGIBLE = False
EXECUTION_MODEL_VERSION = "next_observation_ioc_v1"
TIME_IN_FORCE = "next_observation_ioc"

# Fixed diagnostic codes (parent #769 D10). The host never stores free-form
# exception text in a code field.
TERMINAL_STATUSES = frozenset({"filled", "canceled", "expired", "rejected"})


class ExecutionError(ValueError):
    """Raised for contract violations; carries a fixed reason code only."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


# ---------------------------------------------------------------------------
# value objects
# ---------------------------------------------------------------------------


def _dec(value: object, name: str, *, positive: bool = False, allow_zero: bool = True) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:  # pragma: no cover - defensive
        raise ExecutionError("VALUE_OUT_OF_RANGE") from exc
    if not result.is_finite():
        raise ExecutionError("NONFINITE_VALUE")
    if positive and result <= 0:
        raise ExecutionError("VALUE_OUT_OF_RANGE")
    if not allow_zero and result == 0:
        raise ExecutionError("VALUE_OUT_OF_RANGE")
    if result < 0:
        raise ExecutionError("VALUE_OUT_OF_RANGE")
    del name
    return result


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ExecutionError("TIMEZONE_MISSING")
    del name
    return value.astimezone(timezone.utc)


def _text(value: object, name: str, *, limit: int = 128) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ExecutionError("VALUE_OUT_OF_RANGE")
    del name
    return value


@dataclass(frozen=True)
class ClockQuality:
    """Clock quality recorded for both the observation writer and the filler.

    ``max_error_ms`` is the approved operational tolerance (N00 v2 §3.5,
    confirmed value 1000 ms). The fence uses the sum of both errors, so callers
    must supply the same value for the quote writer and the fill worker.
    """

    synced: bool
    source: str
    max_error_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.synced, bool):
            raise ExecutionError("TYPE_INVALID")
        if self.source not in {"ntp", "vm_clock", "injected", "unknown"}:
            raise ExecutionError("VALUE_OUT_OF_RANGE")
        if not isinstance(self.max_error_ms, int) or isinstance(self.max_error_ms, bool):
            raise ExecutionError("TYPE_INVALID")
        if not 0 <= self.max_error_ms <= 60_000:
            raise ExecutionError("VALUE_OUT_OF_RANGE")

    @property
    def trusted(self) -> bool:
        return self.synced and self.source != "unknown"

    @property
    def budget_seconds(self) -> Decimal:
        """Two-clock order-proof budget; the fence requires a strictly larger gap."""
        return D(self.max_error_ms * 2) / D(1000)


@dataclass(frozen=True)
class DepthLevel:
    price: Decimal
    size: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "price", _dec(self.price, "price", positive=True))
        object.__setattr__(self, "size", _dec(self.size, "size"))


@dataclass(frozen=True)
class InstrumentConstraints:
    """Approved per-instrument cost/lot/tick assumptions.

    Every value is mandatory: N00 §5 leaves the market values unapproved, so
    this module must not invent defaults. A missing value is a caller error.
    """

    version: str
    market: str
    quote_currency: str
    lot: Decimal
    min_quantity: Decimal
    min_notional: Decimal
    price_tick: Decimal
    fee_bps: Decimal
    fee_min: Decimal
    slippage_bps: Decimal
    spread_limit_bps: Decimal
    participation_rate: Decimal
    max_quote_age_seconds: Decimal
    volatility_limit_bps: Decimal | None = None
    max_order_notional: Decimal | None = None

    def __post_init__(self) -> None:
        _text(self.version, "version", limit=64)
        if self.market not in {"stocks", "fx"}:
            raise ExecutionError("VALUE_OUT_OF_RANGE")
        _text(self.quote_currency, "quote_currency", limit=16)
        object.__setattr__(self, "lot", _dec(self.lot, "lot", positive=True))
        object.__setattr__(self, "min_quantity", _dec(self.min_quantity, "min_quantity", positive=True))
        object.__setattr__(self, "min_notional", _dec(self.min_notional, "min_notional"))
        object.__setattr__(self, "price_tick", _dec(self.price_tick, "price_tick", positive=True))
        object.__setattr__(self, "fee_bps", _dec(self.fee_bps, "fee_bps"))
        object.__setattr__(self, "fee_min", _dec(self.fee_min, "fee_min"))
        object.__setattr__(self, "slippage_bps", _dec(self.slippage_bps, "slippage_bps"))
        object.__setattr__(self, "spread_limit_bps", _dec(self.spread_limit_bps, "spread_limit_bps"))
        participation = _dec(self.participation_rate, "participation_rate", positive=True)
        if participation > 1:
            raise ExecutionError("VALUE_OUT_OF_RANGE")
        object.__setattr__(self, "participation_rate", participation)
        object.__setattr__(
            self, "max_quote_age_seconds", _dec(self.max_quote_age_seconds, "max_quote_age_seconds")
        )
        if self.volatility_limit_bps is not None:
            object.__setattr__(
                self, "volatility_limit_bps", _dec(self.volatility_limit_bps, "volatility_limit_bps")
            )
        if self.max_order_notional is not None:
            object.__setattr__(
                self, "max_order_notional", _dec(self.max_order_notional, "max_order_notional")
            )

    def fee_for(self, notional: Decimal) -> Decimal:
        """Fee in quote currency; rounded up to one unit and floored by ``fee_min``.

        Slippage and spread are already in the execution price, so the fee is
        never applied to a slippage-adjusted price a second time.
        """
        variable = (notional * self.fee_bps / TEN_THOUSAND).to_integral_value(rounding=ROUND_CEILING)
        return max(self.fee_min, variable)


@dataclass(frozen=True)
class MarketObservation:
    """Subset of MarketObservation v1 required by execution (N00 v2 §2.5)."""

    observation_id: str
    source: str
    asset: str
    ingest_sequence: int
    market_event_at: datetime
    market_available_at: datetime
    available_at: datetime
    bid: Decimal
    ask: Decimal
    bid_size: Decimal
    ask_size: Decimal
    size_semantics: str
    tradeable: bool
    realtime: bool
    session_id: str
    calendar_version: str
    quote_digest: str
    provenance_class: str
    instrument_constraints_version: str
    bids: tuple[DepthLevel, ...] | None = None
    asks: tuple[DepthLevel, ...] | None = None
    session_open: bool | None = None
    volatility_bps: Decimal | None = None

    def __post_init__(self) -> None:
        _text(self.observation_id, "observation_id")
        _text(self.source, "source", limit=64)
        _text(self.asset, "asset")
        if not isinstance(self.ingest_sequence, int) or isinstance(self.ingest_sequence, bool):
            raise ExecutionError("TYPE_INVALID")
        if self.ingest_sequence < 1:
            raise ExecutionError("VALUE_OUT_OF_RANGE")
        for name in ("market_event_at", "market_available_at", "available_at"):
            object.__setattr__(self, name, _aware(getattr(self, name), name))
        if not (self.market_event_at <= self.market_available_at <= self.available_at):
            raise ExecutionError("VALUE_OUT_OF_RANGE")
        object.__setattr__(self, "bid", _dec(self.bid, "bid", positive=True))
        object.__setattr__(self, "ask", _dec(self.ask, "ask", positive=True))
        if self.bid > self.ask:
            raise ExecutionError("VALUE_OUT_OF_RANGE")
        object.__setattr__(self, "bid_size", _dec(self.bid_size, "bid_size"))
        object.__setattr__(self, "ask_size", _dec(self.ask_size, "ask_size"))
        if self.size_semantics not in {"executable", "indicative", "unknown"}:
            raise ExecutionError("VALUE_OUT_OF_RANGE")
        if not isinstance(self.tradeable, bool) or not isinstance(self.realtime, bool):
            raise ExecutionError("TYPE_INVALID")
        _text(self.session_id, "session_id", limit=64)
        _text(self.calendar_version, "calendar_version", limit=64)
        if not isinstance(self.quote_digest, str) or len(self.quote_digest) != 64:
            raise ExecutionError("VALUE_OUT_OF_RANGE")
        if self.provenance_class not in {"real_observation", "test_fixture", "synthetic_replay"}:
            raise ExecutionError("VALUE_OUT_OF_RANGE")
        _text(self.instrument_constraints_version, "instrument_constraints_version", limit=64)
        if self.bids is not None:
            object.__setattr__(self, "bids", tuple(self.bids))
        if self.asks is not None:
            object.__setattr__(self, "asks", tuple(self.asks))
        if self.session_open is not None and not isinstance(self.session_open, bool):
            raise ExecutionError("TYPE_INVALID")
        if self.volatility_bps is not None:
            object.__setattr__(self, "volatility_bps", _dec(self.volatility_bps, "volatility_bps"))

    @property
    def identity(self) -> tuple[str, str, datetime, str]:
        """Quote identity used to prove a quote differs from the decision quote.

        A re-read of the same file, a heartbeat or a receiver timestamp update
        does not change this identity, so it cannot make an old quote eligible.
        """
        return (self.source, self.asset, self.market_event_at, self.quote_digest)

    def levels(self, side: str) -> tuple[DepthLevel, ...]:
        if side == "buy":
            if self.asks is not None:
                return self.asks
            return (DepthLevel(self.ask, self.ask_size),)
        if side == "sell":
            if self.bids is not None:
                return self.bids
            return (DepthLevel(self.bid, self.bid_size),)
        raise ExecutionError("VALUE_OUT_OF_RANGE")


@dataclass(frozen=True)
class ReceiptState:
    """Durable decision-receipt state needed before a fill may be considered."""

    receipt_id: str
    status: str
    input_cutoff_sequence: int
    receipt_committed_at: datetime | None
    decision_quote: tuple[str, str, datetime, str] | None
    evidence_valid: bool = True

    def __post_init__(self) -> None:
        _text(self.receipt_id, "receipt_id")
        if self.status not in {"accepted", "held", "rejected"}:
            raise ExecutionError("VALUE_OUT_OF_RANGE")
        if not isinstance(self.input_cutoff_sequence, int) or isinstance(self.input_cutoff_sequence, bool):
            raise ExecutionError("TYPE_INVALID")
        if self.input_cutoff_sequence < 1:
            raise ExecutionError("VALUE_OUT_OF_RANGE")
        if self.receipt_committed_at is not None:
            object.__setattr__(
                self, "receipt_committed_at", _aware(self.receipt_committed_at, "receipt_committed_at")
            )
        if not isinstance(self.evidence_valid, bool):
            raise ExecutionError("TYPE_INVALID")

    @property
    def committed(self) -> bool:
        return self.status == "accepted" and self.receipt_committed_at is not None


@dataclass(frozen=True)
class Fence:
    """Durable fill fence; the first stored value is reused by every worker.

    ``fence_at`` must be sampled only after the committed marker SELECT
    succeeded (N00 v3 §1.1). A fence absent from storage forbids every fill.
    """

    fence_id: str
    marker_id: str
    receipt_id: str
    fence_at: datetime
    clock_quality: ClockQuality
    worker_id: str

    def __post_init__(self) -> None:
        _text(self.fence_id, "fence_id")
        _text(self.marker_id, "marker_id")
        _text(self.receipt_id, "receipt_id")
        object.__setattr__(self, "fence_at", _aware(self.fence_at, "fence_at"))
        _text(self.worker_id, "worker_id", limit=64)

    def admits(self, observation: MarketObservation) -> bool:
        """Strict N00 v3 §1.4 condition: ``available_at > fence_at + 2E``."""
        return observation.available_at > self.fence_at + _seconds(self.clock_quality.budget_seconds)


@dataclass(frozen=True)
class RiskSnapshot:
    """Account-side headroom passed in by N05's single-writer risk manager.

    Cash and inventory are separate: an unsettled sell does not fund a buy.
    """

    cash_available: Decimal
    inventory_available: Decimal
    exposure_headroom: Decimal
    new_entries_allowed: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "cash_available", _dec(self.cash_available, "cash_available"))
        object.__setattr__(
            self, "inventory_available", _dec(self.inventory_available, "inventory_available")
        )
        object.__setattr__(self, "exposure_headroom", _dec(self.exposure_headroom, "exposure_headroom"))
        if not isinstance(self.new_entries_allowed, bool):
            raise ExecutionError("TYPE_INVALID")


@dataclass(frozen=True)
class PendingOrder:
    """PaperOrder v1 subset that execution reads (N00 v2 §2.7)."""

    order_id: str
    receipt_id: str
    run_id: str
    scenario_id: str
    account_id: str
    asset: str
    side: str
    requested_quantity: Decimal
    remaining_quantity: Decimal
    filled_quantity: Decimal
    status: str
    expires_at: datetime
    accepted_at: datetime | None
    not_before_at: datetime | None
    reservation_id: str
    event_use_id: str
    execution_model_version: str = EXECUTION_MODEL_VERSION
    time_in_force: str = TIME_IN_FORCE

    def __post_init__(self) -> None:
        for name in ("order_id", "receipt_id", "run_id", "scenario_id", "account_id", "asset",
                     "reservation_id", "event_use_id"):
            _text(getattr(self, name), name)
        if self.side not in {"buy", "sell"}:
            raise ExecutionError("VALUE_OUT_OF_RANGE")
        object.__setattr__(
            self, "requested_quantity", _dec(self.requested_quantity, "requested_quantity", positive=True)
        )
        object.__setattr__(
            self, "remaining_quantity", _dec(self.remaining_quantity, "remaining_quantity")
        )
        object.__setattr__(self, "filled_quantity", _dec(self.filled_quantity, "filled_quantity"))
        if self.status not in {
            "pending", "partially_filled", "filled", "rejected", "canceled", "expired",
        }:
            raise ExecutionError("VALUE_OUT_OF_RANGE")
        # D7 keeps the canceled amount out of the order row, so only an upper
        # bound is checkable here: requested = filled + remaining + canceled.
        if self.filled_quantity + self.remaining_quantity > self.requested_quantity:
            raise ExecutionError("VALUE_OUT_OF_RANGE")
        object.__setattr__(self, "expires_at", _aware(self.expires_at, "expires_at"))
        if self.accepted_at is not None:
            object.__setattr__(self, "accepted_at", _aware(self.accepted_at, "accepted_at"))
        if self.not_before_at is not None:
            object.__setattr__(self, "not_before_at", _aware(self.not_before_at, "not_before_at"))
        if self.accepted_at is not None and self.not_before_at is not None:
            if not self.accepted_at <= self.not_before_at <= self.expires_at:
                raise ExecutionError("VALUE_OUT_OF_RANGE")
        if self.execution_model_version != EXECUTION_MODEL_VERSION:
            raise ExecutionError("SCHEMA_VERSION_UNSUPPORTED")
        if self.time_in_force != TIME_IN_FORCE:
            raise ExecutionError("SCHEMA_VERSION_UNSUPPORTED")

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


@dataclass(frozen=True)
class Fill:
    fill_id: str
    order_id: str
    receipt_id: str
    account_id: str
    asset: str
    side: str
    quantity: Decimal
    price: Decimal
    fee_jpy: Decimal
    slippage_bps: Decimal
    quote_ref: str
    market_event_at: datetime
    market_available_at: datetime
    filled_at: datetime
    partial: bool
    level_index: int
    execution_model_version: str = EXECUTION_MODEL_VERSION

    @property
    def notional(self) -> Decimal:
        return self.quantity * self.price


@dataclass(frozen=True)
class ExecutionOutcome:
    order_id: str
    status: str
    requested_quantity: Decimal
    filled_quantity: Decimal
    remaining_quantity: Decimal
    canceled_quantity: Decimal
    fills: tuple[Fill, ...] = ()
    reason_codes: tuple[str, ...] = ()
    selected_observation_id: str | None = None
    terminal: bool = False
    stage: str = "EXECUTE"

    def __post_init__(self) -> None:
        if self.requested_quantity != (
            self.filled_quantity + self.remaining_quantity + self.canceled_quantity
        ):
            raise ExecutionError("VALUE_OUT_OF_RANGE")
        if self.stage not in {"EXECUTE", "SETTLE"}:
            raise ExecutionError("VALUE_OUT_OF_RANGE")

    @property
    def filled_delta(self) -> Decimal:
        return sum((fill.quantity for fill in self.fills), ZERO)

    @property
    def fee_total(self) -> Decimal:
        return sum((fill.fee_jpy for fill in self.fills), ZERO)

    @property
    def notional_total(self) -> Decimal:
        return sum((fill.notional for fill in self.fills), ZERO)


class FillJournal(Protocol):
    """Durability port owned by N05; only the idempotency query is needed here."""

    def filled_keys(self, order_id: str) -> frozenset[tuple[str, int]]: ...


class InMemoryFillJournal:
    """Test double; production uses the N05 ledger transaction."""

    def __init__(self) -> None:
        self._keys: dict[str, set[tuple[str, int]]] = {}

    def filled_keys(self, order_id: str) -> frozenset[tuple[str, int]]:
        return frozenset(self._keys.get(order_id, ()))

    def record(self, order_id: str, quote_ref: str, level_index: int) -> None:
        self._keys.setdefault(order_id, set()).add((quote_ref, level_index))

    def record_fills(self, order_id: str, fills: Iterable[Fill]) -> None:
        for fill in fills:
            self.record(order_id, fill.quote_ref, fill.level_index)


class LiquidityTracker:
    """Prevents two orders from the same account consuming one level twice.

    Independent counterfactual arms use separate accounts, therefore separate
    trackers, and never compete for one book.
    """

    def __init__(self) -> None:
        self._used: dict[tuple[str, str, int], Decimal] = {}

    def used(self, account_id: str, observation_id: str, level_index: int) -> Decimal:
        return self._used.get((account_id, observation_id, level_index), ZERO)

    def consume(self, account_id: str, observation_id: str, level_index: int, quantity: Decimal) -> None:
        key = (account_id, observation_id, level_index)
        self._used[key] = self._used.get(key, ZERO) + quantity


# ---------------------------------------------------------------------------
# arithmetic helpers
# ---------------------------------------------------------------------------


def _seconds(value: Decimal) -> timedelta:
    return timedelta(seconds=float(value))


def ceil_to_tick(price: Decimal, tick: Decimal) -> Decimal:
    return (price / tick).to_integral_value(rounding=ROUND_CEILING) * tick


def floor_to_tick(price: Decimal, tick: Decimal) -> Decimal:
    return (price / tick).to_integral_value(rounding=ROUND_FLOOR) * tick


def round_lot_down(quantity: Decimal, lot: Decimal) -> Decimal:
    if quantity <= 0:
        return ZERO
    return (quantity / lot).to_integral_value(rounding=ROUND_FLOOR) * lot


def execution_price(side: str, level_price: Decimal, constraints: InstrumentConstraints) -> Decimal:
    """Adverse slippage, then adverse tick rounding.

    The spread is already embedded in bid/ask; it is never subtracted again.
    """
    slip = constraints.slippage_bps / TEN_THOUSAND
    if side == "buy":
        return ceil_to_tick(level_price * (1 + slip), constraints.price_tick)
    if side == "sell":
        return floor_to_tick(level_price * (1 - slip), constraints.price_tick)
    raise ExecutionError("VALUE_OUT_OF_RANGE")


def spread_bps(observation: MarketObservation) -> Decimal:
    mid = (observation.bid + observation.ask) / 2
    return (observation.ask - observation.bid) / mid * TEN_THOUSAND


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------


def select_observation(
    order: PendingOrder,
    *,
    receipt: ReceiptState,
    fence: Fence | None,
    observations: Sequence[MarketObservation],
    clock: ClockQuality,
) -> tuple[MarketObservation | None, tuple[str, ...]]:
    """Return the first eligible later observation, with deterministic ordering.

    Eligibility is identity/new-ness only (parent #769 §C, N00 v3 §1):
    different quote identity from the decision quote, newer ingest sequence,
    ``not_before_at`` older than the two-clock budget, and a fence that the
    observation strictly post-dates. Market quality is evaluated later so that
    an untradeable first observation is a ``skip_and_wait``, never a reason to
    cherry-pick a more favourable board.
    """
    if fence is None or fence.receipt_id != order.receipt_id:
        return None, ()
    if not clock.trusted or not fence.clock_quality.trusted:
        return None, ("CLOCK_UNTRUSTED",)
    if order.not_before_at is None or receipt.decision_quote is None:
        return None, ()

    budget = _seconds(max(clock.budget_seconds, fence.clock_quality.budget_seconds))

    ordered = sorted(observations, key=lambda item: item.ingest_sequence)
    seen_sequences: set[int] = set()
    for observation in ordered:
        if observation.ingest_sequence in seen_sequences:
            raise ExecutionError("VALUE_OUT_OF_RANGE")
        seen_sequences.add(observation.ingest_sequence)
        if observation.asset != order.asset:
            # P1-1: a different asset's book must never fill this order.
            continue
        if observation.identity == receipt.decision_quote:
            continue
        if observation.ingest_sequence <= receipt.input_cutoff_sequence:
            continue
        # N00 v3 §1.5: a gap within two clock errors cannot prove ordering.
        if observation.market_event_at <= order.not_before_at + budget:
            continue
        if observation.market_available_at <= order.not_before_at + budget:
            continue
        if not fence.admits(observation):
            continue
        return observation, ()
    return None, ()


# ---------------------------------------------------------------------------
# market quality, sizing and fills
# ---------------------------------------------------------------------------


def _market_rejection(
    observation: MarketObservation,
    constraints: InstrumentConstraints,
    now: datetime,
) -> str | None:
    if observation.instrument_constraints_version != constraints.version:
        return "VALUE_OUT_OF_RANGE"
    if observation.session_open is False:
        return "MARKET_CLOSED"
    if not observation.tradeable:
        return "QUOTE_UNTRADEABLE"
    if observation.size_semantics != "executable":
        return "LIQUIDITY_UNKNOWN"
    if spread_bps(observation) > constraints.spread_limit_bps:
        return "SPREAD_TOO_WIDE"
    if (
        constraints.volatility_limit_bps is not None
        and observation.volatility_bps is not None
        and observation.volatility_bps > constraints.volatility_limit_bps
    ):
        return "VOLATILITY_LIMIT"
    age = D((now - observation.market_available_at).total_seconds())
    if age < 0 or age > constraints.max_quote_age_seconds:
        return "QUOTE_STALE"
    return None


def _available_levels(
    observation: MarketObservation,
    side: str,
    constraints: InstrumentConstraints,
    liquidity: LiquidityTracker | None,
    account_id: str,
) -> list[tuple[int, Decimal, Decimal]]:
    result: list[tuple[int, Decimal, Decimal]] = []
    for index, level in enumerate(observation.levels(side)):
        capacity = level.size * constraints.participation_rate
        if liquidity is not None:
            capacity -= liquidity.used(account_id, observation.observation_id, index)
        if capacity <= 0:
            continue
        result.append((index, level.price, capacity))
    return result


def _max_affordable(
    side: str,
    price: Decimal,
    constraints: InstrumentConstraints,
    risk: RiskSnapshot,
) -> Decimal:
    """Upper bound on quantity before lot rounding.

    Fee headroom is included, so the fill can never overdraw cash or inventory.
    """
    if side == "buy":
        if price <= 0:
            return ZERO
        unit_cost = price * (1 + constraints.fee_bps / TEN_THOUSAND)
        if unit_cost <= 0:
            return ZERO
        # fee_min is per order, so reserve it once rather than per unit.
        spendable = max(ZERO, risk.cash_available - constraints.fee_min)
        return min(spendable / unit_cost, risk.exposure_headroom / price)
    if price <= 0:
        return ZERO
    return min(risk.inventory_available, risk.exposure_headroom / price)


def _check_minimums(
    quantity: Decimal,
    notional: Decimal,
    constraints: InstrumentConstraints,
) -> tuple[Decimal, str | None]:
    if quantity <= 0:
        return ZERO, "MIN_ORDER_NOT_MET"
    if quantity < constraints.min_quantity:
        return ZERO, "MIN_ORDER_NOT_MET"
    if notional < constraints.min_notional:
        return ZERO, "MIN_ORDER_NOT_MET"
    return quantity, None


def build_fills(
    order: PendingOrder,
    observation: MarketObservation,
    constraints: InstrumentConstraints,
    risk: RiskSnapshot,
    *,
    now: datetime,
    liquidity: LiquidityTracker | None = None,
    journal: FillJournal | None = None,
) -> tuple[tuple[Fill, ...], Decimal, str | None, tuple[str, ...]]:
    """Return ``(fills, unfilled_remainder, reason, codes)`` for one observation.

    Quantity is the minimum of the order remainder, participating depth,
    cash/inventory/exposure headroom, floored to the lot size. A post-acceptance
    price gap can only shrink the quantity; the remainder is canceled by IOC.
    ``reason`` names the cap that produced a partial fill (or a zero fill).
    """
    side = order.side
    levels = _available_levels(observation, side, constraints, liquidity, order.account_id)
    if not levels:
        return (), order.remaining_quantity, "LIQUIDITY_INSUFFICIENT", ()

    worst_price = max(execution_price(side, price, constraints) for _, price, _ in levels)
    affordable = _max_affordable(side, worst_price, constraints, risk)
    if constraints.max_order_notional is not None and worst_price > 0:
        affordable = min(affordable, constraints.max_order_notional / worst_price)

    capacity = sum((available for _, _, available in levels), ZERO)
    capped_by_risk = affordable < min(order.remaining_quantity, capacity)
    target = min(order.remaining_quantity, capacity, affordable)
    target = round_lot_down(target, constraints.lot)
    if target <= 0:
        # Lot rounding (or min quantity) rather than a funds shortfall: keep the
        # reason code honest so no-trade audits are not distorted.
        if affordable < order.remaining_quantity:
            code = "INSUFFICIENT_FUNDS" if side == "buy" else "INSUFFICIENT_INVENTORY"
        else:
            code = "MIN_ORDER_NOT_MET"
        return (), order.remaining_quantity, code, ()

    # Allocate level by level in lot increments.
    pending = target
    planned: list[tuple[int, Decimal, Decimal]] = []
    for index, level_price, available in levels:
        if pending <= 0:
            break
        take = round_lot_down(min(pending, available), constraints.lot)
        if take <= 0:
            continue
        planned.append((index, level_price, take))
        pending -= take
    total = sum((take for _, _, take in planned), ZERO)
    if total <= 0:
        if affordable < order.remaining_quantity:
            code = "INSUFFICIENT_FUNDS" if side == "buy" else "INSUFFICIENT_INVENTORY"
        else:
            code = "MIN_ORDER_NOT_MET"
        return (), order.remaining_quantity, code, ()

    gross = sum((take * execution_price(side, price, constraints) for _, price, take in planned), ZERO)
    total, minimum_code = _check_minimums(total, gross, constraints)
    if minimum_code is not None:
        return (), order.remaining_quantity, minimum_code, ()

    # Re-delivered quote / restart: only keys absent from the journal are new.
    already = journal.filled_keys(order.order_id) if journal is not None else frozenset()
    planned = [item for item in planned if (observation.observation_id, item[0]) not in already]
    if not planned:
        return (), order.remaining_quantity, "ORDER_ALREADY_FINAL", ()
    total = sum((take for _, _, take in planned), ZERO)

    allocation = sum((available for _, _, available in levels), ZERO)
    if total < order.remaining_quantity:
        if capped_by_risk:
            reason: str | None = "RISK_CHANGED_BEFORE_FILL"
        elif allocation < order.remaining_quantity:
            reason = "LIQUIDITY_INSUFFICIENT"
        else:
            reason = "RISK_CHANGED_BEFORE_FILL"
    else:
        reason = None

    partial = total < order.requested_quantity
    fills: list[Fill] = []
    for index, level_price, take in planned:
        price = execution_price(side, level_price, constraints)
        notional = take * price
        fills.append(
            Fill(
                fill_id=f"{order.order_id}:{observation.observation_id}:{index}",
                order_id=order.order_id,
                receipt_id=order.receipt_id,
                account_id=order.account_id,
                asset=order.asset,
                side=side,
                quantity=take,
                price=price,
                fee_jpy=constraints.fee_for(notional),
                slippage_bps=constraints.slippage_bps,
                quote_ref=observation.observation_id,
                market_event_at=observation.market_event_at,
                market_available_at=observation.market_available_at,
                filled_at=now,
                partial=partial,
                level_index=index,
            )
        )
    remainder = order.remaining_quantity - total
    return tuple(fills), remainder, reason, ()


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------


def _outcome(
    order: PendingOrder,
    *,
    status: str,
    fills: Sequence[Fill] = (),
    canceled: Decimal = ZERO,
    codes: Sequence[str] = (),
    observation_id: str | None = None,
    terminal: bool,
    stage: str = "EXECUTE",
) -> ExecutionOutcome:
    filled = order.filled_quantity + sum((fill.quantity for fill in fills), ZERO)
    remaining = order.requested_quantity - filled - canceled
    if remaining < 0:
        raise ExecutionError("VALUE_OUT_OF_RANGE")
    return ExecutionOutcome(
        order_id=order.order_id,
        status=status,
        requested_quantity=order.requested_quantity,
        filled_quantity=filled,
        remaining_quantity=remaining,
        canceled_quantity=canceled,
        fills=tuple(fills),
        reason_codes=tuple(codes),
        selected_observation_id=observation_id,
        terminal=terminal,
        stage=stage,
    )


def execute_pending_order(
    order: PendingOrder,
    *,
    receipt: ReceiptState,
    fence: Fence | None,
    observations: Sequence[MarketObservation],
    constraints: InstrumentConstraints,
    risk: RiskSnapshot,
    clock: ClockQuality,
    now: datetime,
    liquidity: LiquidityTracker | None = None,
    journal: FillJournal | None = None,
) -> ExecutionOutcome:
    """Process one pending IOC order against the first eligible observation.

    Returns an outcome for N05 to commit atomically. The function performs no
    I/O and never marks a held position: an expiry or a source outage leaves
    existing positions valued by the caller and only stops new entries.
    """
    now = _aware(now, "now")

    if order.receipt_id != receipt.receipt_id:
        return _outcome(
            order,
            status="canceled",
            canceled=order.remaining_quantity,
            codes=("ACCOUNT_SCOPE_MISMATCH",),
            terminal=True,
        )

    if not clock.trusted:
        # D10 EXECUTE: CLOCK_UNTRUSTED -> stop_new + cancel_pending.
        return _outcome(
            order,
            status="canceled",
            canceled=order.remaining_quantity,
            codes=("CLOCK_UNTRUSTED",),
            terminal=True,
        )

    if order.terminal:
        return _outcome(order, status=order.status, codes=("ORDER_ALREADY_FINAL",), terminal=True)

    if now >= order.expires_at:
        return _outcome(
            order,
            status="expired",
            canceled=order.remaining_quantity,
            codes=("ORDER_EXPIRED",),
            terminal=True,
            stage="SETTLE",
        )

    if not receipt.committed or order.accepted_at is None or order.not_before_at is None:
        # Fail closed: an uncommitted receipt or a missing T2 marker never fills.
        return _outcome(order, status=order.status, codes=("RECEIPT_NOT_COMMITTED",), terminal=False)

    if not receipt.evidence_valid:
        return _outcome(
            order,
            status="canceled",
            canceled=order.remaining_quantity,
            codes=("EVIDENCE_REVOKED",),
            terminal=True,
        )

    if fence is None or fence.receipt_id != order.receipt_id:
        # Fence not established yet: not eligible for evaluation, keep waiting.
        return _outcome(order, status=order.status, terminal=False)

    if fence is not None and getattr(fence, "clock_quality", None) is not None and not fence.clock_quality.trusted:
        return _outcome(
            order,
            status="canceled",
            canceled=order.remaining_quantity,
            codes=("CLOCK_UNTRUSTED",),
            terminal=True,
        )

    observation, codes = select_observation(
        order, receipt=receipt, fence=fence, observations=observations, clock=clock
    )
    if observation is None:
        return _outcome(order, status=order.status, codes=codes, terminal=False)
    if codes:
        return _outcome(order, status=order.status, codes=codes, terminal=False)

    rejection = _market_rejection(observation, constraints, now)
    if rejection is not None:
        if rejection == "VALUE_OUT_OF_RANGE":
            raise ExecutionError(rejection)
        # Market group: skip_and_wait, keep the pending order until expiry.
        return _outcome(
            order,
            status=order.status,
            codes=(rejection,),
            observation_id=observation.observation_id,
            terminal=False,
        )

    fills, remainder, reason, extra = build_fills(
        order,
        observation,
        constraints,
        risk,
        now=now,
        liquidity=liquidity,
        journal=journal,
    )
    if extra:
        return _outcome(
            order,
            status=order.status,
            codes=extra,
            observation_id=observation.observation_id,
            terminal=False,
        )
    if reason == "ORDER_ALREADY_FINAL":
        # The journal already holds these fills: they must not be created again.
        return _outcome(
            order,
            status=order.status,
            codes=("ORDER_ALREADY_FINAL",),
            observation_id=observation.observation_id,
            terminal=order.terminal,
        )
    if not fills:
        if reason == "LIQUIDITY_INSUFFICIENT":
            # Empty book: skip_and_wait per N00 §3.4 (do not cancel the reservation).
            return _outcome(
                order,
                status=order.status,
                codes=("LIQUIDITY_INSUFFICIENT",),
                observation_id=observation.observation_id,
                terminal=False,
            )
        # Risk group after acceptance: shrink to zero and cancel the reservation.
        return _outcome(
            order,
            status="canceled",
            canceled=order.remaining_quantity,
            codes=(reason or "MIN_ORDER_NOT_MET",),
            observation_id=observation.observation_id,
            terminal=True,
        )

    filled_total = sum((fill.quantity for fill in fills), ZERO)
    if filled_total == order.remaining_quantity:
        # Fully filled: no remainder to cancel.
        if liquidity is not None:
            for fill in fills:
                liquidity.consume(order.account_id, fill.quote_ref, fill.level_index, fill.quantity)
        if journal is not None:
            journal.record_fills(order.order_id, fills)  # type: ignore[attr-defined]
        return _outcome(
            order,
            status="filled",
            fills=fills,
            observation_id=observation.observation_id,
            terminal=True,
            stage="SETTLE",
        )

    # Partial fill: IOC cancels the remainder in the same transaction.
    codes_out: list[str] = ["IOC_REMAINDER_CANCELED"]
    if reason is not None:
        codes_out.append(reason)
    if liquidity is not None:
        for fill in fills:
            liquidity.consume(order.account_id, fill.quote_ref, fill.level_index, fill.quantity)
    if journal is not None:
        journal.record_fills(order.order_id, fills)  # type: ignore[attr-defined]
    return _outcome(
        order,
        status="canceled",
        fills=fills,
        canceled=remainder,
        codes=tuple(codes_out),
        observation_id=observation.observation_id,
        terminal=True,
        stage="SETTLE",
    )


def advance_partial(order: PendingOrder, outcome: ExecutionOutcome) -> PendingOrder:
    """Apply an outcome to an in-memory order copy (the ledger does this for real).

    Kept here so tests can exercise multi-tick behaviour without a database.
    """
    if outcome.order_id != order.order_id:
        raise ExecutionError("ACCOUNT_SCOPE_MISMATCH")
    status = outcome.status
    return replace(
        order,
        filled_quantity=outcome.filled_quantity,
        remaining_quantity=outcome.remaining_quantity,
        status=status,
    )

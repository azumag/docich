"""Exchange types and the two Protocols the LIVE worker talks to (Issue #3623, L2).

No network, no ccxt, no keys here.  A real exchange (L3) only has to *read*: the Protocol
is split into ``ExchangeReader`` (balances, books, orders, fills) and ``OrderSender``
(submit, cancel).  Only the mock implements ``OrderSender`` in L2/L3; the executor refuses
any sender whose ``kind`` is not on its allow-list.

Amounts are ``Decimal`` (never float).  Everything handed to the L1 ledger goes through
``dec_text`` so "10", "10.0000" and "1E+1" are one value.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Mapping, Protocol

from .canonical import canonical_json


def dec(value: object) -> Decimal:
    """Strict Decimal: float and bool are refused, the result must be finite."""
    if isinstance(value, (float, bool)) or value is None:
        raise ValueError(f"not an exact number: {value!r}")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"not a number: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"not finite: {value!r}")
    return result


def dec_text(value: Decimal) -> str:
    """One canonical text per value ("10", never "10.0000" or "1E+1")."""
    value = dec(value)
    if value == 0:
        return "0"
    text = format(value, "f")  # normalize() would round to the context precision
    return text.rstrip("0").rstrip(".") if "." in text else text


def ms_to_seconds(ms: int) -> float:
    """The same conversion every time, so a re-delivered fill has the same executed_at."""
    return float(Decimal(int(ms)) / Decimal(1000))


def raw_json_text(raw: Mapping | None) -> str | None:
    """Canonical JSON of the exchange's raw payload; a float anywhere is refused."""
    if raw is None:
        return None
    return canonical_json(dict(raw)).decode("utf-8")


class OrderStatus(str, Enum):  # same vocabulary as the ledger's orders.status CHECK
    INACTIVE = "INACTIVE"
    UNFILLED = "UNFILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FULLY_FILLED = "FULLY_FILLED"
    CANCELED_UNFILLED = "CANCELED_UNFILLED"
    CANCELED_PARTIALLY_FILLED = "CANCELED_PARTIALLY_FILLED"
    REJECTED = "REJECTED"


TERMINAL_STATUSES = frozenset({OrderStatus.FULLY_FILLED, OrderStatus.CANCELED_UNFILLED,
                               OrderStatus.CANCELED_PARTIALLY_FILLED, OrderStatus.REJECTED})


@dataclass(frozen=True)
class Capabilities:
    client_order_id: bool       # a client order id can be attached on submit
    fetch_by_client_id: bool    # ...and an order can be looked up by it
    fills_by_order: bool        # the fill history can be filtered by order id
    client_order_id_max_len: int = 0
    deposit_history: bool = False   # a deposit history per currency can be read (Issue #4587)


@dataclass(frozen=True)
class MarketRules:
    symbol: str
    base: str
    quote: str
    amount_step: Decimal
    min_amount: Decimal
    price_tick: Decimal
    base_unit: Decimal          # rounding unit of the base balance
    quote_unit: Decimal         # rounding unit of the quote balance
    max_fee_rate: Decimal | None = None   # upper bound of a fee as a fraction of notional (None: unknown)


@dataclass(frozen=True)
class OrderBook:
    symbol: str
    bids: tuple[tuple[Decimal, Decimal], ...]   # (price, amount), best first
    asks: tuple[tuple[Decimal, Decimal], ...]
    exchange_ts_ms: int


@dataclass(frozen=True)
class AssetBalance:
    total: Decimal
    free: Decimal
    locked: Decimal


@dataclass(frozen=True)
class BalanceSnapshot:
    assets: Mapping[str, AssetBalance]   # upper-case currency code
    exchange_ts_ms: int | None
    fetched_at: float


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: str
    order_type: str
    amount: Decimal
    price: Decimal | None
    client_order_id: str | None


@dataclass(frozen=True)
class OrderSnapshot:
    exchange_order_id: str
    symbol: str
    side: str
    order_type: str
    amount: Decimal
    price: Decimal | None
    status: OrderStatus
    executed_amount: Decimal
    remaining_amount: Decimal
    average_price: Decimal | None
    ordered_at_ms: int | None
    client_order_id: str | None = None
    raw: Mapping | None = None


class FeeStatus(str, Enum):
    KNOWN = "known"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FeeInfo:
    status: FeeStatus
    amount: Decimal | None = None       # negative = rebate
    currency: str | None = None

    def __post_init__(self) -> None:
        if self.status is FeeStatus.UNKNOWN and (self.amount is not None or self.currency is not None):
            raise ValueError("an unknown fee carries no amount or currency")
        if self.status is FeeStatus.KNOWN and (self.amount is None or not self.currency):
            raise ValueError("a known fee needs an amount and a currency")


@dataclass(frozen=True)
class Fill:
    trade_id: str
    exchange_order_id: str
    symbol: str
    side: str
    amount: Decimal
    price: Decimal
    quote_notional: Decimal | None      # None: amount * price exactly
    fee: FeeInfo
    maker_taker: str                    # "maker" | "taker" | "unknown"
    executed_at_ms: int
    raw: Mapping | None = None

    def notional(self) -> Decimal:
        return self.quote_notional if self.quote_notional is not None else self.amount * self.price


@dataclass(frozen=True)
class OrderPage:
    orders: tuple[OrderSnapshot, ...]
    complete: bool          # False: the list may be cut off, so it proves nothing about absence


@dataclass(frozen=True)
class FillPage:
    fills: tuple[Fill, ...]
    complete: bool


@dataclass(frozen=True)
class DepositRecord:
    """One deposit the exchange reports in its own history (Issue #4587).

    ``record_id`` is the deterministic ledger id for the record
    (``<exchange>:deposit:<exchange's own id>``), so a restart re-read is a no-op.
    ``status`` is the exchange's own word; ``credited`` is True only when the exchange
    says the money is in the balance (bitbank: ``DONE`` -- ``FOUND``/``CONFIRMED`` may
    still be in flight and are never counted).  ``occurred_at_ms`` is when the exchange
    first *saw* the deposit (bitbank ``found_at``); ``confirmed_at_ms`` is when it
    actually credited the balance (bitbank ``confirmed_at``, None while in flight or when
    the exchange does not report it).  Only the latter proves whether the money was
    inside the opening snapshot, so the baseline classifier must use it.
    """
    record_id: str
    currency: str               # upper-case currency code
    amount: Decimal
    status: str
    credited: bool
    occurred_at_ms: int
    confirmed_at_ms: int | None = None
    raw: Mapping | None = None


@dataclass(frozen=True)
class DepositPage:
    deposits: tuple[DepositRecord, ...]
    complete: bool              # False: the list may be cut off, so a missing row proves nothing


class CancelKind(str, Enum):
    CANCELED = "canceled"
    ALREADY_CLOSED = "already_closed"


@dataclass(frozen=True)
class CancelOutcome:
    kind: CancelKind
    snapshot: OrderSnapshot | None


class ExchangeError(RuntimeError):
    """Base class of everything an exchange adapter may raise."""


class ExchangeAmbiguous(ExchangeError):
    """It is not known whether the request arrived (timeout, disconnect, 5xx, anything unclassified)."""


class ExchangeRejected(ExchangeError):
    """The exchange explicitly refused: nothing was created."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class OrderNotFound(ExchangeError):
    """A lookup answered 'there is no such order' (a firm answer, unlike a timeout)."""


class ExchangeDataError(ExchangeError):
    """The response is malformed or a required field is missing."""


class ExchangeReader(Protocol):
    @property
    def capabilities(self) -> Capabilities: ...

    def market_rules(self, symbol: str) -> MarketRules: ...

    def fetch_balance(self) -> BalanceSnapshot: ...

    def fetch_order_book(self, symbol: str, *, depth: int = 20) -> OrderBook: ...

    def fetch_order(self, symbol: str, exchange_order_id: str) -> OrderSnapshot: ...

    def fetch_order_by_client_id(self, symbol: str, client_order_id: str) -> OrderSnapshot | None: ...

    def fetch_open_orders(self, symbol: str) -> OrderPage: ...

    def fetch_fills(self, symbol: str, *, since_ms: int, exchange_order_id: str | None = None) -> FillPage: ...

    def fetch_deposits(self, currency: str, *, since_ms: int) -> DepositPage: ...


class OrderSender(Protocol):
    kind: str

    def submit_order(self, req: OrderRequest) -> OrderSnapshot: ...

    def cancel_order(self, symbol: str, exchange_order_id: str) -> CancelOutcome: ...


def make_client_order_id(decision_key: str, caps: Capabilities) -> str | None:
    """Deterministic id for a decision, or None when the exchange has no client order id."""
    if not caps.client_order_id:
        return None
    limit = caps.client_order_id_max_len if caps.client_order_id_max_len > 1 else 32
    return ("d" + hashlib.sha256(decision_key.encode("utf-8")).hexdigest())[:limit]


class Guarded:
    """Wrap a reader/sender: an exception nobody classified becomes ``ExchangeAmbiguous``.

    Everything the executor calls on the exchange goes through this, so "the exchange failed in an
    unexpected way" always means "outcome unknown" and never leaks out as a crash of the polling loop
    (or, worse, as a wrong "not created").  Ledger code is not wrapped.
    """

    def __init__(self, inner) -> None:
        object.__setattr__(self, "_inner", inner)

    def __getattr__(self, name: str):
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def call(*args, **kwargs):
            try:
                return attr(*args, **kwargs)
            except ExchangeError:
                raise
            except Exception as exc:
                if type(exc).__name__ == "LeaseLost":
                    raise
                raise ExchangeAmbiguous(f"{name}: unclassified {type(exc).__name__}") from exc
        return call

"""``BitbankReader``: the L2 ``ExchangeReader`` over bitbank's read-only endpoints (Issue #3623, L3).

It implements reads only: there is no ``submit_order``, ``cancel_order`` or ``kind`` here, so the
order executor refuses it as a sender.  It talks to a ``BitbankTransport`` (``get(endpoint, params)``),
which is where ccxt lives; tests use a fake one.

Completeness is conservative, because an incomplete list proves nothing about absence:
open orders are complete only when fewer than ``count`` came back; fills only when fewer than the
page size came back, or the oldest one is already older than the requested start.  ``since`` is not
sent to the exchange (its unit is documented inconsistently); the cut is made here.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Mapping, Protocol, Sequence

from ...live.exchange import (BalanceSnapshot, Capabilities, ExchangeAmbiguous, ExchangeDataError, ExchangeError,
                              FillPage, MarketRules, OrderBook, OrderNotFound, OrderPage, OrderSnapshot)
from . import parse


class BitbankTransport(Protocol):
    def get(self, endpoint: str, params: Mapping[str, object]) -> object:
        """The ``data`` of a successful response.  Failures are raised as L2 exceptions."""


class ReaderUnavailable(RuntimeError):
    """The reader could not be built (message: a short reason, never a value)."""


@dataclass(frozen=True)
class ReaderHealth:
    last_error_kind: str | None
    consecutive_failures: int
    last_ok_at: float | None


CAPABILITIES = Capabilities(client_order_id=False, fetch_by_client_id=False, fills_by_order=True,
                            client_order_id_max_len=0)


class BitbankReader:
    def __init__(self, transport: BitbankTransport, *, symbols: Sequence[str], clock: Callable[[], float],
                 open_orders_count: int = 100, fills_count: int = 1000,
                 fee_rate_floor: Decimal = Decimal(0)) -> None:
        self._transport = transport
        self._symbols = tuple(symbols)
        self._clock = clock
        self._open_count, self._fills_count = int(open_orders_count), int(fills_count)
        self._floor = fee_rate_floor
        self._health = ReaderHealth(None, 0, None)
        self._by_pair = {parse.pair_id(s): s for s in self._symbols}
        self._rules: dict[str, MarketRules] = {}
        self._rates: dict[str, parse.FeeRates] = {}
        try:
            self.reload_rules()
        except ExchangeError as exc:
            raise ReaderUnavailable(f"cannot load market rules ({type(exc).__name__})") from None

    # -------------------------------------------------------------- plumbing
    def _get(self, endpoint: str, params: Mapping[str, object]) -> object:
        """Whatever the transport raises, a read comes out as Ambiguous, DataError, or (order lookup only)
        OrderNotFound.  A "rejection" of a read, or an exception nobody classified, is just "unknown"."""
        try:
            data = self._transport.get(endpoint, params)
        except (ExchangeAmbiguous, ExchangeDataError) as exc:
            error: ExchangeError = exc
        except OrderNotFound as exc:
            error = exc if endpoint == "order" else ExchangeAmbiguous(f"{endpoint}: not found")
        except ExchangeError:
            error = ExchangeAmbiguous(f"{endpoint}: unexpected exchange error")
        except Exception as exc:
            error = ExchangeAmbiguous(f"{endpoint}: unclassified {type(exc).__name__}")
        else:
            return data
        kind = getattr(error, "health_kind", "data" if isinstance(error, ExchangeDataError) else "network")
        self._health = ReaderHealth(kind, self._health.consecutive_failures + 1, self._health.last_ok_at)
        raise error

    def _parsed(self, fn, *args):
        try:
            return fn(*args)
        except ExchangeDataError:
            self._health = ReaderHealth("data", self._health.consecutive_failures + 1, self._health.last_ok_at)
            raise

    def _ok(self) -> None:
        """Called by a public read once its response is fully parsed and checked."""
        self._health = ReaderHealth(None, 0, self._clock())

    def health(self) -> ReaderHealth:
        return self._health

    def reload_rules(self) -> None:
        precisions = self._parsed(parse.asset_precisions, self._get("assets", {}))
        rules, rates = self._parsed(parse.market_rules_from, self._get("pairs", {}), precisions, self._symbols,
                                    self._floor)
        self._rules, self._rates = rules, rates
        self._ok()

    # -------------------------------------------------------------- ExchangeReader
    @property
    def capabilities(self) -> Capabilities:
        return CAPABILITIES

    def market_rules(self, symbol: str) -> MarketRules:
        try:
            return self._rules[symbol]
        except KeyError:
            raise ExchangeDataError("unknown symbol") from None

    def fetch_balance(self) -> BalanceSnapshot:
        result = self._parsed(parse.balance_from, self._get("assets", {}), self._clock())
        self._ok()
        return result

    def fetch_order_book(self, symbol: str, *, depth: int = 20) -> OrderBook:
        self.market_rules(symbol)
        result = self._parsed(parse.book_from, self._get("depth", {"pair": parse.pair_id(symbol)}), symbol, depth)
        self._ok()
        return result

    def fetch_order(self, symbol: str, exchange_order_id: str) -> OrderSnapshot:
        self.market_rules(symbol)
        data = self._get("order", {"pair": parse.pair_id(symbol), "order_id": str(exchange_order_id)})
        snap = self._parsed(parse.order_from, data, self._by_pair)
        if snap.symbol != symbol or snap.exchange_order_id != str(exchange_order_id):
            self._parsed(_mismatch, "order")
        self._ok()
        return snap

    def fetch_order_by_client_id(self, symbol: str, client_order_id: str) -> OrderSnapshot | None:
        raise ExchangeDataError("bitbank has no client order id")

    def fetch_open_orders(self, symbol: str) -> OrderPage:
        self.market_rules(symbol)
        data = self._get("active_orders", {"pair": parse.pair_id(symbol), "count": self._open_count})
        rows = self._parsed(_list, data, "orders")
        orders = tuple(self._parsed(parse.order_from, row, self._by_pair) for row in rows)
        if any(o.symbol != symbol for o in orders):
            self._parsed(_mismatch, "active_orders")
        self._ok()
        return OrderPage(orders=orders, complete=len(rows) < self._open_count)

    def fetch_fills(self, symbol: str, *, since_ms: int, exchange_order_id: str | None = None) -> FillPage:
        self.market_rules(symbol)
        params: dict[str, object] = {"pair": parse.pair_id(symbol), "count": self._fills_count, "order": "desc"}
        if exchange_order_id is not None:
            params["order_id"] = str(exchange_order_id)
        data = self._get("trade_history", params)
        rows = self._parsed(_list, data, "trades")
        # Rows older than the requested start are only dated, not parsed: an odd row in old history must not
        # make every read fail.  Rows inside the window are parsed strictly (a bad one fails the page).
        stamps = [self._parsed(parse.ms_value, parse._field(row, "executed_at", "executed_at"), "executed_at")
                  for row in rows]
        fills = [self._parsed(parse.fill_from, row, self._by_pair, self._rates)
                 for row, stamp in zip(rows, stamps) if stamp >= since_ms]
        if any(f.symbol != symbol for f in fills) or (
                exchange_order_id is not None and any(f.exchange_order_id != str(exchange_order_id) for f in fills)):
            self._parsed(_mismatch, "trade_history")
        oldest = min(stamps, default=None)
        complete = len(rows) < self._fills_count or (oldest is not None and oldest < since_ms)
        kept = sorted(fills, key=lambda f: (f.executed_at_ms, int(f.trade_id)))   # ids are digits (parse.id_text)
        self._ok()
        return FillPage(fills=tuple(kept), complete=complete)


def _list(data: object, key: str) -> list:
    rows = parse._field(data, key, key)
    if not isinstance(rows, list):
        raise parse._bad(key)
    return rows


def _mismatch(name: str):
    raise parse._bad(f"{name} mismatch")


__all__ = ["BitbankReader", "BitbankTransport", "ReaderUnavailable", "ReaderHealth", "CAPABILITIES", "OrderNotFound"]

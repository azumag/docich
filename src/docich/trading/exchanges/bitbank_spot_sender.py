"""The isolated pre-E bitbank spot order sender (Issue #3623).

This module is an adapter only.  The default production wiring never builds it.  The explicit
``live_worker run --spot-order-sender`` service role builds it together with the GET-only
reader from the one fixed order-capable key file (``bitbank_spot_session``), and
``live.executor.SENDER_KINDS_ALLOWED`` accepts this kind there; the existing ``live_control``
on+trade gate still decides whether a cycle may send.  The constructor receives the key and
secret; this module never reads a file, an environment variable or a CLI option.  There is no
withdrawal path.

Only two private spot endpoints exist, at the fixed HTTPS host: create and cancel.  Every call is
one fresh ``HTTPSConnection``, one POST, a bounded read and a close; there are no redirects, no
retries, no nonce and no path in the signature.  Anything after the request that is not an exact,
documented answer is ``ExchangeAmbiguous`` (never ``ExchangeRejected``), so the executor reads the
order back instead of sending it again.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from typing import Any

from ..live.canonical import canonical_json
from ..live.exchange import (CancelKind, CancelOutcome, ExchangeAmbiguous, ExchangeRejected, OrderRequest,
                             OrderSnapshot, OrderStatus, dec, dec_text)
from .bitbank_live import parse

KIND = "bitbank_spot"
API_HOST = "api.bitbank.cc"
ORDER_PATH = "/v1/user/spot/order"
CANCEL_PATH = "/v1/user/spot/cancel_order"
TIME_WINDOW_MS = 5000
MIN_INTERVAL_S = 0.2             # 0.2 s between call starts is at most five per second
DEFAULT_TIMEOUT_S = 10.0
MAX_TIMEOUT_S = 60.0
MAX_RESPONSE_BYTES = 64 * 1024

# Only bitbank's explicit spot-order validation errors may mean "nothing was created".  Everything
# else (50009, 70019, rate limits, 5xx, auth/clock errors, unknown codes) is ambiguous.
REJECT_CODES = frozenset({60001, 60003, 60004, 60005, 60006, 60011})

_SYMBOL = re.compile(r"^[A-Z0-9]+/[A-Z0-9]+$")
_ORDER_ID = re.compile(r"^[1-9][0-9]*$")
_SUBMIT_STATUSES = frozenset({OrderStatus.UNFILLED, OrderStatus.PARTIALLY_FILLED, OrderStatus.FULLY_FILLED,
                              OrderStatus.CANCELED_UNFILLED, OrderStatus.CANCELED_PARTIALLY_FILLED})
_CANCELED_STATUSES = frozenset({OrderStatus.CANCELED_UNFILLED, OrderStatus.CANCELED_PARTIALLY_FILLED})


def _ambiguous(reason: str) -> ExchangeAmbiguous:
    """A short, value-free reason: never the response body, headers, signature or key."""
    return ExchangeAmbiguous(f"bitbank spot: {reason}")


def _open_connection(host: str, timeout_s: float):
    """The module's only connection factory.  ``HTTPSConnection`` verifies TLS by default.

    The import is lazy so importing this adapter loads no network module and definitely opens no
    socket.  The host and path are constants, never arguments a caller supplies.
    """
    from http.client import HTTPSConnection
    return HTTPSConnection(host, timeout=timeout_s)


def _success(value: object) -> bool:
    """Exact JSON ``1`` or ``"1"``; ``True``, ``1.0`` and ``"01"`` are not success."""
    return (type(value) is int and value == 1) or (type(value) is str and value == "1")


def _failure(value: object) -> bool:
    """Exact JSON ``0`` or ``"0"``; ``False`` and ``0.0`` are not a documented failure envelope."""
    return (type(value) is int and value == 0) or (type(value) is str and value == "0")


def _failure_code(payload: Mapping[str, Any]) -> int | None:
    if not _failure(payload.get("success")):
        return None
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return None
    code = data.get("code")
    return code if type(code) is int else None


def _payload(body: bytes) -> dict[str, Any] | None:
    """Return a JSON object, or ``None`` so the caller raises outside the handler."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _positive_id(value: object) -> bool:
    """Canonical positive ASCII integer, with no artificial digit cap."""
    return isinstance(value, str) and _ORDER_ID.fullmatch(value) is not None


def _consistent(snapshot: OrderSnapshot) -> bool:
    """The status and the two quantities must describe one possible order."""
    amount, executed, remaining = snapshot.amount, snapshot.executed_amount, snapshot.remaining_amount
    if amount <= 0 or executed < 0 or remaining < 0 or executed + remaining != amount:
        return False
    if snapshot.status is OrderStatus.UNFILLED:
        return executed == 0 and remaining == amount
    if snapshot.status is OrderStatus.PARTIALLY_FILLED:
        return 0 < executed < amount and remaining > 0
    if snapshot.status is OrderStatus.FULLY_FILLED:
        return executed == amount and remaining == 0
    if snapshot.status is OrderStatus.CANCELED_UNFILLED:
        return executed == 0 and remaining == amount
    if snapshot.status is OrderStatus.CANCELED_PARTIALLY_FILLED:
        return 0 < executed < amount and remaining > 0
    return False


def _credential(value: object, name: str, *, ascii_only: bool) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        raise ValueError(f"{name} contains a control character")
    if ascii_only:
        try:
            value.encode("ascii")
        except UnicodeEncodeError:
            raise ValueError(f"{name} must be ASCII") from None
    return value


def _symbols(symbols: Sequence[str]) -> tuple[str, ...]:
    if isinstance(symbols, str):
        raise ValueError("symbols must be a sequence of BASE/QUOTE strings")
    try:
        items = tuple(symbols)
    except TypeError:
        raise ValueError("symbols must be a sequence of BASE/QUOTE strings") from None
    if not items or any(not isinstance(item, str) or not _SYMBOL.fullmatch(item) for item in items):
        raise ValueError("symbols must be non-empty upper-case BASE/QUOTE spot pairs")
    if len(set(items)) != len(items):
        raise ValueError("symbols must not contain duplicates")
    return items


def _timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("timeout_s must be a finite positive number")
    number = float(value)
    if not math.isfinite(number) or not 0 < number <= MAX_TIMEOUT_S:
        raise ValueError("timeout_s must be a finite positive number of at most 60 seconds")
    return number


def _positive(value: object, name: str) -> Decimal:
    try:
        number = dec(value)
    except ValueError:
        raise ValueError(f"{name} must be a finite positive decimal") from None
    if number <= 0:
        raise ValueError(f"{name} must be a finite positive decimal")
    return number


class BitbankSpotSender:
    """``OrderSender`` for bitbank spot limit orders; no other endpoint exists here."""

    kind = KIND

    def __init__(self, key: str, secret: str, symbols: Sequence[str], *, timeout_s: float = DEFAULT_TIMEOUT_S,
                 monotonic: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 wall_clock: Callable[[], float] = time.time) -> None:
        self._key = _credential(key, "key", ascii_only=True)
        self._secret = _credential(secret, "secret", ascii_only=False)
        self._symbols = _symbols(symbols)
        self._configured = frozenset(self._symbols)
        self._by_pair = {parse.pair_id(symbol): symbol for symbol in self._symbols}
        self._timeout_s = _timeout(timeout_s)
        for name, function in (("monotonic", monotonic), ("sleep", sleep), ("wall_clock", wall_clock)):
            if not callable(function):
                raise ValueError(f"{name} must be callable")
        self._monotonic, self._sleep, self._wall = monotonic, sleep, wall_clock
        self._last_call: float | None = None
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return "BitbankSpotSender(<redacted>)"

    __str__ = __repr__

    def __reduce__(self):
        raise TypeError("not picklable")

    # ------------------------------------------------------------------ public OrderSender interface
    def submit_order(self, req: OrderRequest) -> OrderSnapshot:
        values = self._submit_values(req)
        if values is None:
            raise ExchangeRejected("local_invalid", "local validation")
        symbol, side, amount, price = values
        payload = {"pair": parse.pair_id(symbol), "amount": dec_text(amount), "price": dec_text(price),
                   "side": side, "type": "limit"}
        status, body = self._post(ORDER_PATH, payload)
        return self._submit_snapshot(status, body, symbol, side, amount, price)

    def _submit_values(self, req: object) -> tuple[str, str, Decimal, Decimal] | None:
        """Validate before any I/O; ``None`` means this order was never sent."""
        if not isinstance(req, OrderRequest):
            return None
        try:
            symbol = self._symbol(req.symbol)
            side = req.side
            if side not in ("buy", "sell"):
                raise ValueError("side must be buy or sell")
            if req.order_type != "limit":
                raise ValueError("only spot limit orders are supported")
            if req.client_order_id is not None:
                raise ValueError("bitbank spot orders have no client order id")
            amount = _positive(req.amount, "amount")
            price = _positive(req.price, "price")
        except ValueError:
            return None
        return symbol, side, amount, price

    def cancel_order(self, symbol: str, exchange_order_id: str) -> CancelOutcome:
        pair = self._symbol(symbol)
        if not isinstance(exchange_order_id, str) or not _ORDER_ID.fullmatch(exchange_order_id):
            raise ValueError("exchange order id must be a positive ASCII integer")
        try:
            order_id = int(exchange_order_id)
        except ValueError:
            raise ValueError("exchange order id must be a positive ASCII integer") from None
        payload = {"pair": parse.pair_id(pair), "order_id": order_id}
        status, body = self._post(CANCEL_PATH, payload)
        return self._cancel_outcome(status, body, pair, exchange_order_id)

    # ------------------------------------------------------------------ validation
    def _symbol(self, value: object) -> str:
        if not isinstance(value, str) or value not in self._configured:
            raise ValueError("symbol is not a configured spot pair")
        return value

    # ------------------------------------------------------------------ transport (one POST per call)
    def _post(self, path: str, payload: Mapping[str, object]) -> tuple[int, bytes]:
        if path not in (ORDER_PATH, CANCEL_PATH):
            raise ValueError("unexpected path")
        with self._lock:
            outcome = self._exchange(path, payload)
        if isinstance(outcome, str):
            raise _ambiguous(outcome)
        status, raw = outcome
        if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
            raise _ambiguous("invalid response")
        return status, raw

    def _exchange(self, path: str, payload: Mapping[str, object]) -> str | tuple[int, bytes]:
        """One POST; return a sanitized reason on failure so the caller raises outside it."""
        try:
            self._pace()
        except Exception:
            return "pacing failed"
        try:
            body = canonical_json(payload)
            now_ms = int(self._wall() * 1000)
            if now_ms <= 0:
                raise ValueError("wall clock is not an epoch")
            headers = self._headers(now_ms, body)
        except Exception:
            return "signing failed"
        try:
            connection = _open_connection(API_HOST, self._timeout_s)
            try:
                connection.request("POST", path, body=body, headers=headers)
                response = connection.getresponse()
                try:
                    status = response.status
                    if type(status) is not int:
                        raise ValueError("HTTP status is not an integer")
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                    remaining = response.length
                finally:
                    response.close()
            finally:
                connection.close()
        except Exception:
            return "transport failed"
        if type(remaining) is int and remaining > 0:
            return "truncated response"
        return status, raw

    def _pace(self) -> None:
        """Serialise calls of this instance and leave at least 0.2 s between their starts."""
        now = float(self._monotonic())
        if not math.isfinite(now):
            raise ValueError("monotonic clock is not finite")
        if self._last_call is not None:
            delay = MIN_INTERVAL_S - (now - self._last_call)
            if delay > 0:
                self._sleep(delay)
                now = float(self._monotonic())
                if not math.isfinite(now):
                    raise ValueError("monotonic clock is not finite")
        self._last_call = now

    def _headers(self, now_ms: int, body: bytes) -> dict[str, str]:
        time_text = str(int(now_ms))
        window_text = str(TIME_WINDOW_MS)
        signature = hmac.new(self._secret.encode("utf-8"),
                             time_text.encode("ascii") + window_text.encode("ascii") + body,
                             hashlib.sha256).hexdigest()
        return {"Content-Type": "application/json", "ACCESS-KEY": self._key,
                "ACCESS-REQUEST-TIME": time_text, "ACCESS-TIME-WINDOW": window_text,
                "ACCESS-SIGNATURE": signature}

    # ------------------------------------------------------------------ response parsing
    def _submit_snapshot(self, status: int, body: bytes, symbol: str, side: str, amount: Decimal,
                         price: Decimal) -> OrderSnapshot:
        payload = _payload(body)
        if payload is None:
            raise _ambiguous("malformed response")
        if _success(payload.get("success")):
            if status != 200:
                raise _ambiguous("success envelope with an unexpected HTTP status")
            snapshot = self._snapshot(payload)
            if snapshot is None:
                raise _ambiguous("malformed order")
            if (snapshot.symbol != symbol or snapshot.side != side or snapshot.order_type != "limit"
                    or snapshot.amount != amount or snapshot.price != price
                    or not _positive_id(snapshot.exchange_order_id)):
                raise _ambiguous("order does not match the request")
            if snapshot.status not in _SUBMIT_STATUSES or not _consistent(snapshot):
                raise _ambiguous("unexpected order status or quantities")
            return snapshot
        code = _failure_code(payload)
        if status in (200, 400) and code in REJECT_CODES:
            raise ExchangeRejected(str(code), "order validation")
        raise _ambiguous("order not acknowledged")

    def _cancel_outcome(self, status: int, body: bytes, symbol: str,
                        exchange_order_id: str) -> CancelOutcome:
        payload = _payload(body)
        if payload is None:
            raise _ambiguous("malformed response")
        if not _success(payload.get("success")):
            raise _ambiguous("cancel not acknowledged")
        if status != 200:
            raise _ambiguous("success envelope with an unexpected HTTP status")
        snapshot = self._snapshot(payload)
        if snapshot is None:
            raise _ambiguous("malformed order")
        if (snapshot.symbol != symbol or snapshot.exchange_order_id != exchange_order_id
                or snapshot.order_type != "limit" or snapshot.price is None
                or not _positive_id(snapshot.exchange_order_id)):
            raise _ambiguous("cancel does not match the request")
        if not _consistent(snapshot):
            raise _ambiguous("impossible cancel quantities")
        if snapshot.status in _CANCELED_STATUSES:
            kind = CancelKind.CANCELED
        elif snapshot.status is OrderStatus.FULLY_FILLED:
            kind = CancelKind.ALREADY_CLOSED
        else:
            raise _ambiguous("cancel status is not terminal")
        return CancelOutcome(kind=kind, snapshot=snapshot)

    def _snapshot(self, payload: Mapping[str, Any]) -> OrderSnapshot | None:
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise _ambiguous("missing order data")
        position_side = data.get("position_side")
        if position_side is not None and position_side != "":
            raise _ambiguous("not a spot order")
        try:
            return parse.order_from(data, self._by_pair)
        except Exception:
            return None


__all__ = ["BitbankSpotSender", "KIND", "API_HOST", "ORDER_PATH", "CANCEL_PATH", "TIME_WINDOW_MS",
           "MIN_INTERVAL_S", "MAX_RESPONSE_BYTES", "REJECT_CODES"]

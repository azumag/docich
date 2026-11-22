"""``CcxtReadTransport``: ccxt as a signer and an HTTP carrier for a fixed set of GET endpoints.

This is the only module that touches ccxt, and it imports it lazily.  It never uses ccxt's unified
methods (they drop ``fee_amount_base`` and turn numbers into floats); it calls seven implicit GET
methods by their fixed names and parses the raw JSON itself (``parse_float=str``, so no float ever
exists).  Three layers keep it read-only:

1. the endpoint names come from a fixed table (nothing is built from arguments);
2. ``exchange.fetch`` is wrapped: any method other than GET, any body, or any URL outside the table
   raises ``ReadOnlyViolation`` before a byte leaves the process;
3. this class has no ``submit_order``/``cancel_order``/``kind`` (and the reader has none either).

Failures are raised as L2 exceptions (see ``errors``).  Keys never reach logs, messages or reprs.
"""
from __future__ import annotations

import calendar
import importlib
import json
import logging
import re
import time
from types import MappingProxyType
from typing import Callable, Final, Mapping
from urllib.parse import urlparse

from ...live.exchange import ExchangeAmbiguous, ExchangeDataError
from . import errors
from .credentials import Credentials

API_HOST: Final = "api.bitbank.cc"
PUBLIC_HOST: Final = "public.bitbank.cc"

ENDPOINTS: Final = MappingProxyType({            # logical name -> ccxt implicit GET method
    "pairs": "marketsGetSpotPairs",
    "depth": "publicGetPairDepth",
    "assets": "privateGetUserAssets",
    "order": "privateGetUserSpotOrder",
    "active_orders": "privateGetUserSpotActiveOrders",
    "trade_history": "privateGetUserSpotTradeHistory",
    "deposit_history": "privateGetUserDepositHistory",   # read by the deposit reconciler too (#4587)
})
CHECK_ONLY_ENDPOINTS: Final = MappingProxyType({  # reachability checks run by the owner's check script only
    "spot_status": "privateGetSpotStatus",
})
_ALLOWED_PATHS: Final = frozenset({
    (API_HOST, "/spot/pairs"), (API_HOST, "/v1/user/assets"), (API_HOST, "/v1/user/spot/order"),
    (API_HOST, "/v1/user/spot/active_orders"), (API_HOST, "/v1/user/spot/trade_history"),
    (API_HOST, "/v1/user/deposit_history"), (API_HOST, "/v1/spot/status")})
_DEPTH_PATH = re.compile(r"^/[a-z0-9]+_[a-z0-9]+/depth$")


class ReadOnlyViolation(BaseException):
    """Something tried to leave the read-only table.  A BaseException on purpose: nothing may swallow it."""


def url_allowed(url: object) -> bool:
    try:
        parsed = urlparse(str(url))
    except ValueError:
        return False
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        return False
    host, path = (parsed.hostname or "").lower(), parsed.path
    return (host, path) in _ALLOWED_PATHS or (host == PUBLIC_HOST and bool(_DEPTH_PATH.match(path)))


def _quiet_logger() -> logging.Logger:
    logger = logging.getLogger("docich.bitbank_live.ccxt")
    logger.setLevel(logging.WARNING)
    logger.propagate = False
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    return logger


_MONTHS = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov",
                                       "Dec"), start=1)}
_HTTP_DATE = re.compile(r"^[A-Za-z]{3}, (\d{2}) ([A-Za-z]{3}) (\d{4}) (\d{2}):(\d{2}):(\d{2}) GMT$")


def parse_http_date(text: object) -> float | None:
    """``Sun, 06 Nov 1994 08:49:37 GMT`` -> epoch seconds.  (email.utils would import ``socket``, which this
    package must never load.)"""
    match = _HTTP_DATE.match(str(text).strip()) if text else None
    if not match or match.group(2) not in _MONTHS:
        return None
    day, month, year, hour, minute, second = (int(match.group(1)), _MONTHS[match.group(2)], int(match.group(3)),
                                              int(match.group(4)), int(match.group(5)), int(match.group(6)))
    try:
        return float(calendar.timegm((year, month, day, hour, minute, second, 0, 0, 0)))
    except (ValueError, OverflowError):
        return None


def _json_loads(text):
    return json.loads(text, parse_float=str)


class CcxtReadTransport:
    def __init__(self, credentials: Credentials | None, *, timeout_ms: int = 10_000, time_window_ms: int = 10_000,
                 min_interval_s: float = 0.2, retry_pause_s: float = 1.0,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 wall_clock: Callable[[], float] = time.time,
                 exchange_factory: Callable[[dict], object] | None = None,
                 endpoints: Mapping[str, str] = ENDPOINTS) -> None:
        self._endpoints = dict(endpoints)
        self._min_interval, self._retry_pause = float(min_interval_s), float(retry_pause_s)
        self._clock, self._sleep, self._wall = clock, sleep, wall_clock
        self._clock_samples: list[tuple[float, float]] = []
        self._last_call: float | None = None
        config: dict = {"enableRateLimit": False, "timeout": int(timeout_ms), "verbose": False,
                        "options": {"authMethod": "timeWindow", "timeWindow": str(int(time_window_ms))}}
        if credentials is not None:
            key, secret = credentials.reveal()
            config["apiKey"], config["secret"] = key, secret
        factory = exchange_factory or _ccxt_factory
        self._exchange = factory(config)
        del config                                        # the key is held by the exchange object only
        self._harden(self._exchange)

    def __repr__(self) -> str:
        return "CcxtReadTransport(<redacted>)"

    __str__ = __repr__

    def __reduce__(self):
        raise TypeError("not picklable")

    # ------------------------------------------------------------------ hardening
    def _harden(self, exchange) -> None:
        exchange.verbose = False
        exchange.enableLastResponseHeaders = True       # the response ``Date`` is the only server clock bitbank gives
        exchange.logger = _quiet_logger()
        exchange.on_json_response = _json_loads          # numbers stay strings: no float ever exists
        real_fetch = exchange.fetch

        def guarded_fetch(url, method="GET", headers=None, body=None):
            if method != "GET" or body or not url_allowed(url):
                raise ReadOnlyViolation("only GET requests to the fixed bitbank read endpoints are allowed")
            return real_fetch(url, method, headers, body)

        exchange.fetch = guarded_fetch

    def _scrub(self) -> None:
        for name in ("last_http_response", "last_json_response", "last_request_headers", "last_request_body",
                     "last_response_headers"):
            if hasattr(self._exchange, name):
                setattr(self._exchange, name, None)

    def _pace(self) -> None:
        now = self._clock()
        if self._last_call is not None and now - self._last_call < self._min_interval:
            self._sleep(self._min_interval - (now - self._last_call))
        self._last_call = self._clock()

    # ------------------------------------------------------------------ the one entry point
    def get(self, endpoint: str, params: Mapping[str, object]) -> object:
        try:
            method_name = self._endpoints[endpoint]
        except KeyError:
            raise ReadOnlyViolation(f"unknown endpoint {endpoint!r}") from None
        for attempt in (1, 2):
            self._pace()
            error = self._call_once(endpoint, method_name, params)
            if not isinstance(error, Exception):
                return error                             # the data
            if attempt == 1 and getattr(error, "health_kind", None) == "rate":
                self._sleep(self._retry_pause)           # one pause and one more try, reads are idempotent
                continue
            raise error
        raise AssertionError("unreachable")

    def _call_once(self, endpoint: str, method_name: str, params: Mapping[str, object]):
        """The data on success, else an L2 exception *instance* (returned, so it carries no traceback of ccxt)."""
        self._scrub()
        method = getattr(self._exchange, method_name)
        started = self._wall()
        try:
            response = method(dict(params))
        except ReadOnlyViolation:
            raise
        except Exception as exc:
            self._note_server_date(started)
            body = getattr(self._exchange, "last_http_response", None)
            names = tuple(cls.__name__ for cls in type(exc).__mro__ if cls.__name__ not in ("object", "Exception",
                                                                                              "BaseException"))
            self._scrub()
            return errors.classify(endpoint=endpoint, body=body if isinstance(body, str) else None, exc_names=names)
        else:
            self._note_server_date(started)
        finally:
            self._scrub()
        return _unwrap(endpoint, response)

    def _note_server_date(self, started: float) -> None:
        """Record what one response's ``Date`` says about (local clock - bitbank clock).

        ``Date`` is the server's time when it built the response, floored to the second, so with the request sent at
        ``started`` and the answer received at ``ended`` (local clock) the true offset d satisfies
        ``started - Date - 1 <= d <= ended - Date``.  Intervals from several calls intersect into a tighter one."""
        headers = getattr(self._exchange, "last_response_headers", None)
        try:
            server = parse_http_date(headers.get("Date")) if headers is not None else None
        except (TypeError, ValueError, AttributeError):
            server = None
        if server is not None:
            self._clock_samples.append((started - server - 1.0, self._wall() - server))
            del self._clock_samples[:-32]

    def clock_samples(self) -> tuple[tuple[float, float], ...]:
        """(low, high) bounds in seconds for local minus bitbank, one pair per call that carried a Date header."""
        return tuple(self._clock_samples)

    def clock_bounds(self, last: int = 8) -> tuple[float, float] | None:
        """The intersection of the last ``last`` intervals: (lowest possible, highest possible) offset, seconds."""
        return intersect_bounds(self._clock_samples[-last:])


def intersect_bounds(samples) -> tuple[float, float] | None:
    samples = list(samples)
    if not samples:
        return None
    return max(lo for lo, _ in samples), min(hi for _, hi in samples)


def _unwrap(endpoint: str, response: object):
    if not isinstance(response, dict):
        return errors.classify(endpoint=endpoint, exc_names=("BadResponse",))
    if response.get("success") not in (1, "1"):
        data = response.get("data")
        code = data.get("code") if isinstance(data, dict) else None
        body = json.dumps({"success": 0, "data": {"code": code}}) if isinstance(code, int) else None
        return errors.classify(endpoint=endpoint, body=body, exc_names=("BadResponse",))
    data = response.get("data")
    if not isinstance(data, (dict, list)):
        return ExchangeDataError(f"{endpoint}: success without data")
    return data


def _ccxt_factory(config: dict):
    ccxt = importlib.import_module("ccxt")
    return ccxt.bitbank(config)


__all__ = ["CcxtReadTransport", "ENDPOINTS", "CHECK_ONLY_ENDPOINTS", "ReadOnlyViolation", "url_allowed",
           "ExchangeAmbiguous"]

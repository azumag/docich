"""Map what a failed bitbank/ccxt call looked like to the L2 exception classes.

The reader may raise only ``ExchangeAmbiguous`` (the default for everything), ``OrderNotFound`` (code
50009 on an order lookup) and ``ExchangeDataError`` (raised by the parser, never here).  It never raises
``ExchangeRejected``: the L2b callers do not catch it, and a read cannot "reject" anything.

Messages carry only endpoint, kind, HTTP status, bitbank code and the ccxt class name: never a body,
URL, header or key.
"""
from __future__ import annotations

import json

from ...live.exchange import ExchangeAmbiguous, ExchangeError, OrderNotFound

_CLOCK = {20004} | set(range(20033, 20040))
_AUTH = {20001, 20002, 20003, 20005} | set(range(20011, 20027))
_RATE = {10009}
_NETWORK = {10001, 10003, 10005, 10007, 10008, 70001, 70002, 70003, 70011, 70012}
_ACCOUNT = set(range(50003, 50009))
NOT_FOUND_CODE = 50009

_NAME_KIND = {
    "RateLimitExceeded": "rate", "InvalidNonce": "clock",
    "AuthenticationError": "auth", "PermissionDenied": "auth",
}


def kind_for_code(code: int, endpoint: str) -> str:
    if code in _CLOCK:
        return "clock"
    if code in _AUTH:
        return "auth"
    if code in _RATE:
        return "rate"
    if code in _NETWORK:
        return "network"
    if code in _ACCOUNT:
        return "account"
    return "request"


def kind_for_http(status: int | None) -> str | None:
    if status is None:
        return None
    if status == 429:
        return "rate"
    if status in (401, 403):
        return "auth"
    if status >= 500 or status in (408, 504):
        return "network"
    return None


def parse_error_body(body: str | None) -> int | None:
    """The bitbank error code of ``{"success":0,"data":{"code":N}}``, else None."""
    if not body:
        return None
    try:
        parsed = json.loads(body)
    except ValueError:
        return None
    if not isinstance(parsed, dict) or parsed.get("success") != 0:
        return None
    data = parsed.get("data")
    code = data.get("code") if isinstance(data, dict) else None
    return code if isinstance(code, int) and not isinstance(code, bool) else None


def classify(*, endpoint: str, http_status: int | None = None, body: str | None = None,
             exc_names: tuple[str, ...] = ()) -> ExchangeError:
    """``exc_names``: the class names in the ccxt exception's MRO (most specific first)."""
    code = parse_error_body(body)
    if code == NOT_FOUND_CODE and endpoint == "order":
        error: ExchangeError = OrderNotFound(f"{endpoint}: not found code={code}")
        error.health_kind = "request"   # type: ignore[attr-defined]
        return error
    kind = None
    if code is not None:
        kind = kind_for_code(code, endpoint)
    if kind is None:
        kind = kind_for_http(http_status)
    if kind is None:
        for name in exc_names:
            if name in _NAME_KIND:
                kind = _NAME_KIND[name]
                break
    if kind is None:
        kind = "network"
    cls = exc_names[0] if exc_names else "-"
    error = ExchangeAmbiguous(f"{endpoint}: {kind} http={http_status} code={code} cls={cls}")
    error.health_kind = kind   # type: ignore[attr-defined]
    return error

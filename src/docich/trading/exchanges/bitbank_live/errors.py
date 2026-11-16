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

# Code groups follow bitbank's errors.md.  Only 20034 (invalid ACCESS-REQUEST-TIME), 20035 (not sent within
# ACCESS-TIME-WINDOW) and 20037 (invalid ACCESS-REQUEST-TIME) are about time; 20004/20033/20036/20038/20039 are
# missing or malformed headers of ours (a "request" error), and 20018/20019 mean a missing /v1/ in the path.
_CLOCK = {20034, 20035, 20037}
_AUTH = {20001, 20002, 20003, 20005, 20011, 20014, 20023, 20024, 20025, 20026}
_RATE = {10009}
_NETWORK = {10001, 10003, 10005, 10007, 10008, 70001, 70002, 70003, 70011, 70012}
_ACCOUNT = {50003, 50004, 50005, 50006, 50008}
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
    if not isinstance(parsed, dict) or parsed.get("success") not in (0, "0"):   # ccxt's samples show both
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

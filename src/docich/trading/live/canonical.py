"""Canonical JSON, hashing and decimal text for LIVE digests."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation


class CanonicalError(ValueError):
    pass


def _reject_float(obj: object) -> None:
    if isinstance(obj, float):
        raise CanonicalError("floats are not allowed in canonical JSON")
    if isinstance(obj, dict):
        for key, value in obj.items():
            if not isinstance(key, str):
                raise CanonicalError("canonical JSON keys must be strings")
            _reject_float(value)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            _reject_float(value)


def canonical_json(obj: object) -> bytes:
    _reject_float(obj)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_sha256_hex(value: object) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(ch in "0123456789abcdef" for ch in value))


def decimal_text(value: object, *, name: str, allow_zero: bool = False,
                 allow_negative: bool = False) -> str:
    if isinstance(value, bool) or isinstance(value, float):
        raise CanonicalError(f"{name} must be a decimal string or int")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise CanonicalError(f"{name} is not a decimal") from exc
    if not number.is_finite():
        raise CanonicalError(f"{name} must be finite")
    if number < 0 and not allow_negative:
        raise CanonicalError(f"{name} must not be negative")
    if number == 0 and not allow_zero:
        raise CanonicalError(f"{name} must not be zero")
    return format(number, "f")

"""Bounded data contracts. Candidate Python is never imported by the host."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat

MAX_SOURCE = 256 * 1024
MAX_OUTPUT = 64 * 1024
MAX_INPUT = 1024 * 1024
IMAGE_RE = re.compile(r"(?:[a-zA-Z0-9._/:-]+@)?sha256:[0-9a-f]{64}\Z")
SYMBOL_RE = re.compile(r"[A-Z0-9]{1,16}/JPY\Z")
DECIMAL_RE = re.compile(r"(?:0|[1-9][0-9]{0,29})(?:\.[0-9]{1,18})?\Z")


class StrategyError(ValueError):
    """Contains a fixed diagnostic code, never generated source or API errors."""


def encode(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        raise StrategyError("invalid_json") from exc


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise StrategyError("duplicate_json_key")
        result[key] = value
    return result


def decode(raw: bytes, *, limit: int = MAX_OUTPUT) -> object:
    if not isinstance(raw, bytes) or len(raw) > limit:
        raise StrategyError("json_size_limit")
    try:
        value = json.loads(raw, object_pairs_hook=_pairs)
    except (ValueError, RecursionError, UnicodeError) as exc:
        raise StrategyError("invalid_json") from exc
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if depth > 16:
            raise StrategyError("json_depth_limit")
        if isinstance(item, float) and not math.isfinite(item):
            raise StrategyError("nonfinite_json")
        if isinstance(item, (list, dict)):
            stack.extend((child, depth + 1) for child in
                         (item.values() if isinstance(item, dict) else item))
    # Reject unpaired Unicode surrogates too, before persistence or re-encoding.
    encode(value)
    return value


def quantity(value: object) -> Decimal:
    if not isinstance(value, str) or not DECIMAL_RE.fullmatch(value):
        raise StrategyError("invalid_quantity")
    return Decimal(value)


def decimal_text(value: Decimal) -> str:
    return format(value, "f")


def read_source(path: Path) -> str:
    """Read one regular, singly-linked file without following a leaf symlink."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise StrategyError("source_not_regular")
            raw = handle.read(MAX_SOURCE + 1)
        return validate_source(raw.decode("utf-8"))
    except (OSError, UnicodeError) as exc:
        raise StrategyError("source_read_failed") from exc


def validate_source(source: object) -> str:
    if not isinstance(source, str) or not source.strip() or "\x00" in source:
        raise StrategyError("invalid_source")
    try:
        size = len(source.encode("utf-8"))
    except UnicodeError as exc:
        raise StrategyError("invalid_source") from exc
    if size > MAX_SOURCE:
        raise StrategyError("source_size_limit")
    return source


def bounded_text(value: object, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise StrategyError("invalid_metadata")
    if any(ord(char) < 32 for char in value):
        raise StrategyError("invalid_metadata")
    return value


@dataclass(frozen=True)
class Artifact:
    digest: str
    payload: dict

    @classmethod
    def create(cls, *, source: str, image: str, name: str, family: str,
               thesis: str, symbols: list[str], parameters: dict | None = None,
               initial_state: dict | None = None) -> Artifact:
        if not isinstance(image, str) or not IMAGE_RE.fullmatch(image):
            raise StrategyError("immutable_image_required")
        if (not isinstance(symbols, list) or not 1 <= len(symbols) <= 8
                or any(not isinstance(s, str) or not SYMBOL_RE.fullmatch(s) for s in symbols)
                or len(set(symbols)) != len(symbols)):
            raise StrategyError("invalid_symbols")
        payload = dict(schema_version=1, source=validate_source(source), image=image,
                       name=bounded_text(name, 80), family=bounded_text(family, 80),
                       thesis=bounded_text(thesis, 600), symbols=sorted(symbols),
                       parameters={} if parameters is None else parameters,
                       initial_state={} if initial_state is None else initial_state)
        for key in ("parameters", "initial_state"):
            if not isinstance(payload[key], dict):
                raise StrategyError("invalid_state")
            decode(encode(payload[key]), limit=16 * 1024)
        raw = encode(payload)
        return cls(hashlib.sha256(raw).hexdigest(), payload)


def validate_decision(raw: bytes, symbols: list[str]) -> dict:
    data = decode(raw)
    if not isinstance(data, dict) or set(data) != {"schema_version", "target_positions", "state", "reason"}:
        raise StrategyError("invalid_decision_keys")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise StrategyError("invalid_schema_version")
    if not isinstance(data["state"], dict):
        raise StrategyError("invalid_state")
    if not isinstance(data["reason"], str) or len(data["reason"]) > 400:
        raise StrategyError("invalid_reason")
    targets = data["target_positions"]
    if not isinstance(targets, list) or len(targets) > 32:
        raise StrategyError("invalid_targets")
    seen = set()
    for target in targets:
        if not isinstance(target, dict) or set(target) != {"symbol", "target_base_quantity"}:
            raise StrategyError("invalid_target_keys")
        symbol = target["symbol"]
        if not isinstance(symbol, str) or symbol not in symbols or symbol in seen:
            raise StrategyError("invalid_target_symbol")
        seen.add(symbol)
        quantity(target["target_base_quantity"])
    return data

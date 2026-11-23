"""Dependency-free wire contract for the market-data bridge.

A single envelope carries readiness and quotes under one generation, so a
consumer can never combine a fresh readiness with stale quotes (or vice
versa).  Every field crossing the Windows -> Linux boundary is validated here
before anything is published.  There is deliberately no order/execution type.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = 1
MARKETS = ("stocks", "fx")
EXPECTED_SOURCE = {"stocks": "kabu-station", "fx": "oanda-mt5-demo"}

# Fixed, non-secret reason codes.  Unknown upstream errors are mapped to one
# of these; raw broker errors, account numbers and credentials never cross.
REASON_CODES = frozenset({
    "ok",
    "warming_up",
    "preopen_empty_ranking",
    "token_expired",
    "adapter_hang",
    "adapter_timeout",
    "terminal_disconnected",
    "wrong_terminal",
    "upstream_unavailable",
    "upstream_error",
    "bridge_unreachable",
    "auth_missing",
    "auth_failed",
    "unknown_route",
    "method_not_allowed",
    "invalid_request",
    "wrong_market",
    "wrong_source",
    "wrong_symbol",
    "generation_conflict",
    "sequence_regressed",
    "invalid_sequence",
    "invalid_schema",
    "invalid_status",
    "invalid_reason",
    "invalid_quote",
    "stale_data",
    "future_timestamp",
    "lease_expired",
    "stale_host_epoch",
    "payload_too_large",
    "provider_unavailable",
})

STATUSES = frozenset({"ready", "warming_up", "unavailable"})
MARKET_STATES = frozenset({"preopen", "open", "closed", "unknown"})


def safe_reason(reason: object) -> str:
    """Map any internal error to the published fixed-code allowlist."""
    return reason if isinstance(reason, str) and reason in REASON_CODES else "upstream_error"

JP_SYMBOL = re.compile(r"JP\.[0-9A-Z]{4}\Z")
FX_SYMBOL = re.compile(r"[A-Z]{6}\Z")


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: float
    ask: float
    bid_size: float = 0.0
    ask_size: float = 0.0
    source_ts: float = 0.0   # original upstream observation time
    received_ts: float = 0.0  # Windows receive time (never replaces source_ts)
    sequence: int = 0


@dataclass(frozen=True)
class Candidate:
    symbol: str
    rank: int
    score: float = 0.0
    source_ts: float = 0.0


@dataclass(frozen=True)
class Snapshot:
    market: str
    source: str
    source_epoch: str      # Windows terminal/session epoch; changes on reconnect
    generation: int        # provider/bridge instance generation
    sequence: int          # monotonically increasing within a generation
    status: str            # ready | warming_up | unavailable
    reason: str
    market_state: str      # preopen | open | closed | unknown
    source_ts: float
    received_ts: float
    quotes: tuple[Quote, ...] = ()
    candidates: tuple[Candidate, ...] = ()


@dataclass(frozen=True)
class Envelope:
    schema_version: int
    snapshot: Snapshot
    published_at: float    # Linux wall-clock publish time
    lease_deadline: float  # wall-clock deadline; consumer-side stale guard
    host_epoch: str        # Linux boot id; disk state is not trusted across reboot
    publisher: str         # single publisher id
    generation: int        # duplicate of snapshot.generation for fast mismatch checks


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def symbol_ok(symbol: Any, market: str) -> bool:
    if not isinstance(symbol, str):
        return False
    pattern = JP_SYMBOL if market == "stocks" else FX_SYMBOL if market == "fx" else None
    return bool(pattern and pattern.fullmatch(symbol))


def _validate_quote(q: Any, *, market: str, now: float, max_source_age_s: float,
                    clock_skew_s: float) -> str | None:
    if not isinstance(q, Quote):
        return "invalid_schema"
    if not symbol_ok(q.symbol, market):
        return "wrong_symbol"
    if not (_finite(q.bid) and _finite(q.ask) and _finite(q.bid_size) and _finite(q.ask_size)):
        return "invalid_quote"
    if q.bid <= 0 or q.ask < q.bid or q.bid_size < 0 or q.ask_size < 0:
        return "invalid_quote"
    if not (_finite(q.source_ts) and _finite(q.received_ts)) or q.source_ts <= 0:
        return "invalid_quote"
    if q.received_ts < q.source_ts - clock_skew_s:
        return "invalid_quote"
    if q.source_ts > now + clock_skew_s:
        return "future_timestamp"
    if now - q.source_ts > max_source_age_s:
        return "stale_data"
    return None


def validate_snapshot(snap: Any, *, expected_market: str, expected_source: str,
                      source_epoch: str, generation: int, now: float,
                      max_source_age_s: float = 15.0, clock_skew_s: float = 2.0,
                      required_symbols: tuple[str, ...] = (),
                      max_quotes: int = 200, max_candidates: int = 100) -> tuple[bool, str]:
    """Fail-closed validation.  Returns (ok, reason_code)."""
    if not isinstance(snap, Snapshot):
        return False, "invalid_schema"
    if snap.market not in MARKETS or snap.market != expected_market:
        return False, "wrong_market"
    if snap.source != expected_source:
        return False, "wrong_source"
    if snap.source_epoch != source_epoch:
        return False, "generation_conflict"
    if not _positive_int(snap.generation) or snap.generation != generation:
        return False, "generation_conflict"
    if not _positive_int(snap.sequence):
        return False, "invalid_sequence"
    if snap.status not in STATUSES:
        return False, "invalid_status"
    if snap.reason not in REASON_CODES:
        return False, "invalid_reason"
    if snap.market_state not in MARKET_STATES:
        return False, "invalid_status"
    if not _finite(snap.source_ts) or not _finite(snap.received_ts):
        return False, "invalid_quote"
    if snap.source_ts < 0 or snap.received_ts < 0:
        return False, "invalid_quote"
    if len(snap.quotes) > max_quotes or len(snap.candidates) > max_candidates:
        return False, "payload_too_large"
    if not isinstance(snap.quotes, tuple) or not isinstance(snap.candidates, tuple):
        return False, "invalid_schema"

    for q in snap.quotes:
        reason = _validate_quote(q, market=snap.market, now=now,
                                 max_source_age_s=max_source_age_s, clock_skew_s=clock_skew_s)
        if reason:
            return False, reason
    for c in snap.candidates:
        if not isinstance(c, Candidate) or not symbol_ok(c.symbol, snap.market):
            return False, "wrong_symbol"
        if not _positive_int(c.rank) or not _finite(c.score):
            return False, "invalid_quote"

    if snap.status == "unavailable":
        return False, snap.reason
    if snap.status == "ready":
        by_symbol = {q.symbol: q for q in snap.quotes}
        if any(s not in by_symbol for s in required_symbols):
            return False, "invalid_quote"
        if snap.source_ts <= 0:
            return False, "invalid_quote"
        if snap.source_ts > now + clock_skew_s:
            return False, "future_timestamp"
        if now - snap.source_ts > max_source_age_s:
            return False, "stale_data"
    # warming_up is a valid structural state: empty ranking before the open is
    # not a connectivity error, but it is not tradeable either.
    return True, "ok"


def validate_envelope(enc: Any, *, expected_market: str, expected_source: str,
                      source_epoch: str, generation: int, host_epoch: str, now: float,
                      max_source_age_s: float = 15.0, max_lease_age_s: float = 5.0,
                      clock_skew_s: float = 2.0,
                      required_symbols: tuple[str, ...] = ()) -> tuple[bool, str]:
    if not isinstance(enc, Envelope):
        return False, "invalid_schema"
    if enc.schema_version != SCHEMA_VERSION:
        return False, "invalid_schema"
    if enc.host_epoch != host_epoch:
        return False, "stale_host_epoch"
    if enc.generation != generation or enc.snapshot.generation != generation:
        return False, "generation_conflict"
    if not (_finite(enc.published_at) and _finite(enc.lease_deadline)):
        return False, "invalid_schema"
    if enc.published_at > now + clock_skew_s:
        return False, "future_timestamp"
    if enc.lease_deadline <= enc.published_at:
        return False, "invalid_schema"
    if enc.lease_deadline - enc.published_at > max_lease_age_s + clock_skew_s:
        return False, "invalid_schema"
    if now >= enc.lease_deadline:
        return False, "lease_expired"
    ok, reason = validate_snapshot(
        enc.snapshot, expected_market=expected_market, expected_source=expected_source,
        source_epoch=source_epoch, generation=generation, now=now,
        max_source_age_s=max_source_age_s, clock_skew_s=clock_skew_s,
        required_symbols=required_symbols)
    return ok, reason


def is_tradeable(enc: Any, **kwargs) -> tuple[bool, str]:
    """Validation plus an explicit entry gate; warming_up is never tradeable."""
    ok, reason = validate_envelope(enc, **kwargs)
    if not ok:
        return False, reason
    if enc.snapshot.status != "ready":
        return False, enc.snapshot.reason
    return True, "ok"


def encode_quote(q: Quote) -> dict:
    return {"symbol": q.symbol, "bid": q.bid, "ask": q.ask,
            "bid_size": q.bid_size, "ask_size": q.ask_size,
            "source_ts": q.source_ts, "received_ts": q.received_ts,
            "sequence": q.sequence}


def decode_quote(data: Any) -> Quote:
    if not isinstance(data, dict) or not {"symbol", "bid", "ask", "source_ts"} <= data.keys():
        raise ValueError("invalid_schema")
    return Quote(
        symbol=data.get("symbol"), bid=data.get("bid"), ask=data.get("ask"),
        bid_size=data.get("bid_size", 0.0), ask_size=data.get("ask_size", 0.0),
        source_ts=data.get("source_ts", 0.0), received_ts=data.get("received_ts", 0.0),
        sequence=data.get("sequence", 0))


def encode_candidate(c: Candidate) -> dict:
    return {"symbol": c.symbol, "rank": c.rank, "score": c.score, "source_ts": c.source_ts}


def decode_candidate(data: Any) -> Candidate:
    if not isinstance(data, dict) or "symbol" not in data:
        raise ValueError("invalid_schema")
    return Candidate(symbol=data.get("symbol"), rank=data.get("rank", 0),
                     score=data.get("score", 0.0), source_ts=data.get("source_ts", 0.0))


def encode_snapshot(snap: Snapshot) -> dict:
    return {
        "market": snap.market, "source": snap.source, "source_epoch": snap.source_epoch,
        "generation": snap.generation, "sequence": snap.sequence, "status": snap.status,
        "reason": snap.reason, "market_state": snap.market_state,
        "source_ts": snap.source_ts, "received_ts": snap.received_ts,
        "quotes": [encode_quote(q) for q in snap.quotes],
        "candidates": [encode_candidate(c) for c in snap.candidates],
    }


def decode_snapshot(data: Any) -> Snapshot:
    required = {"market", "source", "generation", "sequence", "status", "reason",
                "market_state", "source_ts", "received_ts"}
    if not isinstance(data, dict) or not required <= data.keys():
        raise ValueError("invalid_schema")
    quotes = data.get("quotes", [])
    candidates = data.get("candidates", [])
    if not isinstance(quotes, list) or not isinstance(candidates, list):
        raise ValueError("invalid_schema")
    try:
        return Snapshot(
            market=data.get("market"), source=data.get("source"),
            source_epoch=data.get("source_epoch", ""), generation=data.get("generation", 0),
            sequence=data.get("sequence", 0), status=data.get("status", "unavailable"),
            reason=data.get("reason", "upstream_error"),
            market_state=data.get("market_state", "unknown"),
            source_ts=data.get("source_ts", 0.0), received_ts=data.get("received_ts", 0.0),
            quotes=tuple(decode_quote(q) for q in quotes),
            candidates=tuple(decode_candidate(c) for c in candidates))
    except (ValueError, TypeError):
        raise ValueError("invalid_schema")


def encode_envelope(enc: Envelope) -> dict:
    return {
        "schema_version": enc.schema_version, "snapshot": encode_snapshot(enc.snapshot),
        "published_at": enc.published_at, "lease_deadline": enc.lease_deadline,
        "host_epoch": enc.host_epoch, "publisher": enc.publisher,
        "generation": enc.generation,
    }


def decode_envelope(data: Any) -> Envelope:
    if not isinstance(data, dict) or not isinstance(data.get("snapshot"), dict):
        raise ValueError("invalid_schema")
    return Envelope(
        schema_version=data.get("schema_version", 0),
        snapshot=decode_snapshot(data["snapshot"]),
        published_at=data.get("published_at", 0.0),
        lease_deadline=data.get("lease_deadline", 0.0),
        host_epoch=data.get("host_epoch", ""), publisher=data.get("publisher", ""),
        generation=data.get("generation", 0))

"""Read-only adapter from an existing market quote record to MarketObservation (N02, #778).

The existing feed (``trading/markets/feeds.py``, ``Quote``) is read only; this
module never calls it, changes it, or opens a connection.  A quote becomes a
``MarketObservation`` only when every field the execution model needs is
present and valid.  Nothing is inferred: a quote without a source observation
id, ingest sequence, size semantics, calendar version or provenance class is
refused with ``PROVENANCE_INCOMPLETE``, and the refusal lists field names only.

Because the existing ``Quote`` carries none of these fields, its records are
refused today.  Making them eligible needs a separate provider change, which is
outside this approval.

Re-reading the same quote keeps its ``quote_digest`` and therefore its identity;
a re-read never gets a new source time.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Mapping

from .classification import digest
from .execution import ExecutionError, MarketObservation

ADAPTER_VERSION = "fixture-market-adapter-v1"
REQUIRED_FIELDS = (
    "observation_id", "source", "asset", "ingest_sequence", "market_event_at",
    "market_available_at", "available_at", "bid", "ask", "bid_size", "ask_size",
    "size_semantics", "tradeable", "realtime", "session_id", "calendar_version",
    "provenance_class", "instrument_constraints_version",
)
CODE_PROVENANCE_INCOMPLETE = "PROVENANCE_INCOMPLETE"
CODE_FIELD_INVALID = "FIELD_INVALID"


@dataclass(frozen=True)
class AdapterRejection:
    code: str
    missing: tuple[str, ...] = ()


def _as_utc(value: object) -> datetime | None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc)


def adapt_quote(record: Mapping[str, object]) -> MarketObservation | AdapterRejection:
    if not isinstance(record, Mapping):
        return AdapterRejection(CODE_FIELD_INVALID)
    missing = tuple(name for name in REQUIRED_FIELDS if name not in record)
    if missing:
        return AdapterRejection(CODE_PROVENANCE_INCOMPLETE, missing)
    try:
        for name in ("market_event_at", "market_available_at", "available_at"):
            if _as_utc(record[name]) is None:
                return AdapterRejection(CODE_FIELD_INVALID, (name,))
        quote_digest = digest({k: _jsonable(record[k]) for k in sorted(record)})
        return MarketObservation(
            observation_id=str(record["observation_id"]),
            source=str(record["source"]),
            asset=str(record["asset"]),
            ingest_sequence=record["ingest_sequence"],  # type: ignore[arg-type]
            market_event_at=_as_utc(record["market_event_at"]),  # type: ignore[arg-type]
            market_available_at=_as_utc(record["market_available_at"]),  # type: ignore[arg-type]
            available_at=_as_utc(record["available_at"]),  # type: ignore[arg-type]
            bid=Decimal(str(record["bid"])),
            ask=Decimal(str(record["ask"])),
            bid_size=Decimal(str(record["bid_size"])),
            ask_size=Decimal(str(record["ask_size"])),
            size_semantics=str(record["size_semantics"]),
            tradeable=record["tradeable"],  # type: ignore[arg-type]
            realtime=record["realtime"],  # type: ignore[arg-type]
            session_id=str(record["session_id"]),
            calendar_version=str(record["calendar_version"]),
            quote_digest=quote_digest,
            provenance_class=str(record["provenance_class"]),
            instrument_constraints_version=str(record["instrument_constraints_version"]),
        )
    except (ExecutionError, InvalidOperation, TypeError, ValueError):
        return AdapterRejection(CODE_FIELD_INVALID)


def _jsonable(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value

"""Persisted safety latch.  Tripping is always allowed; clearing is human-only."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum


class SafetyReason(str, Enum):
    UNKNOWN_ORDER = "unknown_order"
    BALANCE_MISMATCH = "balance_mismatch"
    DATA_ANOMALY = "data_anomaly"
    FEE_MISSING = "fee_missing"
    ENABLE_MISSING = "enable_missing"
    LEASE_LOST = "lease_lost"


@dataclass(frozen=True)
class SafetyTrip:
    trip_id: int
    reason: str
    detail: str
    tripped_at: float
    cleared_at: float | None = None
    cleared_by: str | None = None


@dataclass(frozen=True)
class SafetyState:
    trips: tuple[SafetyTrip, ...]
    unresolved_unknown_submissions: int
    ledger_readable: bool

    @property
    def block_new_buys(self) -> bool:
        return bool(self.trips) or self.unresolved_unknown_submissions > 0 or not self.ledger_readable

    @property
    def block_exit_orders(self) -> bool:
        # v1: while stopped, monitor and reconcile only (owner decision pending for fee/data cases).
        return self.block_new_buys


_UNKNOWN = "UNKNOWN_SUBMISSION"


def active_trips(cur: sqlite3.Cursor) -> tuple[SafetyTrip, ...]:
    rows = cur.execute("SELECT trip_id, reason, detail, tripped_at, cleared_at, cleared_by "
                       "FROM safety_trips WHERE cleared_at IS NULL ORDER BY trip_id").fetchall()
    return tuple(SafetyTrip(*row) for row in rows)


def unresolved_unknowns(cur: sqlite3.Cursor) -> int:
    return cur.execute("SELECT COUNT(*) FROM intents WHERE state=?", (_UNKNOWN,)).fetchone()[0]


def insert_trip(cur: sqlite3.Cursor, reason: SafetyReason, detail: str, now: float) -> bool:
    """Insert an active trip unless one with the same reason is active.  Returns inserted."""
    if cur.execute("SELECT 1 FROM safety_trips WHERE reason=? AND cleared_at IS NULL",
                   (reason.value,)).fetchone():
        return False
    cur.execute("INSERT INTO safety_trips(reason, detail, tripped_at) VALUES (?,?,?)",
                (reason.value, str(detail)[:500], float(now)))
    return True


def read_safety_state(ledger) -> SafetyState:
    """Never raises; an unreadable ledger reads as stopped."""
    try:
        with ledger.read_cursor() as cur:
            return SafetyState(active_trips(cur), unresolved_unknowns(cur), True)
    except Exception:
        return SafetyState((), 0, False)


def trip(ledger, reason: SafetyReason, *, detail: str, now: float) -> bool:
    """Trip the latch (no writer lease needed: stopping is always safe)."""
    return ledger.trip_safety(reason, detail=detail, now=now)


def clear_safety_trip(ledger, reason: SafetyReason, *, operator: str, note: str,
                      evidence: str, now: float) -> None:
    """HUMAN-ONLY.  Nothing in src/ may call this (a test enforces it)."""
    ledger.clear_safety(reason, operator=operator, note=note, evidence=evidence, now=now)


def note_enable_result(ledger, lease, result, *, now: float) -> None:
    """Record an enable-gate reading.  Trips ENABLE_MISSING only after the gate was once open."""
    ledger.record_enable_observation(lease, result, now=now)

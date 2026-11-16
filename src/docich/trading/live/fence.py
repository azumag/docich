"""Writer-lease checks for the executor (Issue #3623, L2).

``check_lease`` only reads.  Writes are already fenced by L1 (``_tx`` verifies the lease inside
every transaction); this closes the gap *between* writes, in particular right before something
is sent to the exchange.
"""
from __future__ import annotations

from .lease import LeaseLost, verify_in_tx
from .safety import SafetyReason


def check_lease(ledger, lease) -> None:
    """Raise LeaseLost unless this process still holds the lease (read-only)."""
    with ledger.read_cursor() as cur:
        verify_in_tx(cur, lease)


def on_lease_lost(ledger, *, detail: str, now: float) -> None:
    """Best effort: leave a trip behind (stopping needs no lease), never raise."""
    try:
        ledger.trip_safety(SafetyReason.LEASE_LOST, detail=detail, now=now)
    except Exception:
        pass


__all__ = ["LeaseLost", "check_lease", "on_lease_lost"]

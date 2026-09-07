"""Deterministic paper execution; no exchange or private API access."""
from __future__ import annotations

from .ledger import PaperLedger
from .models import AllocationDecision, PaperFill


class PaperBroker:
    """Persist an allocation decision as an immediate synthetic fill."""

    def __init__(self, ledger: PaperLedger):
        self.ledger = ledger

    def fill(self, decision: AllocationDecision, *, timestamp: float) -> PaperFill:
        return self.ledger.record_fill(decision, timestamp=timestamp)
